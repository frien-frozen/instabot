"""Runtime agent profile configuration synced from the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.instagram_account import InstagramAccount


@dataclass(frozen=True)
class AgentProfileConfig:
    """In-memory snapshot of one Instagram account's automation settings."""

    account_id: int
    instagram_id: str
    username: str
    access_token: str
    graph_host: str
    api_version: str
    system_prompt: str
    reply_comments: bool
    reply_messages: bool
    reply_mentions: bool
    reply_story_mentions: bool
    delay_min: int
    delay_max: int
    language_mode: str
    enabled: bool
    ai_provider: str
    ai_api_key: str | None
    gemini_model: str | None

    @property
    def graph_base_url(self) -> str:
        return f"https://{self.graph_host}/{self.api_version}"

    def is_feature_enabled(self, event_type: str, mention_type: str | None = None) -> bool:
        if not self.enabled:
            return False
        if event_type == "comment":
            return self.reply_comments
        if event_type == "message":
            return self.reply_messages
        if event_type == "mention":
            if mention_type == "story_mentions":
                return self.reply_story_mentions
            return self.reply_mentions
        return False

    @classmethod
    def from_account(cls, account: InstagramAccount) -> AgentProfileConfig:
        return cls(
            account_id=account.id,
            instagram_id=account.instagram_user_id,
            username=(account.username or "").strip(),
            access_token=account.access_token.strip(),
            graph_host=account.graph_host,
            api_version=account.api_version,
            system_prompt=account.system_prompt,
            reply_comments=account.comments_enabled,
            reply_messages=account.messages_enabled,
            reply_mentions=account.mentions_enabled,
            reply_story_mentions=account.story_mentions_enabled,
            delay_min=account.reply_delay_min,
            delay_max=account.reply_delay_max,
            language_mode=account.language_mode,
            enabled=account.is_active,
            ai_provider=account.ai_provider,
            ai_api_key=account.ai_api_key.strip() if account.ai_api_key else None,
            gemini_model=account.gemini_model,
        )

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "instagram_id": self.instagram_id,
            "username": self.username,
            "system_prompt": self.system_prompt,
            "reply_comments": self.reply_comments,
            "reply_messages": self.reply_messages,
            "reply_mentions": self.reply_mentions,
            "reply_story_mentions": self.reply_story_mentions,
            "commentReplyEnabled": self.reply_comments,
            "messageReplyEnabled": self.reply_messages,
            "mentionReplyEnabled": self.reply_mentions,
            "storyMentionReplyEnabled": self.reply_story_mentions,
            "delay_min": self.delay_min,
            "delay_max": self.delay_max,
            "language_mode": self.language_mode,
            "enabled": self.enabled,
            "ai_provider": self.ai_provider,
            "gemini_model": self.gemini_model,
        }
