"""In-memory profile configuration cache refreshed from the dashboard API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.schemas.agent_config import AgentProfileConfig


class ProfileConfigStore:
    """Thread-safe in-memory store of dashboard-synced profile configs."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_instagram_id: dict[str, AgentProfileConfig] = {}
        self._by_username: dict[str, AgentProfileConfig] = {}
        self._last_sync_at: datetime | None = None
        self._last_sync_ok: bool = False

    @property
    def last_sync_at(self) -> datetime | None:
        return self._last_sync_at

    @property
    def last_sync_ok(self) -> bool:
        return self._last_sync_ok

    async def replace_all(self, profiles: list[AgentProfileConfig], *, sync_ok: bool) -> None:
        by_id = {profile.instagram_id: profile for profile in profiles if profile.instagram_id}
        by_username: dict[str, AgentProfileConfig] = {}
        for profile in profiles:
            if profile.username:
                by_username[profile.username.lower()] = profile

        async with self._lock:
            self._by_instagram_id = by_id
            self._by_username = by_username
            self._last_sync_at = datetime.now(timezone.utc)
            self._last_sync_ok = sync_ok

    async def upsert(self, profile: AgentProfileConfig) -> None:
        async with self._lock:
            if profile.instagram_id:
                self._by_instagram_id[profile.instagram_id] = profile
            if profile.username:
                self._by_username[profile.username.lower()] = profile

    async def get_by_instagram_id(self, instagram_id: str) -> AgentProfileConfig | None:
        async with self._lock:
            return self._by_instagram_id.get(instagram_id)

    async def get_by_username(self, username: str) -> AgentProfileConfig | None:
        async with self._lock:
            return self._by_username.get(username.lower())

    async def all_profiles(self) -> list[AgentProfileConfig]:
        async with self._lock:
            return list(self._by_instagram_id.values())

    async def profile_count(self) -> int:
        async with self._lock:
            return len(self._by_instagram_id)

    def snapshot_by_instagram_id(self) -> dict[str, AgentProfileConfig]:
        return dict(self._by_instagram_id)
