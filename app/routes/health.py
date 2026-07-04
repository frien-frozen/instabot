"""Health and status routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import Response

from app.config import Settings, get_settings
from app.dependencies import get_gemini_service, get_instagram_service
from app.schemas import (
    GeminiHealthResponse,
    HealthResponse,
    InstagramHealthResponse,
    MessagesHealthResponse,
)
from app.services.gemini_service import (
    DEFAULT_GEMINI_MODEL,
    RECOMMENDED_GEMINI_MODELS,
    GeminiAPIError,
    GeminiService,
)
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


@router.get("/health/messages", response_model=MessagesHealthResponse)
async def messages_health_check(
    settings: Settings = Depends(get_settings),
    instagram: InstagramService = Depends(get_instagram_service),
) -> MessagesHealthResponse:
    """Verify Instagram messaging webhook readiness and token validity."""
    messaging_enabled = True  # App subscribes to messaging via Meta dashboard
    try:
        profile = await instagram.validate_token()
        user_id = str(profile.get("user_id") or profile.get("id") or "")
        username = profile.get("username")
        return MessagesHealthResponse(
            status="ok",
            graph_host=settings.meta_graph_host,
            messaging_webhook_enabled=messaging_enabled,
            access_token_valid=True,
            authenticated_user_id=user_id,
            username=username,
            permissions_note=(
                "Ensure 'messages' webhook field is subscribed in Meta dashboard "
                "and token has instagram_business_manage_messages permission."
            ),
        )
    except InstagramAPIError as exc:
        return MessagesHealthResponse(
            status="error",
            graph_host=settings.meta_graph_host,
            messaging_webhook_enabled=messaging_enabled,
            access_token_valid=False,
            error=str(exc),
        )


@router.get("/health/gemini", response_model=GeminiHealthResponse)
async def gemini_health_check(
    settings: Settings = Depends(get_settings),
    gemini: GeminiService = Depends(get_gemini_service),
) -> GeminiHealthResponse:
    """Verify GEMINI_API_KEY and GEMINI_MODEL with a live test prompt."""
    recommended = sorted(RECOMMENDED_GEMINI_MODELS)
    try:
        test_reply = await gemini.validate_model()
        return GeminiHealthResponse(
            status="ok",
            model=gemini.model,
            test_reply=test_reply,
            recommended_models=recommended,
            hint=(
                f"Using {gemini.model}"
                + (
                    f" (auto-corrected from {gemini.configured_model!r})"
                    if gemini.configured_model != gemini.model
                    else ""
                )
            ),
        )
    except GeminiAPIError as exc:
        return GeminiHealthResponse(
            status="error",
            model=gemini.model,
            recommended_models=recommended,
            error=str(exc),
            hint="Set GEMINI_MODEL=gemini-2.5-flash in Render environment variables",
        )


@router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with basic service info."""
    return {
        "service": "Instabot",
        "description": "AI-powered Instagram comment auto-reply SaaS",
        "webhook": "/webhook",
        "health": "/health",
        "health_instagram": "/health/instagram",
        "health_messages": "/health/messages",
        "health_gemini": "/health/gemini",
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
