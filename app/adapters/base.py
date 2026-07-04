"""Platform adapter interfaces — add new platforms without changing business logic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.config import Settings
from app.schemas.events import BaseEvent


class PlatformAdapter(ABC):
    """Parse webhooks and send replies for a specific platform."""

    platform: str

    @abstractmethod
    def parse_webhook(self, body: dict[str, Any], settings: Settings) -> list[BaseEvent]:
        """Extract normalized events from a raw webhook payload."""

    @abstractmethod
    def normalize_body(self, body: dict[str, Any], settings: Settings) -> dict[str, Any]:
        """Normalize platform-specific test/minimal payloads."""
