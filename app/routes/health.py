"""Health and status routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.config import Settings, get_settings
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
async def health_check(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HealthResponse | Response:
    """Liveness probe for Cloud Run, Render, and load balancers."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env,
    )


@router.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request) -> dict[str, str] | Response:
    """Root endpoint with basic service info."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return {
        "service": "Instabot",
        "description": "AI-powered Instagram comment auto-reply SaaS",
        "webhook": "/webhook",
        "health": "/health",
        "docs": "/docs",
    }


@router.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon(request: Request) -> Response:
    """Silence browser favicon requests in logs."""
    return Response(status_code=204)
