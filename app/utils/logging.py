"""Structured logging utilities."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from typing import Any

from app.config import Settings, get_settings


class StructuredFormatter(logging.Formatter):
    """Format log records as structured key=value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            base.update(record.extra_fields)
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)
        return " | ".join(f"{k}={v!r}" for k, v in base.items())


def setup_logging(settings: Settings | None = None) -> None:
    """Configure application-wide structured logging."""
    cfg = settings or get_settings()
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured log entry with extra fields."""
    record = logger.makeRecord(
        logger.name,
        level,
        "(unknown file)",
        0,
        event,
        (),
        None,
    )
    record.extra_fields = fields
    logger.handle(record)


@contextmanager
def log_duration(logger: logging.Logger, operation: str, **fields: Any):
    """Context manager that logs elapsed time for an operation."""
    start = time.perf_counter()
    log_event(logger, logging.INFO, f"{operation}_started", **fields)
    try:
        yield
    except Exception as exc:
        elapsed = round(time.perf_counter() - start, 3)
        log_event(
            logger,
            logging.ERROR,
            f"{operation}_failed",
            duration_seconds=elapsed,
            error=str(exc),
            **fields,
        )
        raise
    else:
        elapsed = round(time.perf_counter() - start, 3)
        log_event(
            logger,
            logging.INFO,
            f"{operation}_completed",
            duration_seconds=elapsed,
            **fields,
        )
