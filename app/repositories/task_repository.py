"""Task persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.task import Task, TaskType

# Built-in automations that must always exist for the Instagram bot to work.
_DEFAULT_TASKS: tuple[tuple[str, str, int, dict[str, Any]], ...] = (
    (
        "DM Auto Reply",
        TaskType.DM_AUTO_REPLY,
        10,
        {
            "ai_enabled": True,
            "delay_min": 0,
            "delay_max": 0,
            "memory_enabled": True,
            "profile_context_enabled": True,
        },
    ),
    (
        "Comment Auto Reply",
        TaskType.COMMENT_AUTO_REPLY,
        20,
        {
            "ai_enabled": True,
            "fixed_reply": None,
            "ignore_own_comments": True,
            "reply_once_per_user": False,
            "delay_min": 3,
            "delay_max": 15,
        },
    ),
    (
        "Mention Reply",
        TaskType.MENTION_REPLY,
        30,
        {
            "ai_enabled": True,
        },
    ),
)


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

    async def get_by_type(self, task_type: str) -> Task | None:
        return await Task.find_one(Task.task_type == task_type)

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

    async def set_enabled(self, task_id: int, enabled: bool) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        return await self.update(task, enabled=enabled)

    async def toggle_enabled(self, task_id: int) -> Task | None:
        task = await self.get(task_id)
        if task is None:
            return None
        return await self.update(task, enabled=not task.enabled)

    async def delete(self, task_id: int) -> bool:
        task = await self.get(task_id)
        if task is None:
            return False
        await task.delete()
        return True

    async def ensure_defaults(self) -> list[str]:
        """
        Ensure core DM / comment / mention tasks exist and are enabled.

        Unlike a one-shot seed, this also repairs missing or disabled core tasks
        even when other tasks (e.g. reel engagement) already exist.
        """
        actions: list[str] = []
        for name, task_type, priority, settings in _DEFAULT_TASKS:
            existing = await self.get_by_type(task_type)
            if existing is None:
                await self.create(
                    name=name,
                    task_type=task_type,
                    settings=settings,
                    enabled=True,
                    priority=priority,
                )
                actions.append(f"created:{task_type}")
                continue

            changed = False
            if not existing.enabled:
                existing.enabled = True
                changed = True
                actions.append(f"enabled:{task_type}")
            # Keep ai_enabled on for core chat tasks (settings may be incomplete).
            merged = dict(existing.settings or {})
            for key, value in settings.items():
                if key not in merged:
                    merged[key] = value
                    changed = True
            if merged.get("ai_enabled") is False:
                merged["ai_enabled"] = True
                changed = True
                actions.append(f"ai_on:{task_type}")
            if changed:
                existing.settings = merged
                existing.updated_at = datetime.now(timezone.utc)
                await existing.save()
                if f"enabled:{task_type}" not in actions and f"ai_on:{task_type}" not in actions:
                    actions.append(f"repaired:{task_type}")
        return actions

    async def reset_all_tasks(self) -> dict[str, int]:
        """
        Delete every task, then re-seed core DM / comment / mention automations.

        Use after bad reel tasks or a messy task list. Reel automations must be
        recreated from Telegram afterward.
        """
        existing = await self.list_all()
        deleted = 0
        for task in existing:
            await task.delete()
            deleted += 1
        created = await self.ensure_defaults()
        return {"deleted": deleted, "core_actions": len(created)}
