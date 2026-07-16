"""Database engine and session management."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
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
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# libpq query params that asyncpg does not accept in the connection URL
_ASYNCPG_UNSUPPORTED_QUERY_PARAMS = frozenset(
    {"sslmode", "sslcert", "sslkey", "sslrootcert", "channel_binding"}
)

_REVISION_PATTERN = re.compile(r'^revision:\s*str\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_UNKNOWN_REVISION_PATTERN = re.compile(
    r"Can't locate revision identified by ['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
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


def _run_alembic_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
    )


def _list_migration_revisions() -> list[str]:
    """Return revision IDs found in alembic/versions (sorted)."""
    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    revisions: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        match = _REVISION_PATTERN.search(path.read_text(encoding="utf-8"))
        if match:
            revisions.append(match.group(1))
    return sorted(revisions)


def _get_current_db_revision() -> str | None:
    result = _run_alembic_command("current")
    if result.returncode != 0:
        return None
    output = (result.stdout or "") + (result.stderr or "")
    available = set(_list_migration_revisions())
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("INFO") or line.startswith("Traceback"):
            continue
        token = line.split()[0]
        if token in available or (token.isalnum() and token not in {"(head)", "ERROR", "FAILED"}):
            return token
    return None


def _repair_stamp_target(unknown_revision: str, available: list[str]) -> str:
    """
    Pick a stamp target when alembic_version references a missing revision.

    Prefer the highest available revision that sorts before the unknown one;
    otherwise fall back to the latest revision in the repository.
    """
    if not available:
        raise RuntimeError("No Alembic revisions found in alembic/versions")

    prior = [rev for rev in available if rev < unknown_revision]
    if prior:
        return prior[-1]
    return available[-1]


def run_alembic_migrations() -> None:
    """
    Apply pending Alembic migrations synchronously.

    Called at startup so tables exist even if the platform start command
    skips start.sh. Safe to run on every boot (no-op when already at head).

    If the database references a revision missing from this deployment, stamp
    back to the nearest known revision and upgrade again. Failures are logged
    but do not crash the application.
    """
    available = _list_migration_revisions()
    head = available[-1] if available else None

    result = _run_alembic_command("upgrade", "head")
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)

    if result.returncode == 0:
        log_event(
            logger,
            logging.INFO,
            "database_migrations_applied",
            head=head,
            available_revisions=available,
        )
        return

    combined = (result.stdout or "") + (result.stderr or "")
    if "planLimitReached" in combined or "db.prisma.io" in combined:
        log_event(
            logger,
            logging.ERROR,
            "database_plan_limit_reached",
            error="Prisma/database account restriction (planLimitReached). Update DATABASE_URL to a working Postgres.",
            alembic_output=combined.strip()[-2000:],
        )
        return

    unknown_match = _UNKNOWN_REVISION_PATTERN.search(combined)
    current = _get_current_db_revision()

    if unknown_match or (current and current not in available):
        unknown = unknown_match.group(1) if unknown_match else current
        if not unknown or unknown.lower().startswith("traceback") or " " in unknown:
            log_event(
                logger,
                logging.ERROR,
                "database_migration_failed",
                current_revision=current,
                head=head,
                available_revisions=available,
                error=combined.strip()[-2000:],
            )
            return
        try:
            stamp_target = _repair_stamp_target(unknown or "", available)
        except RuntimeError as exc:
            log_event(
                logger,
                logging.ERROR,
                "database_migration_repair_failed",
                error=str(exc),
                current_revision=current,
                available_revisions=available,
                alembic_output=combined.strip(),
            )
            return

        log_event(
            logger,
            logging.WARNING,
            "database_migration_repair_stamp",
            unknown_revision=unknown,
            stamp_target=stamp_target,
            available_revisions=available,
        )

        stamp_result = _run_alembic_command("stamp", stamp_target)
        if stamp_result.stdout:
            print(stamp_result.stdout, flush=True)
        if stamp_result.stderr:
            print(stamp_result.stderr, flush=True)

        if stamp_result.returncode != 0:
            log_event(
                logger,
                logging.ERROR,
                "database_migration_stamp_failed",
                stamp_target=stamp_target,
                error=(stamp_result.stderr or stamp_result.stdout or "").strip(),
            )
            return

        retry = _run_alembic_command("upgrade", "head")
        if retry.stdout:
            print(retry.stdout, flush=True)
        if retry.stderr:
            print(retry.stderr, flush=True)

        if retry.returncode == 0:
            log_event(
                logger,
                logging.WARNING,
                "database_migration_repair_succeeded",
                stamp_target=stamp_target,
                head=head,
            )
            return

        log_event(
            logger,
            logging.ERROR,
            "database_migration_repair_upgrade_failed",
            stamp_target=stamp_target,
            error=(retry.stderr or retry.stdout or "").strip(),
        )
        return

    log_event(
        logger,
        logging.ERROR,
        "database_migration_failed",
        current_revision=current,
        head=head,
        available_revisions=available,
        error=combined.strip(),
    )


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
