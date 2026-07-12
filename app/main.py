"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api import webhook_router
from app.config import get_settings
from app.database import close_db, get_engine, get_session_factory, run_alembic_migrations
from app.dependencies import get_gemini_service, get_instagram_service
from app.middleware import RequestLoggingMiddleware
from app.routes import health_router
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.telegram import TelegramBotRunner
from app.utils.logging import get_logger, log_event, setup_logging
from app.workers import EventWorker

logger = get_logger(__name__)

_worker: EventWorker | None = None
_telegram: TelegramBotRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _worker, _telegram

    settings = get_settings()
    setup_logging(settings)

    run_alembic_migrations()
    get_engine(settings)

    try:
        ig = InstagramService(settings)
        profile = await ig.validate_token()
        log_event(
            logger,
            logging.INFO,
            "instagram_token_valid",
            user_id=profile.get("user_id") or profile.get("id"),
            username=profile.get("username"),
        )
    except InstagramAPIError as exc:
        log_event(logger, logging.WARNING, "instagram_token_invalid", error=str(exc))

    gemini = get_gemini_service()
    validated = await gemini.validate_model()
    if validated is not None:
        log_event(logger, logging.INFO, "gemini_model_valid", model=gemini.model)
    else:
        log_event(
            logger,
            logging.WARNING,
            "gemini_model_unavailable",
            configured_model=gemini.configured_model,
            active_model=gemini.model,
            hint="Replies may fail until a working GEMINI_MODEL is configured",
        )

    _worker = EventWorker(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini=get_gemini_service(),
        instagram=get_instagram_service(),
    )
    await _worker.start()

    _telegram = TelegramBotRunner(settings)
    await _telegram.start()

    yield

    if _telegram:
        await _telegram.stop()
    if _worker:
        await _worker.stop()
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Instagram Automation Platform",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health_router)
    app.include_router(webhook_router)
    return app


app = create_app()
