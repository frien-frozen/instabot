"""Synchronize agent profile configuration from the dashboard API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.schemas.agent_config import AgentProfileConfig
from app.services.account_service import AccountService
from app.services.profile_config_store import ProfileConfigStore
from app.utils.agent_logging import agent_log

logger = logging.getLogger(__name__)


class ConfigSyncService:
    """Poll the dashboard API and refresh the in-memory profile cache."""

    def __init__(
        self,
        settings: Settings,
        store: ProfileConfigStore,
        account_service: AccountService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._accounts = account_service
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_loop(), name="config-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def sync_once(self) -> int:
        try:
            profiles = await self._fetch_profiles()
            await self._store.replace_all(profiles, sync_ok=True)
            for profile in profiles:
                agent_log(
                    logger,
                    "SYNC",
                    logging.INFO,
                    "profile loaded into cache",
                    account_id=profile.account_id,
                    instagram_id=profile.instagram_id,
                    username=profile.username,
                    enabled=profile.enabled,
                    reply_comments=profile.reply_comments,
                    reply_messages=profile.reply_messages,
                    reply_mentions=profile.reply_mentions,
                    reply_story_mentions=profile.reply_story_mentions,
                )
            agent_log(
                logger,
                "SYNC",
                logging.INFO,
                "configuration refreshed",
                profile_count=len(profiles),
                usernames=[profile.username for profile in profiles if profile.username],
            )
            return len(profiles)
        except Exception as exc:
            agent_log(
                logger,
                "SYNC",
                logging.WARNING,
                "sync failed, keeping last successful configuration",
                error=str(exc),
                cached_profiles=await self._store.profile_count(),
            )
            return await self._store.profile_count()

    async def _run_loop(self) -> None:
        interval = self._settings.agent_config_sync_interval_seconds
        while not self._stop.is_set():
            await self.sync_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _fetch_profiles(self) -> list[AgentProfileConfig]:
        base_url = self._settings.dashboard_api_base_url.strip()
        if base_url:
            try:
                return await self._fetch_via_http(base_url)
            except Exception as exc:
                agent_log(
                    logger,
                    "SYNC",
                    logging.WARNING,
                    "dashboard HTTP sync failed, falling back to database",
                    error=str(exc),
                )
                return await self._accounts.list_agent_profiles()
        return await self._accounts.list_agent_profiles()

    def _build_auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        api_key = self._settings.resolved_dashboard_api_key.strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        return headers

    async def _fetch_via_http(self, base_url: str) -> list[AgentProfileConfig]:
        url = f"{base_url.rstrip('/')}/api/agent/config"
        timeout = self._settings.http_timeout_seconds
        headers = self._build_auth_headers()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        items: list[Any]
        if isinstance(payload, dict) and "profiles" in payload:
            items = payload["profiles"]
        elif isinstance(payload, list):
            items = payload
        else:
            raise ValueError("Unexpected agent config response shape")

        profiles: list[AgentProfileConfig] = []
        skipped = 0
        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            profile = self._profile_from_api_payload(item)
            if profile is not None:
                profiles.append(profile)
            else:
                skipped += 1
                agent_log(
                    logger,
                    "SYNC",
                    logging.WARNING,
                    "skipped invalid profile from dashboard API",
                    instagram_id=item.get("instagram_id") or item.get("instagramUserId"),
                    username=item.get("username"),
                )
        if skipped:
            agent_log(
                logger,
                "SYNC",
                logging.WARNING,
                "some profiles were skipped during HTTP sync",
                skipped=skipped,
                loaded=len(profiles),
            )
        return profiles

    @staticmethod
    def _profile_from_api_payload(data: dict[str, Any]) -> AgentProfileConfig | None:
        instagram_id = str(
            data.get("instagram_id")
            or data.get("instagramUserId")
            or data.get("instagram_user_id")
            or ""
        )
        if not instagram_id:
            return None

        access_token = str(
            data.get("access_token")
            or data.get("accessToken")
            or ""
        )
        if not access_token:
            return None

        automation = data.get("automation")
        if not isinstance(automation, dict):
            automation = {}

        def _bool(*keys: str, default: bool = True) -> bool:
            for key in keys:
                if key in data:
                    return bool(data[key])
                if key in automation:
                    return bool(automation[key])
            return default

        return AgentProfileConfig(
            account_id=int(data.get("account_id") or data.get("accountId") or 0),
            instagram_id=instagram_id,
            username=str(data.get("username") or ""),
            access_token=access_token,
            graph_host=str(data.get("graph_host") or data.get("graphHost") or "graph.instagram.com"),
            api_version=str(data.get("api_version") or data.get("apiVersion") or "v21.0"),
            system_prompt=str(data.get("system_prompt") or data.get("systemPrompt") or ""),
            reply_comments=_bool("reply_comments", "commentReplyEnabled", default=True),
            reply_messages=_bool("reply_messages", "messageReplyEnabled", default=True),
            reply_mentions=_bool("reply_mentions", "mentionReplyEnabled", default=True),
            reply_story_mentions=_bool(
                "reply_story_mentions",
                "storyReplyEnabled",
                "storyMentionReplyEnabled",
                default=False,
            ),
            delay_min=int(data.get("delay_min", data.get("delayMin", 3))),
            delay_max=int(data.get("delay_max", data.get("delayMax", 15))),
            language_mode=str(data.get("language_mode") or data.get("languageMode") or "auto"),
            enabled=bool(data.get("enabled", data.get("isActive", True))),
            ai_provider=str(data.get("ai_provider") or data.get("aiProvider") or "gemini"),
            ai_api_key=data.get("ai_api_key") or data.get("aiApiKey") or data.get("apiKey"),
            gemini_model=data.get("gemini_model") or data.get("geminiModel") or data.get("aiModel"),
        )
