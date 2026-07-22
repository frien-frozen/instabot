"""Telegram admin panel for automation tasks."""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.database import get_session_factory
from app.models.task import TaskType
from app.repositories.event_repository import EventRepository
from app.repositories.task_repository import TaskRepository
from app.services.instagram_service import InstagramService
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# In-memory reel creation wizard state keyed by Telegram chat id
_reel_wizards: dict[int, dict[str, Any]] = {}


def is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.telegram_admin_id_list


async def build_main_menu_text(settings: Settings) -> str:
    factory = get_session_factory(settings)
    async with factory() as session:
        tasks = await TaskRepository(session).list_all()
        stats = await EventRepository(session).count_by_status()
    enabled = sum(1 for t in tasks if t.enabled)
    lines = [
        "🤖 *Instagram Automation Platform*",
        "",
        f"Tasks: {len(tasks)} ({enabled} on)",
        f"Events pending: {stats.get('pending', 0)}",
        f"Events failed: {stats.get('failed', 0)}",
        "",
        "Use the buttons below to manage automations.",
        "",
        "Commands:",
        "`/enable <id>` · `/disable <id>` · `/toggle <id>`",
        "`/delete <id>` · `/repair` · `/reset`",
        "`/behaviour` — update assistant behavior (no manual markdown)",
        "`/cancel` — cancel the current wizard",
    ]
    return "\n".join(lines)


MAIN_KEYBOARD = [
    ["📋 Tasks", "➕ Create Task"],
    ["🔧 Repair Tasks", "♻️ Reset Tasks"],
    ["📊 Statistics", "📝 Logs"],
]


async def list_tasks_text(settings: Settings) -> str:
    factory = get_session_factory(settings)
    async with factory() as session:
        tasks = await TaskRepository(session).list_all()
    if not tasks:
        return "No tasks yet. Tap *Repair Tasks* or *Create Task*."
    lines = [
        "*Tasks*",
        "",
        "Toggle: `/toggle <id>`",
        "Enable: `/enable <id>`",
        "Disable: `/disable <id>`",
        "",
    ]
    for t in tasks:
        status = "✅ ON" if t.enabled else "⏸ OFF"
        lines.append(f"{status} `{t.id}` — {t.name} (`{t.task_type}`)")
    return "\n".join(lines)


async def stats_text(settings: Settings) -> str:
    factory = get_session_factory(settings)
    async with factory() as session:
        stats = await EventRepository(session).count_by_status()
    lines = ["*Statistics*"]
    for key in ("pending", "processing", "completed", "failed"):
        lines.append(f"• {key}: {stats.get(key, 0)}")
    return "\n".join(lines)


async def repair_tasks(settings: Settings) -> str:
    """Create/enable missing core DM + comment + mention tasks."""
    factory = get_session_factory(settings)
    async with factory() as session:
        actions = await TaskRepository(session).ensure_defaults()
        await session.commit()
        tasks = await TaskRepository(session).list_all()
    enabled = [t for t in tasks if t.enabled]
    detail = ", ".join(actions) if actions else "already healthy"
    log_event(logger, logging.INFO, "telegram_tasks_repaired", actions=actions)
    return (
        f"✅ Core tasks repaired ({detail}).\n"
        f"Enabled now: {len(enabled)}/{len(tasks)}\n"
        "DM, Comment, and Mention auto-replies are ON."
    )


async def reset_all_tasks(settings: Settings) -> str:
    """Wipe all tasks and re-seed core automations only."""
    factory = get_session_factory(settings)
    async with factory() as session:
        result = await TaskRepository(session).reset_all_tasks()
        await session.commit()
        tasks = await TaskRepository(session).list_all()
    log_event(logger, logging.WARNING, "telegram_tasks_reset", **result)
    lines = [
        "♻️ *All tasks reset.*",
        f"Deleted: {result['deleted']}",
        f"Core tasks restored: {len(tasks)}",
        "",
        "Reel automations were cleared — create them again with *Create Task*.",
    ]
    for t in tasks:
        lines.append(f"✅ `{t.id}` — {t.name}")
    return "\n".join(lines)


async def set_task_enabled(settings: Settings, task_id: int, enabled: bool) -> str:
    factory = get_session_factory(settings)
    async with factory() as session:
        task = await TaskRepository(session).set_enabled(task_id, enabled)
        await session.commit()
    if task is None:
        return f"Task `{task_id}` not found."
    state = "enabled ✅" if task.enabled else "disabled ⏸"
    return f"Task `{task.id}` ({task.name}) {state}."


async def toggle_task(settings: Settings, task_id: int) -> str:
    factory = get_session_factory(settings)
    async with factory() as session:
        task = await TaskRepository(session).toggle_enabled(task_id)
        await session.commit()
    if task is None:
        return f"Task `{task_id}` not found."
    state = "enabled ✅" if task.enabled else "disabled ⏸"
    return f"Task `{task.id}` ({task.name}) {state}."


async def create_reel_task(settings: Settings, chat_id: int, wizard: dict[str, Any]) -> str:
    ig = InstagramService(settings)
    media_id = await ig.resolve_media_id_from_url(wizard["reel_url"])
    factory = get_session_factory(settings)
    async with factory() as session:
        repo = TaskRepository(session)
        # Replace any older reel automations for the same media.
        removed = 0
        for old in await repo.list_all():
            if (
                old.task_type == TaskType.REEL_ENGAGEMENT
                and str((old.settings or {}).get("media_id") or "") == str(media_id)
            ):
                await old.delete()
                removed += 1
        task = await repo.create(
            name=f"Reel: {wizard['reel_url'][-20:]}",
            task_type=TaskType.REEL_ENGAGEMENT,
            priority=5,
            settings={
                "reel_url": wizard["reel_url"],
                "media_id": media_id,
                "public_reply_mode": wizard.get("public_reply_mode", "ai"),
                "public_reply_fixed": wizard.get("public_reply_fixed", ""),
                "dm_mode": wizard.get("dm_mode", "ai"),
                "dm_fixed": wizard.get("dm_fixed", ""),
                "require_follow": wizard.get("require_follow", False),
                "require_like": wizard.get("require_like", False),
                "gate_message": "Follow the page and like this post first 🙌",
            },
        )
        await session.commit()
    _reel_wizards.pop(chat_id, None)
    log_event(
        logger,
        logging.INFO,
        "reel_task_created",
        task_id=task.id,
        media_id=media_id,
        replaced=removed,
    )
    extra = f" (replaced {removed} old)" if removed else ""
    return (
        f"✅ Reel automation created (task `{task.id}`, media `{media_id}`){extra}\n"
        f"Public: `{wizard.get('public_reply_mode')}` · DM: `{wizard.get('dm_mode')}`"
    )

def get_wizard(chat_id: int) -> dict[str, Any] | None:
    return _reel_wizards.get(chat_id)


def set_wizard(chat_id: int, data: dict[str, Any]) -> None:
    _reel_wizards[chat_id] = data


def clear_wizard(chat_id: int) -> None:
    _reel_wizards.pop(chat_id, None)
