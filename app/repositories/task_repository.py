"""Task persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskType


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_enabled(self) -> list[Task]:
        result = await self._session.execute(
            select(Task)
            .where(Task.enabled.is_(True))
            .order_by(Task.priority.asc(), Task.id.asc())
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[Task]:
        result = await self._session.execute(select(Task).order_by(Task.priority.asc(), Task.id.asc()))
        return list(result.scalars().all())

    async def get(self, task_id: int) -> Task | None:
        result = await self._session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        task_type: str,
        settings: dict[str, Any],
        enabled: bool = True,
        priority: int = 100,
    ) -> Task:
        task = Task(
            name=name,
            task_type=task_type,
            settings=settings,
            enabled=enabled,
            priority=priority,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def update(self, task: Task, **fields: Any) -> Task:
        for key, value in fields.items():
            setattr(task, key, value)
        await self._session.flush()
        return task

    async def delete(self, task_id: int) -> bool:
        task = await self.get(task_id)
        if task is None:
            return False
        await self._session.delete(task)
        await self._session.flush()
        return True

    async def ensure_defaults(self) -> None:
        """Seed built-in tasks when the table is empty."""
        result = await self._session.execute(select(Task.id).limit(1))
        if result.scalar_one_or_none() is not None:
            return

        defaults = [
            ("DM Auto Reply", TaskType.DM_AUTO_REPLY, 10, {
                "ai_enabled": True,
                "delay_min": 0,
                "delay_max": 0,
                "memory_enabled": True,
                "profile_context_enabled": True,
            }),
            ("Comment Auto Reply", TaskType.COMMENT_AUTO_REPLY, 20, {
                "ai_enabled": True,
                "fixed_reply": None,
                "ignore_own_comments": True,
                "reply_once_per_user": False,
                "delay_min": 3,
                "delay_max": 15,
            }),
            ("Mention Reply", TaskType.MENTION_REPLY, 30, {
                "ai_enabled": True,
            }),
        ]
        for name, task_type, priority, settings in defaults:
            self._session.add(
                Task(name=name, task_type=task_type, priority=priority, settings=settings, enabled=True)
            )
        await self._session.flush()
