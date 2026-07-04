"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.database import close_db, get_engine, get_session_factory, run_alembic_migrations
from app.dependencies import get_account_service, get_config_sync_service
from app.middleware import RequestLoggingMiddleware
from app.routes import agent_router, api_router, health_router, webhook_router
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.agent_logging import agent_log
from app.utils.logging import get_logger, log_event, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle hooks."""
    settings = get_settings()
    setup_logging(settings)

    try:
        run_alembic_migrations()
        log_event(logger, logging.INFO, "database_migrations_applied")
    except RuntimeError as exc:
        log_event(logger, logging.ERROR, "database_migration_failed", error=str(exc))
        raise

    get_engine(settings)

    account_service = get_account_service()
    await account_service.bootstrap_default_account()

    config_sync = get_config_sync_service()
    synced = await config_sync.sync_once()
    agent_log(
        logger,
        "SYNC",
        logging.INFO,
        "initial configuration sync completed",
        profile_count=synced,
        interval_seconds=settings.agent_config_sync_interval_seconds,
    )
    config_sync.start()

    try:
        gemini = GeminiService(settings)
        test_reply = await gemini.validate_model()
        log_event(
            logger,
            logging.INFO,
            "gemini_model_valid",
            model=gemini.model,
            configured_model=gemini.configured_model,
            test_reply=test_reply,
        )
    except GeminiAPIError as exc:
        log_event(
            logger,
            logging.ERROR,
            "gemini_model_invalid",
            model=exc.model,
            error=str(exc),
            hint="Set GEMINI_MODEL=gemini-2.5-flash in Render environment variables",
        )

    yield

    await config_sync.stop()
    await close_db()


def create_app() -> FastAPI:
    """Application factory — supports testing and future multi-tenant config."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="AI-powered Instagram comment auto-reply SaaS",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(agent_router)
    app.include_router(webhook_router)

    return app


app = create_app()
