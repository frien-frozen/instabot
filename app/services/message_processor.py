"""Backward-compatible wrapper — delegates to MessageService."""

from app.schemas import MessageCreate
from app.services.message_service import MessageService


class MessageProcessor:
    """Legacy alias for MessageService.process()."""

    def __init__(self, message_service: MessageService) -> None:
        self._service = message_service

    async def process(self, data: MessageCreate) -> None:
        await self._service.process(data)
