"""Backward-compatible wrapper — delegates to CommentService."""

from app.schemas import CommentCreate
from app.services.comment_service import CommentService


class CommentProcessor:
    """Legacy alias for CommentService.process()."""

    def __init__(self, comment_service: CommentService) -> None:
        self._service = comment_service

    async def process(self, data: CommentCreate) -> None:
        await self._service.process(data)
