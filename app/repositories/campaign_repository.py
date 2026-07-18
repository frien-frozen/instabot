"""Campaign persistence and CTA matching."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.campaign import Campaign


class CampaignRepository:
    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def list_enabled(self) -> list[Campaign]:
        now = datetime.now(timezone.utc)
        rows = await Campaign.find(Campaign.enabled == True).to_list()  # noqa: E712
        active: list[Campaign] = []
        for row in rows:
            if row.expires_at is not None and row.expires_at < now:
                continue
            active.append(row)
        return active

    async def get(self, campaign_id: int) -> Campaign | None:
        return await Campaign.get(campaign_id)

    async def create(self, **fields: Any) -> Campaign:
        row = Campaign(id=await next_id("campaigns"), **fields)
        await row.insert()
        return row

    async def match_comment(
        self,
        *,
        comment_text: str,
        media_id: str | None,
    ) -> Campaign | None:
        """Return the first enabled campaign whose trigger matches this comment."""
        text = (comment_text or "").strip().lower()
        if not text:
            return None

        campaigns = await self.list_enabled()
        # Prefer media-specific campaigns, then global ones.
        scoped = [c for c in campaigns if c.media_id and media_id and c.media_id == media_id]
        global_ones = [c for c in campaigns if not c.media_id]
        for campaign in (*scoped, *global_ones):
            for keyword in campaign.trigger_keywords:
                key = keyword.strip().lower()
                if not key:
                    continue
                if text == key or key in text:
                    return campaign
        return None
