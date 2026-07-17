"""Task persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.task import Task, TaskType


class TaskRepository:
    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def list_enabled(self) -> list[Task]:
        return (
            await Task.find(Task.enabled == True)  # noqa: E712
            .sort([("priority", 1), ("_id", 1)])
            .to_list()
        )

    async def list_all(self) -> list[Task]:
        return await Task.find_all().sort([("priority", 1), ("_id", 1)]).to_list()

    async def get(self, task_id: int) -> Task | None:
        return await Task.get(task_id)

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
            id=await next_id("tasks"),
            name=name,
            task_type=task_type,
            settings=settings,
            enabled=enabled,
            priority=priority,
        )
        await task.insert()
        return task

    async def update(self, task: Task, **fields: Any) -> Task:
        for key, value in fields.items():
            setattr(task, key, value)
        task.updated_at = datetime.now(timezone.utc)
        await task.save()
        return task

    async def delete(self, task_id: int) -> bool:
        task = await self.get(task_id)
        if task is None:
            return False
        await task.delete()
        return True

    async def ensure_defaults(self) -> None:
        """Seed built-in tasks when the collection is empty."""
        if await Task.find_one() is not None:
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
            await Task(
                id=await next_id("tasks"),
                name=name,
                task_type=task_type,
                priority=priority,
                settings=settings,
                enabled=True,
            ).insert()
