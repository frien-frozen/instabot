"""Task automation handlers."""

from app.tasks.registry import HANDLERS, match_tasks

__all__ = ["HANDLERS", "match_tasks"]
