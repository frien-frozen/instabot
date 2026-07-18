"""Load markdown knowledge files into the Gemini system prompt."""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Project-root /knowledge — add new .md files here; no code changes required.
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

_PROMPT_FILE = "prompt.md"
_cached_prompt: str | None = None
_loaded_files: list[str] = []


def knowledge_dir() -> Path:
    return KNOWLEDGE_DIR


def loaded_files() -> list[str]:
    return list(_loaded_files)


def load_knowledge(*, force: bool = False) -> str:
    """
    Load every markdown file from knowledge/ and merge into one system prompt.

    prompt.md is always first (personality / boundaries).
    All other *.md files are appended as knowledge sections (sorted by name).
    New files are picked up automatically on the next load/startup.
    """
    global _cached_prompt, _loaded_files

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
        _loaded_files = []
        return _cached_prompt

    prompt_path = KNOWLEDGE_DIR / _PROMPT_FILE
    other_files = sorted(
        p for p in KNOWLEDGE_DIR.glob("*.md") if p.name != _PROMPT_FILE
    )

    parts: list[str] = []
    files: list[str] = []

    if prompt_path.is_file():
        parts.append(prompt_path.read_text(encoding="utf-8").strip())
        files.append(prompt_path.name)
    else:
        log_event(logger, logging.WARNING, "knowledge_prompt_missing", path=str(prompt_path))

    if other_files:
        parts.append("# CLINIC KNOWLEDGE BASE")
        for path in other_files:
            body = path.read_text(encoding="utf-8").strip()
            if not body:
                continue
            title = path.stem.replace("_", " ").upper()
            parts.append(f"## {title}\n\n{body}")
            files.append(path.name)

    _cached_prompt = "\n\n".join(parts).strip()
    _loaded_files = files

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


def get_system_prompt(*, override: str | None = None) -> str:
    """
    System prompt for Gemini.

    If override (e.g. SYSTEM_PROMPT env) is set, use it as the core and still
    append knowledge-base markdown (excluding prompt.md) so clinic facts remain.
    Otherwise use the full merged knowledge load (prompt.md + all docs).
    """
    knowledge = load_knowledge()
    core = (override or "").strip()
    if not core:
        return knowledge

    # Env override replaces prompt.md personality only; keep clinic docs.
    prompt_path = KNOWLEDGE_DIR / _PROMPT_FILE
    other_files = sorted(
        p for p in KNOWLEDGE_DIR.glob("*.md") if p.name != _PROMPT_FILE and p.is_file()
    )
    parts = [core, "# CLINIC KNOWLEDGE BASE"]
    for path in other_files:
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        title = path.stem.replace("_", " ").upper()
        parts.append(f"## {title}\n\n{body}")
    if prompt_path.is_file() and len(other_files) == 0:
        # Fallback: at least return override + full cache
        return f"{core}\n\n{knowledge}"
    return "\n\n".join(parts).strip()
