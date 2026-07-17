"""Settings model — key-value store for future SaaS configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field


class Setting(Document):
    """
    Flexible key-value configuration table.

    Future use cases: AI personality presets, approval mode toggles,
    per-account rate limits, and feature flags.
    """

    id: Optional[int] = None
    key: Indexed(str, unique=True)
    value: str = ""
    description: Optional[str] = None
    account_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "settings"

    def __repr__(self) -> str:
        return f"<Setting key={self.key!r}>"
