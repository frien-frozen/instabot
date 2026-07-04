"""Load and bootstrap Instagram account configuration from PostgreSQL."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.instagram_account import InstagramAccount
from app.models.knowledge import Knowledge
from app.services.gemini_service import SYSTEM_PROMPT
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class AccountService:
    """Resolve account configuration for incoming webhook events."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def get_by_instagram_user_id(
        self,
        session: AsyncSession,
        instagram_user_id: str,
    ) -> InstagramAccount | None:
        result = await session.execute(
            select(InstagramAccount).where(
                InstagramAccount.instagram_user_id == instagram_user_id,
                InstagramAccount.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def resolve_account(self, instagram_user_id: str) -> InstagramAccount | None:
        """Load active account; bootstrap from env if missing."""
        async with self._session_factory() as session:
            account = await self.get_by_instagram_user_id(session, instagram_user_id)
            if account is None:
                account = await self._bootstrap_default_account(session)
                if account and account.instagram_user_id != instagram_user_id:
                    account = await self.get_by_instagram_user_id(session, instagram_user_id)
            return account

    async def bootstrap_default_account(self) -> InstagramAccount | None:
        async with self._session_factory() as session:
            return await self._bootstrap_default_account(session)

    async def _bootstrap_default_account(self, session: AsyncSession) -> InstagramAccount | None:
        ig_user_id = self._settings.resolved_instagram_user_id
        token = self._settings.meta_access_token.strip()
        if not ig_user_id or not token:
            log_event(logger, logging.WARNING, "account_bootstrap_skipped", reason="missing_env_credentials")
            return None

        existing = await session.execute(
            select(InstagramAccount).where(InstagramAccount.instagram_user_id == ig_user_id)
        )
        account = existing.scalar_one_or_none()
        if account:
            account.access_token = token
            account.graph_host = self._settings.meta_graph_host
            account.api_version = self._settings.meta_api_version
            if self._settings.gemini_model:
                account.gemini_model = self._settings.gemini_model
            account.reply_delay_min = self._settings.reply_delay_min_seconds
            account.reply_delay_max = self._settings.reply_delay_max_seconds
            await session.commit()
            return account

        account = InstagramAccount(
            instagram_user_id=ig_user_id,
            access_token=token,
            graph_host=self._settings.meta_graph_host,
            api_version=self._settings.meta_api_version,
            gemini_model=self._settings.gemini_model,
            system_prompt=SYSTEM_PROMPT.strip(),
            reply_delay_min=self._settings.reply_delay_min_seconds,
            reply_delay_max=self._settings.reply_delay_max_seconds,
            comments_enabled=True,
            messages_enabled=True,
            mentions_enabled=True,
            is_active=True,
        )
        session.add(account)
        await session.flush()

        session.add(
            Knowledge(
                account_id=account.id,
                category="about",
                title="About",
                content="Configure this knowledge entry in the database.",
                sort_order=0,
            )
        )
        await session.commit()
        await session.refresh(account)

        log_event(
            logger,
            logging.INFO,
            "account_bootstrapped",
            instagram_user_id=ig_user_id,
            account_id=account.id,
        )
        return account
