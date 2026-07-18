"""Media cache persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.media import Media


class MediaRepository:
    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def get_by_media_id(self, media_id: str) -> Media | None:
        return await Media.find_one(Media.media_id == media_id)

    async def upsert_from_graph(
        self,
        *,
        media_id: str,
        payload: dict[str, Any],
        intent: str,
        campaign_id: int | None = None,
    ) -> Media:
        existing = await self.get_by_media_id(media_id)
        now = datetime.now(timezone.utc)
        fields = {
            "media_type": str(payload.get("media_type") or payload.get("media_product_type") or ""),
            "caption": str(payload.get("caption") or ""),
            "permalink": str(payload.get("permalink") or ""),
            "timestamp": str(payload.get("timestamp")) if payload.get("timestamp") else None,
            "like_count": payload.get("like_count") if isinstance(payload.get("like_count"), int) else None,
            "comments_count": (
                payload.get("comments_count") if isinstance(payload.get("comments_count"), int) else None
            ),
            "intent": intent,
            "campaign_id": campaign_id,
            "raw": payload,
            "updated_at": now,
        }
        if existing is None:
            row = Media(id=await next_id("media"), media_id=media_id, fetched_at=now, **fields)
            await row.insert()
            return row

        for key, value in fields.items():
            setattr(existing, key, value)
        await existing.save()
        return existing
