"""Application configuration loaded exclusively from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for a single Instagram account."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Instabot", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    port: int = Field(default=8080, alias="PORT")

    # Meta / Instagram
    verify_token: str = Field(..., alias="VERIFY_TOKEN")
    meta_access_token: str = Field(..., alias="META_ACCESS_TOKEN")
    meta_api_version: str = Field(default="v21.0", alias="META_API_VERSION")
    instagram_account_id: str = Field(default="", alias="INSTAGRAM_ACCOUNT_ID")
    instagram_user_id: str = Field(default="", alias="INSTAGRAM_USER_ID")
    meta_graph_host: str = Field(default="graph.instagram.com", alias="META_GRAPH_HOST")
    meta_app_secret: str = Field(default="", alias="META_APP_SECRET")

    # Google Gemini
    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    system_prompt: str = Field(default="", alias="SYSTEM_PROMPT")

    # Database
    database_url: str = Field(..., alias="DATABASE_URL")

    # Reply behavior
    reply_delay_min_seconds: int = Field(default=3, alias="REPLY_DELAY_MIN_SECONDS")
    reply_delay_max_seconds: int = Field(default=15, alias="REPLY_DELAY_MAX_SECONDS")
    comments_enabled: bool = Field(default=True, alias="COMMENTS_ENABLED")
    messages_enabled: bool = Field(default=True, alias="MESSAGES_ENABLED")
    mentions_enabled: bool = Field(default=True, alias="MENTIONS_ENABLED")
    story_mentions_enabled: bool = Field(default=True, alias="STORY_MENTIONS_ENABLED")
    dm_history_limit: int = Field(default=20, alias="DM_HISTORY_LIMIT")

    # Durability / retry
    retry_on_startup: bool = Field(default=True, alias="RETRY_ON_STARTUP")
    max_reply_attempts: int = Field(default=20, alias="MAX_REPLY_ATTEMPTS")
    retry_batch_size: int = Field(default=200, alias="RETRY_BATCH_SIZE")

    # HTTP client
    http_timeout_seconds: int = Field(default=30, alias="HTTP_TIMEOUT_SECONDS")
    http_max_retries: int = Field(default=3, alias="HTTP_MAX_RETRIES")

    @property
    def meta_graph_base_url(self) -> str:
        return f"https://{self.meta_graph_host}/{self.meta_api_version}"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def resolved_instagram_user_id(self) -> str:
        return self.instagram_user_id or self.instagram_account_id

    @property
    def resolved_system_prompt(self) -> str:
        return self.system_prompt.strip()


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance (singleton)."""
    return Settings()
