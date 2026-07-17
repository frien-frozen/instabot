"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api import webhook_router
from app.config import get_settings
from app.database import close_db, get_session_factory, init_db
from app.dependencies import get_gemini_service, get_instagram_service
from app.gemini_config import DEFAULT_GEMINI_MODEL, is_gemini_ready, set_gemini_ready
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

    try:
        await init_db(settings)
        log_event(logger, logging.INFO, "mongodb_initialized", database=settings.mongodb_database)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "mongodb_init_failed",
            error=str(exc),
            hint=(
                "1) Atlas Network Access: allow 0.0.0.0/0 (or Render IPs). "
                "2) Set PYTHON_VERSION=3.12.0 in Render (not 3.14). "
                "3) MONGODB_URI must be mongodb+srv://... with correct password."
            ),
        )
        raise

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
    gemini.log_startup_diagnostics()
    validated = await gemini.validate_model()
    if validated is not None:
        set_gemini_ready(True)
        log_event(
            logger,
            logging.INFO,
            "gemini_ready",
            model=gemini.model,
            sdk_version=gemini.sdk_version,
            api_endpoint=gemini.api_endpoint,
        )
    else:
        set_gemini_ready(False)
        log_event(
            logger,
            logging.ERROR,
            "gemini_not_ready",
            configured_model=gemini.configured_model,
            model=gemini.model,
            sdk_version=gemini.sdk_version,
            api_endpoint=gemini.api_endpoint,
            hint=f"Fix GEMINI_API_KEY or set GEMINI_MODEL={DEFAULT_GEMINI_MODEL}",
        )

    _worker = EventWorker(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini=gemini,
        instagram=get_instagram_service(),
    )
    if is_gemini_ready():
        try:
            await _worker.start()
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "worker_start_failed",
                error=str(exc),
                hint="Check MONGODB_URI — app will keep running but events will not process",
            )
    else:
        log_event(logger, logging.WARNING, "worker_deferred_gemini_unavailable")

    _telegram = TelegramBotRunner(settings)
    try:
        await _telegram.start()
    except Exception as exc:
        log_event(logger, logging.WARNING, "telegram_start_failed", error=str(exc))

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
