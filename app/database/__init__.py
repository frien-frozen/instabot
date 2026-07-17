"""MongoDB connection, Beanie init, and session compatibility shim."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.config import Settings, get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None
_initialized = False


class MongoSession:
    """
    Compatibility shim so existing `async with factory() as session` / `commit()`
    call sites keep working. Beanie persists on insert/save; commit is a no-op.
    """

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None


SessionFactory = Callable[[], Any]


def get_client(settings: Settings | None = None) -> AsyncIOMotorClient:
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = AsyncIOMotorClient(cfg.mongodb_uri)
    return _client


def get_db(settings: Settings | None = None) -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        cfg = settings or get_settings()
        _db = get_client(cfg)[cfg.mongodb_database]
    return _db


async def next_id(sequence: str) -> int:
    """Atomic autoincrement for integer document ids (compatible with prior SQL ids)."""
    db = get_db()
    result = await db["counters"].find_one_and_update(
        {"_id": sequence},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


async def init_db(settings: Settings | None = None) -> None:
    """Initialize Motor client and Beanie document models."""
    global _initialized
    cfg = settings or get_settings()
    db = get_db(cfg)

    from app.models.comment import Comment
    from app.models.conversation import Conversation
    from app.models.event import Event
    from app.models.message import Message
    from app.models.pending_reply import PendingReply
    from app.models.processed_webhook import ProcessedWebhook
    from app.models.setting import Setting
    from app.models.task import Task

    await init_beanie(
        database=db,
        document_models=[
            Comment,
            Conversation,
            Event,
            Message,
            PendingReply,
            ProcessedWebhook,
            Setting,
            Task,
        ],
    )
    _initialized = True


def get_session_factory(settings: Settings | None = None) -> SessionFactory:
    """Return a callable that opens a MongoSession context manager."""
    _ = settings  # settings reserved for future per-tenant wiring

    @asynccontextmanager
    async def factory() -> AsyncGenerator[MongoSession, None]:
        yield MongoSession()

    return factory  # type: ignore[return-value]


async def get_db_session() -> AsyncGenerator[MongoSession, None]:
    """FastAPI dependency that yields a MongoSession."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Close the Motor client on shutdown."""
    global _client, _db, _initialized
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        _initialized = False


# Backward-compatible aliases used by older imports (no-ops / Mongo equivalents).
def get_engine(settings: Settings | None = None) -> AsyncIOMotorClient:
    return get_client(settings)


def run_alembic_migrations() -> None:
    """No-op: Alembic removed after Mongo migration."""
    return None
