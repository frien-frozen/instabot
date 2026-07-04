"""Per-account configuration for the automation engine."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InstagramAccount(Base):
    """Instagram business account with tokens, prompts, toggles, and delays."""

    __tablename__ = "instagram_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instagram_user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    graph_host: Mapped[str] = mapped_column(String(255), nullable=False, default="graph.instagram.com")
    api_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v21.0")
    gemini_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ai_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="gemini")
    ai_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    reply_delay_min: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    reply_delay_max: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    comments_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    messages_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mentions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    story_mentions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    language_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    session_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OFFLINE")
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dashboard_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    knowledge_items = relationship("Knowledge", back_populates="account", lazy="selectin")

    __table_args__ = (Index("ix_instagram_accounts_active", "is_active"),)

    @property
    def graph_base_url(self) -> str:
        return f"https://{self.graph_host}/{self.api_version}"
