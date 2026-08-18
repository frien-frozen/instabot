"""Centralized Gemini model names and API configuration."""

from __future__ import annotations

import importlib.metadata
import logging
import re

logger = logging.getLogger(__name__)

# Stable API aliases — prefer these over version-pinned model IDs.
# Flash-Lite = cheapest tier that still works well for short Instagram chat.
GEMINI_FLASH_LITE_LATEST = "gemini-flash-lite-latest"
GEMINI_FLASH_LATEST = "gemini-flash-latest"
# Explicit cheap pin (use if "latest" alias is unavailable on the key).
GEMINI_25_FLASH_LITE = "gemini-2.5-flash-lite"

DEFAULT_GEMINI_MODEL = GEMINI_FLASH_LITE_LATEST

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/"
GEMINI_API_VERSION = "v1beta"

# Allowed Gemini models (Flash-Lite and Flash tier models)
KNOWN_GEMINI_ALIASES = frozenset({
    GEMINI_FLASH_LITE_LATEST,
    GEMINI_FLASH_LATEST,
    GEMINI_25_FLASH_LITE,
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-flash",
})

# Automatic fallback chain when 429 quota is hit on a free-tier model
FALLBACK_GEMINI_MODELS = (
    GEMINI_FLASH_LITE_LATEST,
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    GEMINI_FLASH_LATEST,
)

# Expensive models that burn budget or are unsupported (Pro, Ultra, Gemma, etc.)
_EXPENSIVE_MODEL = re.compile(
    r"(pro|ultra|thinking|exp|preview|gemma|imagen|veo)",
    re.IGNORECASE,
)


def get_gemini_api_endpoint() -> str:
    """Full REST base path used by the google-genai SDK."""
    return f"{GEMINI_API_BASE_URL.rstrip('/')}/{GEMINI_API_VERSION}"


def get_gemini_sdk_version() -> str:
    try:
        return importlib.metadata.version("google-genai")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def normalize_gemini_model(configured: str | None) -> str:
    """
    Resolve the model ID from configuration.

    Empty / gemma / Pro / expensive models → Flash-Lite (default).
    Flash and Flash-Lite models are allowed.
    """
    model = (configured or "").strip()
    if not model:
        return DEFAULT_GEMINI_MODEL

    lower = model.lower()
    if lower.startswith("gemma") or _EXPENSIVE_MODEL.search(lower):
        logger.warning(
            "gemini_model_forced_cheap configured=%s resolved=%s reason=expensive_or_blocked",
            model,
            DEFAULT_GEMINI_MODEL,
        )
        return DEFAULT_GEMINI_MODEL

    if model in KNOWN_GEMINI_ALIASES or "flash" in lower:
        return model

    logger.warning(
        "gemini_model_forced_cheap configured=%s resolved=%s reason=unknown_model",
        model,
        DEFAULT_GEMINI_MODEL,
    )
    return DEFAULT_GEMINI_MODEL


def is_gemini_ready() -> bool:
    """True after startup validation succeeds."""
    return _gemini_ready


def set_gemini_ready(ready: bool) -> None:
    global _gemini_ready
    _gemini_ready = ready
