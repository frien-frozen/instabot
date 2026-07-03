"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.database import close_db, get_engine
from app.middleware import RequestLoggingMiddleware
from app.routes import health_router, webhook_router
from app.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle hooks."""
    settings = get_settings()
    setup_logging(settings)

    # Warm up the database connection pool
    get_engine(settings)

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
