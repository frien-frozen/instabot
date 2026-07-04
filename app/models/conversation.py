"""Conversation model — one row per Instagram DM thread."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    """Instagram Direct Message conversation thread."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_account_user", "account_id", "user_id", unique=True),
    )
