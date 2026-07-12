"""Shared handler dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.event import Event
from app.models.task import Task
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService


@dataclass
class HandlerContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    gemini: GeminiService
    instagram: InstagramService


class BaseTaskHandler:
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        raise NotImplementedError
