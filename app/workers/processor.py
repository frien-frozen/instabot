"""Background event processor."""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.repositories.event_repository import EventRepository
from app.repositories.task_repository import TaskRepository
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService
from app.tasks.handlers.base import HandlerContext
from app.tasks.registry import HANDLERS, match_tasks
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class EventWorker:
  """Poll the events queue and dispatch to task handlers."""

  def __init__(
      self,
      settings: Settings,
      session_factory: async_sessionmaker[AsyncSession],
      gemini: GeminiService,
      instagram: InstagramService,
  ) -> None:
      self._settings = settings
      self._session_factory = session_factory
      self._ctx = HandlerContext(
          settings=settings,
          session_factory=session_factory,
          gemini=gemini,
          instagram=instagram,
      )
      self._running = False
      self._task: asyncio.Task | None = None

  async def start(self) -> None:
      if self._running:
          return
      self._running = True
      async with self._session_factory() as session:
          await TaskRepository(session).ensure_defaults()
          await session.commit()
      self._task = asyncio.create_task(self._loop())
      log_event(logger, logging.INFO, "event_worker_started")

  async def stop(self) -> None:
      self._running = False
      if self._task:
          self._task.cancel()
          try:
              await self._task
          except asyncio.CancelledError:
              pass
      log_event(logger, logging.INFO, "event_worker_stopped")

  async def _loop(self) -> None:
      while self._running:
          try:
              processed = await self._process_batch()
              await asyncio.sleep(0.5 if processed else 1.5)
          except asyncio.CancelledError:
              break
          except Exception as exc:
              log_event(logger, logging.ERROR, "event_worker_loop_error", error=str(exc))
              await asyncio.sleep(2)

  async def _process_batch(self) -> int:
      async with self._session_factory() as session:
          event_repo = EventRepository(session)
          task_repo = TaskRepository(session)
          events = await event_repo.claim_batch(limit=self._settings.worker_batch_size)
          tasks = await task_repo.list_enabled()
          await session.commit()

      if not events:
          return 0

      count = 0
      for event in events:
          started = time.monotonic()
          try:
              matched = match_tasks(event, tasks)
              if not matched:
                  async with self._session_factory() as session:
                      repo = EventRepository(session)
                      db_event = await repo.get(event.id)
                      if db_event:
                          await repo.mark_completed(db_event)
                      await session.commit()
                  continue

              for task in matched:
                  handler = HANDLERS.get(task.task_type)
                  if handler is None:
                      continue
                  await handler.handle(self._ctx, task, event)

              async with self._session_factory() as session:
                  repo = EventRepository(session)
                  db_event = await repo.get(event.id)
                  if db_event is None:
                      continue
                  await repo.mark_completed(db_event, task_id=matched[0].id if matched else None)
                  await session.commit()

              log_event(
                  logger,
                  logging.INFO,
                  "event_processed",
                  event_id=event.event_id,
                  event_type=event.event_type,
                  task_id=matched[0].id if matched else None,
                  execution_ms=int((time.monotonic() - started) * 1000),
                  gemini_model=self._ctx.gemini.model,
                  attempts=event.attempts,
              )
              count += 1
          except Exception as exc:
              async with self._session_factory() as session:
                  repo = EventRepository(session)
                  db_event = await repo.get(event.id)
                  if db_event:
                      await repo.mark_failed(db_event, str(exc))
                  await session.commit()
              log_event(
                  logger,
                  logging.ERROR,
                  "event_processing_failed",
                  event_id=event.event_id,
                  task_id=event.task_id,
                  error=str(exc),
                  attempts=event.attempts,
                  execution_ms=int((time.monotonic() - started) * 1000),
              )
      return count
