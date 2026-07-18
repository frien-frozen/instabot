"""Comment persistence and duplicate-protection repository."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.database import MongoSession, next_id
from app.models.comment import Comment
from app.schemas import CommentCreate
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class CommentRepository:
    """
    Data access layer for comment records.

    Encapsulates duplicate detection and reply state management so route
    handlers remain thin.
    """

    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def get_by_comment_id(self, comment_id: str) -> Comment | None:
        """Return an existing comment by its Instagram comment ID."""
        return await Comment.find_one(Comment.comment_id == comment_id)

    async def has_been_replied(self, comment_id: str) -> bool:
        """Return True if this comment has already received a reply."""
        comment = await self.get_by_comment_id(comment_id)
        return comment is not None and comment.replied

    async def create(self, data: CommentCreate) -> Comment | None:
        """
        Persist a new comment.

        Returns None if the comment already exists (duplicate webhook delivery).
        """
        existing = await self.get_by_comment_id(data.comment_id)
        if existing is not None:
            log_event(
                logger,
                logging.INFO,
                "comment_duplicate_skipped",
                comment_id=data.comment_id,
            )
            return None

        comment = Comment(
            id=await next_id("comments"),
            comment_id=data.comment_id,
            username=data.username,
            message=data.message,
            media_id=data.media_id,
            parent_comment_id=data.parent_comment_id,
            from_id=data.from_id,
            account_id=data.account_id,
            replied=False,
        )
        try:
            await comment.insert()
        except DuplicateKeyError:
            log_event(
                logger,
                logging.INFO,
                "comment_duplicate_skipped",
                comment_id=data.comment_id,
            )
            return None

        log_event(
            logger,
            logging.INFO,
            "comment_stored",
            comment_id=data.comment_id,
            username=data.username,
            media_id=data.media_id,
        )
        return comment

    async def mark_replied(self, comment_id: str, reply_text: str) -> Comment | None:
        """Mark a comment as replied and store the generated reply text."""
        comment = await self.get_by_comment_id(comment_id)
        if comment is None:
            return None

        comment.replied = True
        comment.reply_text = reply_text
        comment.replied_at = datetime.now(timezone.utc)
        await comment.save()

        log_event(
            logger,
            logging.INFO,
            "comment_marked_replied",
            comment_id=comment_id,
            reply_text=reply_text,
        )
        return comment

    async def recent_by_user(
        self,
        *,
        from_id: str | None = None,
        username: str | None = None,
        limit: int = 5,
        exclude_comment_id: str | None = None,
    ) -> list[Comment]:
        """Recent comments from the same person (for Gemini memory)."""
        rows: list[Comment] = []
        if from_id:
            rows = (
                await Comment.find(Comment.from_id == from_id)
                .sort([("created_at", -1)])
                .limit(limit + 5)
                .to_list()
            )
        elif username:
            rows = (
                await Comment.find(Comment.username == username)
                .sort([("created_at", -1)])
                .limit(limit + 5)
                .to_list()
            )
        else:
            return []

        matched: list[Comment] = []
        for row in rows:
            if exclude_comment_id and row.comment_id == exclude_comment_id:
                continue
            matched.append(row)
            if len(matched) >= limit:
                break
        return list(reversed(matched))
