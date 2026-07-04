"""Dashboard API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.dependencies import get_account_service
from app.models.instagram_account import InstagramAccount
from app.schemas import ImportExistingAccountResponse
from app.services.account_service import AccountService
from app.services.gemini_service import SYSTEM_PROMPT
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])

INVALID_TOKEN_ERROR_CODES = frozenset({190, 102, 463})
UNAVAILABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _resolve_access_token(account: InstagramAccount | None, settings: Settings) -> str:
    if account is not None and account.access_token.strip():
        return account.access_token.strip()
    return settings.meta_access_token.strip()


def _build_instagram_client(
    settings: Settings,
    account: InstagramAccount | None,
) -> InstagramService:
    if account is not None:
        return InstagramService.for_account(settings, account)
    return InstagramService(settings)


def _map_instagram_error(exc: InstagramAPIError) -> HTTPException:
    if exc.error_code in INVALID_TOKEN_ERROR_CODES or exc.status_code in {401, 403}:
        return HTTPException(
            status_code=401,
            detail="Instagram access token is invalid or expired.",
        )

    if exc.status_code in UNAVAILABLE_STATUS_CODES or exc.status_code is None:
        return HTTPException(
            status_code=503,
            detail="Instagram Graph API is temporarily unavailable.",
        )

    return HTTPException(
        status_code=502,
        detail="Failed to retrieve Instagram account profile.",
    )


def _build_import_response(
    profile: dict,
    account: InstagramAccount | None,
    settings: Settings,
) -> ImportExistingAccountResponse:
    instagram_id = str(profile.get("user_id") or profile.get("id") or "")
    if not instagram_id and account is not None:
        instagram_id = account.instagram_user_id

    username = str(profile.get("username") or (account.username if account else "") or "")
    name = str(profile.get("name") or "")
    profile_picture = str(profile.get("profile_picture_url") or "")

    if account is not None:
        return ImportExistingAccountResponse(
            instagram_id=instagram_id,
            username=username,
            name=name,
            profile_picture=profile_picture,
            system_prompt=account.system_prompt,
            reply_comments=account.comments_enabled,
            reply_messages=account.messages_enabled,
            reply_mentions=account.mentions_enabled,
            reply_story_mentions=account.mentions_enabled,
            delay_min=account.reply_delay_min,
            delay_max=account.reply_delay_max,
            language_mode="auto",
            enabled=account.is_active,
        )

    return ImportExistingAccountResponse(
        instagram_id=instagram_id,
        username=username,
        name=name,
        profile_picture=profile_picture,
        system_prompt=SYSTEM_PROMPT.strip(),
        reply_comments=True,
        reply_messages=True,
        reply_mentions=True,
        reply_story_mentions=True,
        delay_min=settings.reply_delay_min_seconds,
        delay_max=settings.reply_delay_max_seconds,
        language_mode="auto",
        enabled=True,
    )


@router.get("/import-existing-account", response_model=ImportExistingAccountResponse)
async def import_existing_account(
    settings: Settings = Depends(get_settings),
    account_service: AccountService = Depends(get_account_service),
) -> ImportExistingAccountResponse:
    """
    Import an already-connected Instagram account into the Dashboard.

    Validates the stored access token, fetches live profile data from Instagram,
    and returns the backend's current automation configuration.
    """
    account = await account_service.get_default_account()
    access_token = _resolve_access_token(account, settings)

    if not access_token:
        log_event(logger, logging.WARNING, "import_existing_account_missing")
        raise HTTPException(
            status_code=404,
            detail="No authenticated Instagram account exists.",
        )

    instagram = _build_instagram_client(settings, account)

    try:
        profile = await instagram.fetch_account_profile()
    except InstagramAPIError as exc:
        log_event(
            logger,
            logging.ERROR,
            "import_existing_account_failed",
            error_code=exc.error_code,
            status_code=exc.status_code,
            error_detail=str(exc),
        )
        raise _map_instagram_error(exc) from exc

    response = _build_import_response(profile, account, settings)

    log_event(
        logger,
        logging.INFO,
        "import_existing_account_success",
        instagram_id=response.instagram_id,
        username=response.username,
        has_profile_picture=bool(response.profile_picture),
    )
    return response
