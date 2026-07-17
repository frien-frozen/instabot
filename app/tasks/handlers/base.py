"""Shared handler dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.database import SessionFactory
from app.models.event import Event
from app.models.task import Task
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService


@dataclass
class HandlerContext:
    settings: Settings
    session_factory: SessionFactory
    gemini: GeminiService
    instagram: InstagramService


class BaseTaskHandler:
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        raise NotImplementedError
