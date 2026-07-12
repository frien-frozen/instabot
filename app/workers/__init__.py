"""Background workers."""

from app.workers.processor import EventWorker

__all__ = ["EventWorker"]
