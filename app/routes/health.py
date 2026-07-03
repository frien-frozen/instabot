"""Health and status routes."""

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness probe for Cloud Run and load balancers."""
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env,
    )


@router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with basic service info."""
    return {
        "service": "Instabot",
        "description": "AI-powered Instagram comment auto-reply SaaS",
        "docs": "/docs",
    }
