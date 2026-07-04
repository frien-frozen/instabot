"""Agent configuration API routes for dashboard synchronization."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_account_service
from app.schemas import AgentConfigListResponse, AgentConfigResponse
from app.schemas.agent_config import AgentProfileConfig
from app.services.account_service import AccountService
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _to_response(profile: AgentProfileConfig) -> AgentConfigResponse:
    return AgentConfigResponse(
        account_id=profile.account_id,
        instagram_id=profile.instagram_id,
        username=profile.username,
        system_prompt=profile.system_prompt,
        reply_comments=profile.reply_comments,
        reply_messages=profile.reply_messages,
        reply_mentions=profile.reply_mentions,
        reply_story_mentions=profile.reply_story_mentions,
        commentReplyEnabled=profile.reply_comments,
        messageReplyEnabled=profile.reply_messages,
        mentionReplyEnabled=profile.reply_mentions,
        storyMentionReplyEnabled=profile.reply_story_mentions,
        delay_min=profile.delay_min,
        delay_max=profile.delay_max,
        language_mode=profile.language_mode,
        enabled=profile.enabled,
        ai_provider=profile.ai_provider,
        gemini_model=profile.gemini_model,
        graph_host=profile.graph_host,
        api_version=profile.api_version,
        access_token=profile.access_token,
    )


@router.get("/config", response_model=AgentConfigListResponse)
async def list_agent_config(
    account_service: AccountService = Depends(get_account_service),
) -> AgentConfigListResponse:
    """Return all active profile configurations for agent synchronization."""
    profiles = await account_service.list_agent_profiles()
    log_event(logger, logging.INFO, "agent_config_list", profile_count=len(profiles))
    return AgentConfigListResponse(profiles=[_to_response(profile) for profile in profiles])


@router.get("/config/{username}", response_model=AgentConfigResponse)
async def get_agent_config_by_username(
    username: str,
    account_service: AccountService = Depends(get_account_service),
) -> AgentConfigResponse:
    """Return one profile configuration keyed by Instagram username."""
    account = await account_service.get_by_username(username)
    if account is None:
        raise HTTPException(status_code=404, detail=f"No active profile found for username '{username}'.")

    profile = AgentProfileConfig.from_account(account)
    log_event(
        logger,
        logging.INFO,
        "agent_config_loaded",
        username=profile.username,
        instagram_id=profile.instagram_id,
        enabled=profile.enabled,
    )
    return _to_response(profile)
