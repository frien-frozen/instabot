"""Comment model — stores every incoming Instagram comment and reply state."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Comment(Base):
    """
    Persisted Instagram comment record.

    Designed for future multi-account support via optional account_id column.
    """

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    comment_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="unknown")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    media_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    parent_comment_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reply_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Future: link to Instagram account when multi-account is supported
    account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    replied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_comments_media_id", "media_id"),
        Index("ix_comments_replied", "replied"),
        Index("ix_comments_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Comment id={self.id} comment_id={self.comment_id!r} replied={self.replied}>"
