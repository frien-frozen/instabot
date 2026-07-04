"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.database import close_db, get_engine
from app.middleware import RequestLoggingMiddleware
from app.routes import health_router, webhook_router
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_event, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle hooks."""
    settings = get_settings()
    setup_logging(settings)

    get_engine(settings)

    # Validate Instagram token at startup — surfaces code 190 immediately in logs
    try:
        ig = InstagramService(settings)
        profile = await ig.validate_token()
        log_event(
            logger,
            logging.INFO,
            "instagram_token_valid",
            graph_host=settings.meta_graph_host,
            user_id=profile.get("user_id") or profile.get("id"),
            username=profile.get("username"),
        )
    except InstagramAPIError as exc:
        log_event(
            logger,
            logging.ERROR,
            "instagram_token_invalid",
            graph_host=settings.meta_graph_host,
            error_code=exc.error_code,
            error_detail=str(exc),
            hint="Regenerate token in Meta dashboard → Generate token, then update META_ACCESS_TOKEN on Render",
        )

    yield

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
    app.include_router(webhook_router)

    return app


app = create_app()
