"""Database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# libpq query params that asyncpg does not accept in the connection URL
_ASYNCPG_UNSUPPORTED_QUERY_PARAMS = frozenset(
    {"sslmode", "sslcert", "sslkey", "sslrootcert", "channel_binding"}
)


def _prepare_async_database_url(url: str) -> tuple[str, dict[str, Any]]:
    """
    Convert a PostgreSQL URL for asyncpg and extract SSL connect_args.

    asyncpg does not support libpq-style sslmode in the URL query string.
    Strip unsupported params and pass SSL settings via connect_args instead.
    """
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    connect_args: dict[str, Any] = {}
    sslmode = query.pop("sslmode", [None])[0]
    if sslmode in ("require", "verify-ca", "verify-full", "prefer"):
        connect_args["ssl"] = True
    elif sslmode == "disable":
        connect_args["ssl"] = False

    for param in _ASYNCPG_UNSUPPORTED_QUERY_PARAMS:
        query.pop(param, None)

    clean_query = urlencode(
        [(key, values[-1]) for key, values in query.items()],
        doseq=True,
    )
    clean_url = urlunparse(parsed._replace(query=clean_query))
    return clean_url, connect_args


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the shared async database engine."""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        database_url, connect_args = _prepare_async_database_url(cfg.database_url)
        _engine = create_async_engine(
            database_url,
            connect_args=connect_args,
            echo=cfg.debug,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(settings: Settings | None = None) -> None:
    """Create all tables (used in development; prefer Alembic in production)."""
    engine = get_engine(settings)
    async with engine.begin() as conn:
        from app.models import comment, setting  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the database engine on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
