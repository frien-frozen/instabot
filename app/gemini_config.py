"""Centralized Gemini model names and API configuration."""

from __future__ import annotations

import importlib.metadata

# Stable API aliases — prefer these over version-pinned model IDs.
# Flash-Lite = cheapest tier that still works well for short Instagram chat.
GEMINI_FLASH_LITE_LATEST = "gemini-flash-lite-latest"
GEMINI_FLASH_LATEST = "gemini-flash-latest"
# Explicit cheap pin (use if "latest" alias is unavailable on the key).
GEMINI_25_FLASH_LITE = "gemini-2.5-flash-lite"

DEFAULT_GEMINI_MODEL = GEMINI_FLASH_LITE_LATEST

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/"
GEMINI_API_VERSION = "v1beta"

KNOWN_GEMINI_ALIASES = frozenset({
    GEMINI_FLASH_LITE_LATEST,
    GEMINI_FLASH_LATEST,
    GEMINI_25_FLASH_LITE,
})

_gemini_ready = False


def get_gemini_api_endpoint() -> str:
    """Full REST base path used by the google-genai SDK."""
    return f"{GEMINI_API_BASE_URL.rstrip('/')}/{GEMINI_API_VERSION}"


def get_gemini_sdk_version() -> str:
    try:
        return importlib.metadata.version("google-genai")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def normalize_gemini_model(configured: str | None) -> str:
    """Resolve the model ID from configuration (empty → default, block gemma)."""
    model = (configured or "").strip()
    if not model:
        return DEFAULT_GEMINI_MODEL
    if model.lower().startswith("gemma"):
        return DEFAULT_GEMINI_MODEL
    return model


def is_gemini_ready() -> bool:
    """True after startup validation succeeds."""
    return _gemini_ready


def set_gemini_ready(ready: bool) -> None:
    global _gemini_ready
    _gemini_ready = ready
