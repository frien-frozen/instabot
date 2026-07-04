"""Structured logging helpers for the Instagram automation agent."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.logging import log_event


def agent_log(
    logger: logging.Logger,
    tag: str,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """Emit a tagged agent log entry such as [SYNC], [COMMENT], [ERROR]."""
    log_event(logger, level, f"[{tag}] {message}", tag=tag, **fields)
