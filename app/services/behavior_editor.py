"""Admin-driven updates to modular behavior knowledge files."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.knowledge import knowledge_dir, load_knowledge
from app.services.gemini_service import GeminiService
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Only these files may be changed via /behaviour.
BEHAVIOR_FILES: frozenset[str] = frozenset(
    {
        "booking.md",
        "communication.md",
        "campaigns.md",
        "sales.md",
        "policies.md",
    }
)

# Explicitly never touch (factual / clinic facts).
PROTECTED_FILES: frozenset[str] = frozenset(
    {
        "pricing.md",
        "services.md",
        "doctor_profile.md",
        "website.md",
        "laboratory.md",
        "prompt.md",
        "faq.md",
        "operation.md",
        "organ_trade_safety.md",
        "steroids.md",
    }
)

BACKUP_DIR_NAME = "backups"


@dataclass
class FileChangeSummary:
    filename: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)


@dataclass
class BehaviorEditResult:
    ok: bool
    message: str
    modified_files: list[str] = field(default_factory=list)
    backup_dir: str | None = None
    changes: list[FileChangeSummary] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def format_edit_summary(result: BehaviorEditResult) -> str:
    """Human-readable Telegram summary for the administrator."""
    if not result.ok:
        return result.message

    lines = ["✅ Behavior updated.", ""]
    if result.backup_dir:
        lines.append(f"Backup: `{result.backup_dir}`")
        lines.append("")

    if not result.modified_files:
        lines.append("No files needed changes.")
    else:
        lines.append("Modified files:")
        for name in result.modified_files:
            lines.append(f"• `{name}`")
        lines.append("")

        for change in result.changes:
            lines.append(f"*{change.filename}*")
            for rule in change.added:
                lines.append(f"  + added: {rule}")
            for rule in change.removed:
                lines.append(f"  − removed: {rule}")
            for rule in change.updated:
                lines.append(f"  ~ updated: {rule}")
            if not (change.added or change.removed or change.updated):
                lines.append("  (content merged; no detailed rule list)")
            lines.append("")

    for note in result.notes:
        lines.append(f"Note: {note}")

    lines.append("Knowledge base reloaded — new behavior is active now.")
    return "\n".join(lines).strip()


def _parse_change_entries(raw: Any, filename: str) -> FileChangeSummary:
    change = FileChangeSummary(filename=filename)
    if not isinstance(raw, list):
        return change
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").lower().strip()
        rule = str(entry.get("rule") or "").strip()
        if not rule:
            continue
        if kind == "added":
            change.added.append(rule)
        elif kind == "removed":
            change.removed.append(rule)
        else:
            change.updated.append(rule)
    return change


class BehaviorEditor:
    """
    Analyze natural-language admin instructions and merge into behavior markdown.

    Never writes factual knowledge files. Always backs up before overwrite.
    Reloads the in-memory knowledge prompt after successful writes.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gemini = GeminiService(settings)
        self._root = knowledge_dir()

    def _read_behavior_files(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in sorted(BEHAVIOR_FILES):
            path = self._root / name
            if path.is_file():
                out[name] = path.read_text(encoding="utf-8")
            else:
                out[name] = ""
        return out

    def _backup_files(self, filenames: list[str]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_root = self._root / BACKUP_DIR_NAME / stamp
        backup_root.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            src = self._root / name
            if src.is_file():
                shutil.copy2(src, backup_root / name)
        return backup_root

    def _restore_from_backup(self, backup_path: Path, filenames: list[str]) -> None:
        for name in filenames:
            src = backup_path / name
            dest = self._root / name
            if src.is_file():
                shutil.copy2(src, dest)

    async def apply_instructions(self, instructions: str) -> BehaviorEditResult:
        text = (instructions or "").strip()
        if not text:
            return BehaviorEditResult(ok=False, message="Empty instructions. Send the new behavior text.")

        if len(text) > 8000:
            return BehaviorEditResult(
                ok=False,
                message="Instructions are too long. Please send a shorter description (under 8000 characters).",
            )

        current = self._read_behavior_files()
        try:
            plan = await self._gemini.plan_behavior_targets(
                instructions=text,
                current_files=current,
                editable_files=sorted(BEHAVIOR_FILES),
                protected_files=sorted(PROTECTED_FILES),
            )
        except Exception as exc:
            log_event(logger, logging.ERROR, "behavior_plan_failed", error=str(exc))
            return BehaviorEditResult(
                ok=False,
                message=f"Could not analyze instructions: {exc}",
            )

        if not plan:
            return BehaviorEditResult(
                ok=False,
                message="Could not parse AI plan. Try rephrasing the behavior instructions.",
            )

        notes = [str(n).strip() for n in (plan.get("notes") or []) if str(n).strip()]
        if plan.get("refuse"):
            reason = str(plan.get("refuse_reason") or "Instructions would change factual knowledge.").strip()
            return BehaviorEditResult(
                ok=False,
                message=f"Refused: {reason}",
                notes=notes,
            )

        targets = plan.get("targets")
        if not isinstance(targets, list) or not targets:
            return BehaviorEditResult(
                ok=False,
                message="No behavior files were identified for update. Try being more specific.",
                notes=notes,
            )

        pending: list[tuple[str, str, FileChangeSummary]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "").strip()
            if filename not in BEHAVIOR_FILES or filename in PROTECTED_FILES:
                notes.append(f"Skipped non-behavior / protected file: {filename or '(empty)'}")
                continue

            intent = str(item.get("intent") or "").strip()
            planned_changes = item.get("changes") or []

            try:
                merged_plan = await self._gemini.merge_behavior_file(
                    instructions=text,
                    filename=filename,
                    current_content=current.get(filename) or "",
                    merge_intent=intent,
                )
            except Exception as exc:
                notes.append(f"{filename}: merge failed ({exc})")
                continue

            if not merged_plan:
                notes.append(f"{filename}: could not parse merged content.")
                continue

            new_content = merged_plan.get("new_content")
            if not isinstance(new_content, str) or not new_content.strip():
                notes.append(f"Skipped {filename}: empty merged content.")
                continue

            merged = new_content.strip() + "\n"
            if merged == (current.get(filename) or "").strip() + "\n":
                notes.append(f"{filename}: no content change after merge.")
                continue

            # Prefer per-file merge change list; fall back to target plan list.
            change_raw = merged_plan.get("changes") or planned_changes
            change = _parse_change_entries(change_raw, filename)
            pending.append((filename, merged, change))

        if not pending:
            return BehaviorEditResult(
                ok=False,
                message="Analysis finished but nothing to write. Instructions may already match current behavior.",
                notes=notes,
            )

        backup_path = self._backup_files([name for name, _, _ in pending])
        modified: list[str] = []
        change_summaries: list[FileChangeSummary] = []

        try:
            for filename, merged, change in pending:
                (self._root / filename).write_text(merged, encoding="utf-8")
                modified.append(filename)
                change_summaries.append(change)
        except OSError as exc:
            log_event(logger, logging.ERROR, "behavior_write_failed", error=str(exc))
            self._restore_from_backup(backup_path, [name for name, _, _ in pending])
            return BehaviorEditResult(
                ok=False,
                message=f"Write failed and backup restore attempted: {exc}",
                backup_dir=str(backup_path.relative_to(self._root)),
            )

        load_knowledge(force=True)
        log_event(
            logger,
            logging.INFO,
            "behavior_updated",
            files=modified,
            backup=str(backup_path),
        )

        return BehaviorEditResult(
            ok=True,
            message="ok",
            modified_files=modified,
            backup_dir=str(backup_path.relative_to(self._root)),
            changes=change_summaries,
            notes=notes,
        )
