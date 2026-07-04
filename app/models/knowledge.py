"""Knowledge base entries per Instagram account."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

KNOWLEDGE_CATEGORIES = frozenset({
    "about",
    "faq",
    "products",
    "pricing",
    "website",
    "links",
    "contact",
    "custom",
})


class Knowledge(Base):
    """Structured knowledge injected into AI context."""

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="custom")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    account = relationship("InstagramAccount", back_populates="knowledge_items")

    __table_args__ = (
        Index("ix_knowledge_account_category", "account_id", "category"),
        Index("ix_knowledge_account_active", "account_id", "is_active"),
    )
