"""Load markdown knowledge files into the Gemini system prompt."""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Project-root /knowledge — add new .md files here; no code changes required.
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

_PROMPT_FILE = "prompt.md"

# Slim set for live Instagram chat — full knowledge is ~20k chars and burns credits.
_CHAT_KNOWLEDGE_FILES: tuple[str, ...] = (
    "prompt.md",
    "doctor_profile.md",
    "website.md",
    "booking.md",
    "services.md",
    "steroids.md",
    "operation.md",
    "sales.md",
    "communication.md",
    "pricing.md",
    "policies.md",
)

_cached_prompt: str | None = None
_cached_chat_prompt: str | None = None
_loaded_files: list[str] = []


def knowledge_dir() -> Path:
    return KNOWLEDGE_DIR


def loaded_files() -> list[str]:
    return list(_loaded_files)


def reload_knowledge() -> str:
    """Force-reload all knowledge/*.md into the cached system prompts."""
    global _cached_chat_prompt
    _cached_chat_prompt = None
    return load_knowledge(force=True)


def _merge_files(filenames: list[str]) -> tuple[str, list[str]]:
    parts: list[str] = []
    files: list[str] = []
    for name in filenames:
        path = KNOWLEDGE_DIR / name
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        if name == _PROMPT_FILE:
            parts.append(body)
        else:
            title = path.stem.replace("_", " ").upper()
            parts.append(f"## {title}\n\n{body}")
        files.append(name)
    return "\n\n".join(parts).strip(), files


def load_knowledge(*, force: bool = False) -> str:
    """
    Load every markdown file from knowledge/ and merge into one system prompt.

    prompt.md is always first (personality / boundaries).
    All other *.md files are appended as knowledge sections (sorted by name).
    New files are picked up automatically on the next load/startup.
    """
    global _cached_prompt, _cached_chat_prompt, _loaded_files

    if _cached_prompt is not None and not force:
        return _cached_prompt

    if not KNOWLEDGE_DIR.is_dir():
        log_event(
            logger,
            logging.ERROR,
            "knowledge_dir_missing",
            path=str(KNOWLEDGE_DIR),
        )
        _cached_prompt = ""
        _cached_chat_prompt = ""
        _loaded_files = []
        return _cached_prompt

    prompt_path = KNOWLEDGE_DIR / _PROMPT_FILE
    other_files = sorted(
        p.name for p in KNOWLEDGE_DIR.glob("*.md") if p.name != _PROMPT_FILE
    )
    ordered = ([_PROMPT_FILE] if prompt_path.is_file() else []) + other_files
    _cached_prompt, files = _merge_files(ordered)
    _loaded_files = files
    _cached_chat_prompt = None  # rebuild on next chat load

    # Keep legacy gemini_service aliases in sync for importers.
    try:
        from app.services import gemini_service as _gs

        _gs.DEFAULT_SYSTEM_PROMPT = _cached_prompt
        _gs.SYSTEM_PROMPT = _cached_prompt
    except Exception:
        pass

    log_event(
        logger,
        logging.INFO,
        "knowledge_loaded",
        files=files,
        file_count=len(files),
        chars=len(_cached_prompt),
        path=str(KNOWLEDGE_DIR),
    )
    return _cached_prompt


def load_chat_knowledge(*, force: bool = False) -> str:
    """Slim knowledge pack for live DM/comment replies (cheaper than full merge)."""
    global _cached_chat_prompt

    if _cached_chat_prompt is not None and not force:
        return _cached_chat_prompt

    if not KNOWLEDGE_DIR.is_dir():
        _cached_chat_prompt = load_knowledge(force=force)
        return _cached_chat_prompt

    merged, files = _merge_files(list(_CHAT_KNOWLEDGE_FILES))
    _cached_chat_prompt = merged
    log_event(
        logger,
        logging.INFO,
        "chat_knowledge_loaded",
        files=files,
        file_count=len(files),
        chars=len(_cached_chat_prompt),
    )
    return _cached_chat_prompt


def get_system_prompt(*, override: str | None = None, chat: bool = True) -> str:
    """
    System prompt for Gemini.

    chat=True (default for Instagram replies): slim knowledge set.
    chat=False: full knowledge base (admin / tools).
    """
    knowledge = load_chat_knowledge() if chat else load_knowledge()
    core = (override or "").strip()
    if not core:
        return knowledge

    # Env override replaces prompt.md personality; keep the same knowledge slice.
    names = (
        [n for n in _CHAT_KNOWLEDGE_FILES if n != _PROMPT_FILE]
        if chat
        else sorted(
            p.name for p in KNOWLEDGE_DIR.glob("*.md") if p.name != _PROMPT_FILE
        )
    )
    parts = [core, "# CLINIC KNOWLEDGE BASE"]
    for name in names:
        path = KNOWLEDGE_DIR / name
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        title = path.stem.replace("_", " ").upper()
        parts.append(f"## {title}\n\n{body}")
    return "\n\n".join(parts).strip()
