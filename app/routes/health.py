"""Health and status routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import Response

from app.config import Settings, get_settings
from app.dependencies import get_instagram_service
from app.schemas import HealthResponse, InstagramHealthResponse
from app.services.instagram_service import InstagramAPIError, InstagramService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness probe for Cloud Run, Render, and load balancers."""
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env,
    )


@router.head("/health", include_in_schema=False)
async def health_check_head() -> Response:
    return Response(status_code=200)


@router.get("/health/instagram", response_model=InstagramHealthResponse)
async def instagram_health_check(
    settings: Settings = Depends(get_settings),
    instagram: InstagramService = Depends(get_instagram_service),
) -> InstagramHealthResponse:
    """Verify META_ACCESS_TOKEN against the Instagram Graph API."""
    try:
        profile = await instagram.validate_token()
        return InstagramHealthResponse(
            status="ok",
            graph_host=settings.meta_graph_host,
            username=profile.get("username"),
            user_id=str(profile.get("user_id") or profile.get("id") or ""),
        )
    except InstagramAPIError as exc:
        return InstagramHealthResponse(
            status="error",
            graph_host=settings.meta_graph_host,
            error=str(exc),
        )


@router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with basic service info."""
    return {
        "service": "Instabot",
        "description": "AI-powered Instagram comment auto-reply SaaS",
        "webhook": "/webhook",
        "health": "/health",
        "docs": "/docs",
    }


@router.head("/", include_in_schema=False)
async def root_head() -> Response:
    return Response(status_code=200)


@router.get("/favicon.ico", include_in_schema=False)
@router.head("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Silence browser favicon requests in logs."""
    return Response(status_code=204)
