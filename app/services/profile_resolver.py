"""Resolve dashboard-synced profiles for incoming Instagram events."""

from __future__ import annotations

import logging

from app.schemas.agent_config import AgentProfileConfig
from app.services.account_service import AccountService
from app.services.profile_config_store import ProfileConfigStore
from app.utils.agent_logging import agent_log

logger = logging.getLogger(__name__)


class ProfileResolver:
    """Map webhook account IDs to the latest in-memory profile configuration."""

    def __init__(
        self,
        store: ProfileConfigStore,
        account_service: AccountService,
    ) -> None:
        self._store = store
        self._accounts = account_service

    async def resolve(self, instagram_user_id: str) -> AgentProfileConfig | None:
        profile = await self._store.get_by_instagram_id(instagram_user_id)
        source = "sync_cache"

        if profile is None:
            account = await self._accounts.get_account_by_instagram_user_id(instagram_user_id)
            if account is None:
                cached = await self._store.all_profiles()
                agent_log(
                    logger,
                    "PROFILE",
                    logging.WARNING,
                    "no profile found for incoming event",
                    instagram_id=instagram_user_id,
                    cached_profile_count=len(cached),
                    cached_instagram_ids=[p.instagram_id for p in cached],
                )
                return None

            profile = AgentProfileConfig.from_account(account)
            await self._store.upsert(profile)
            source = "database_fallback"

        agent_log(
            logger,
            "PROFILE",
            logging.INFO,
            "resolved profile for event",
            source=source,
            account_id=profile.account_id,
            instagram_id=profile.instagram_id,
            username=profile.username,
            enabled=profile.enabled,
            reply_comments=profile.reply_comments,
            reply_messages=profile.reply_messages,
            reply_mentions=profile.reply_mentions,
            reply_story_mentions=profile.reply_story_mentions,
            delay_min=profile.delay_min,
            delay_max=profile.delay_max,
            ai_provider=profile.ai_provider,
            gemini_model=profile.gemini_model,
        )
        return profile
