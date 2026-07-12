"""Task handler registry and dispatch."""

from __future__ import annotations

from app.models.event import Event, EventType
from app.models.task import Task, TaskType
from app.tasks.handlers.comment import CommentTaskHandler
from app.tasks.handlers.dm import DmTaskHandler
from app.tasks.handlers.mention import MentionTaskHandler
from app.tasks.handlers.reel import ReelEngagementHandler

HANDLERS = {
    TaskType.DM_AUTO_REPLY: DmTaskHandler(),
    TaskType.COMMENT_AUTO_REPLY: CommentTaskHandler(),
    TaskType.MENTION_REPLY: MentionTaskHandler(),
    TaskType.REEL_ENGAGEMENT: ReelEngagementHandler(),
}


def match_tasks(event: Event, tasks: list[Task]) -> list[Task]:
    """Return enabled tasks that should handle this event."""
    matched: list[Task] = []
    reel_matched = False

    for task in tasks:
        if not task.enabled:
            continue
        if task.task_type == TaskType.DM_AUTO_REPLY and event.event_type == EventType.DM:
            matched.append(task)
        elif task.task_type == TaskType.COMMENT_AUTO_REPLY and event.event_type == EventType.COMMENT:
            matched.append(task)
        elif task.task_type == TaskType.MENTION_REPLY and event.event_type in (EventType.MENTION, EventType.STORY_MENTION):
            matched.append(task)
        elif task.task_type == TaskType.REEL_ENGAGEMENT and event.event_type == EventType.COMMENT:
            media_id = event.payload.get("media_id")
            task_media = task.settings.get("media_id")
            if media_id and task_media and str(media_id) == str(task_media):
                matched.append(task)
                reel_matched = True

    if reel_matched:
        matched = [t for t in matched if t.task_type != TaskType.COMMENT_AUTO_REPLY]

    return matched
