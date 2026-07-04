"""Platform adapters."""

from app.adapters.base import PlatformAdapter
from app.adapters.instagram import InstagramAdapter

__all__ = ["PlatformAdapter", "InstagramAdapter"]
