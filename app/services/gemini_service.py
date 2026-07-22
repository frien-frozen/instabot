"""Google Gemini AI reply generation service."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings
from app.gemini_config import (
    DEFAULT_GEMINI_MODEL,
    get_gemini_api_endpoint,
    get_gemini_sdk_version,
    normalize_gemini_model,
)
from app.knowledge import get_system_prompt
from app.utils.comment_intent import COMMENT_INTENTS
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

# System prompt is built from knowledge/*.md (see app.knowledge.load_knowledge).
DEFAULT_SYSTEM_PROMPT = ""  # populated at startup via load_knowledge(); use get_system_prompt()
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT


class GeminiAPIError(Exception):
    """Raised when the Gemini API returns an error."""

    def __init__(self, message: str, *, model: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.model = model
        self.status_code = status_code


class GeminiService:
    """
    Reusable Gemini AI service for generating Instagram comment replies.

    Designed for future extensibility: personality presets, memory context,
    and human-approval workflows can inject additional prompt segments.
    """

    def __init__(self, settings: Settings, *, api_key: str | None = None, model: str | None = None) -> None:
        self._settings = settings
        key = (api_key or settings.gemini_api_key).strip()
        self._client = genai.Client(api_key=key)
        self._configured_model = (model or settings.gemini_model).strip()
        self._model = normalize_gemini_model(self._configured_model)

        if self._model != self._configured_model:
            log_event(
                logger,
                logging.WARNING,
                "gemini_model_normalized",
                configured_model=self._configured_model,
                resolved_model=self._model,
            )

    @property
    def model(self) -> str:
        return self._model

    @property
    def configured_model(self) -> str:
        return self._configured_model

    @property
    def api_endpoint(self) -> str:
        return get_gemini_api_endpoint()

    @property
    def sdk_version(self) -> str:
        return get_gemini_sdk_version()

    def log_startup_diagnostics(self) -> None:
        """Emit structured logs for Gemini SDK and model configuration."""
        log_event(
            logger,
            logging.INFO,
            "gemini_startup",
            sdk_version=self.sdk_version,
            model=self._model,
            configured_model=self._configured_model,
            api_endpoint=self.api_endpoint,
        )

    async def validate_model(self) -> str | None:
        """
        Verify the configured model with a minimal test prompt.

        Retries transient 503/429 overload errors a few times.
        Returns the test reply on success, or None if validation fails.
        """
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents="Reply with exactly: ok",
                    config=types.GenerateContentConfig(
                        max_output_tokens=10,
                        temperature=0,
                    ),
                )
                reply = (response.text or "").strip()
                if not reply:
                    log_event(
                        logger,
                        logging.ERROR,
                        "gemini_validation_empty_reply",
                        model=self._model,
                        api_endpoint=self.api_endpoint,
                        attempt=attempt,
                    )
                    return None

                log_event(
                    logger,
                    logging.INFO,
                    "gemini_validation_ok",
                    model=self._model,
                    sdk_version=self.sdk_version,
                    api_endpoint=self.api_endpoint,
                    test_reply=reply,
                    attempt=attempt,
                )
                return reply
            except Exception as exc:
                last_error = exc
                retryable = self._is_retryable_error(exc)
                log_event(
                    logger,
                    logging.WARNING if retryable else logging.ERROR,
                    "gemini_validation_failed",
                    model=self._model,
                    sdk_version=self.sdk_version,
                    api_endpoint=self.api_endpoint,
                    error=str(exc),
                    attempt=attempt,
                    retryable=retryable,
                )
                if not retryable or attempt >= 3:
                    break
                await asyncio.sleep(2**attempt)

        if last_error is not None:
            log_event(
                logger,
                logging.ERROR,
                "gemini_validation_exhausted",
                model=self._model,
                error=str(last_error),
            )
        return None

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        text = str(exc).upper()
        return any(
            token in text
            for token in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED")
        )

    async def _generate_with_model(
        self,
        model: str,
        user_content: str,
        system_prompt: str,
        *,
        max_output_tokens: int = 100,
    ) -> str:
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.9,
                max_output_tokens=max_output_tokens,
            ),
        )
        reply = (response.text or "").strip()
        if not reply:
            raise GeminiAPIError(
                f"Model {model!r} returned an empty reply",
                model=model,
            )
        return reply

    async def generate_reply(
        self,
        comment_text: str,
        *,
        personality_override: str | None = None,
        memory_context: str | None = None,
        profile_context: str | None = None,
        post_context: str | None = None,
        max_output_tokens: int = 100,
    ) -> str:
        """
        Generate a natural reply for the given comment or DM text.

        Args:
            comment_text: The latest user message to reply to.
            personality_override: Optional custom system prompt.
            memory_context: Optional prior conversation turns (DM history).
            profile_context: Optional Instagram profile summary for warmth.
            post_context: Optional full post/campaign context package for comments.
            max_output_tokens: Cap on reply length (DMs use a higher limit).

        Returns:
            Generated reply text, stripped of surrounding whitespace.
        """
        system_prompt = get_system_prompt(override=personality_override)

        blocks: list[str] = []
        if post_context:
            blocks.append(post_context)
        if profile_context:
            blocks.append(f"About the person you're talking to:\n{profile_context}")
        if memory_context:
            blocks.append(f"Conversation so far:\n{memory_context}")
        blocks.append(f"Latest user message:\n{comment_text}")
        blocks.append(
            "Reply rules for THIS message: max 2 short sentences, one idea only, "
            "no paragraphs or sales stories. Chat like Instagram, not an article."
        )
        user_content = "\n\n".join(blocks)

        log_event(
            logger,
            logging.INFO,
            "gemini_prompt",
            model=self._model,
            comment_text=comment_text,
            system_prompt=system_prompt,
            has_memory=bool(memory_context),
            memory_turns=len(memory_context.splitlines()) if memory_context else 0,
            has_profile=bool(profile_context),
            has_post_context=bool(post_context),
        )

        last_error: GeminiAPIError | None = None
        for attempt in range(1, 4):
            try:
                reply = await self._generate_with_model(
                    self._model,
                    user_content,
                    system_prompt,
                    max_output_tokens=max_output_tokens,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "gemini_response",
                    model=self._model,
                    reply_text=reply,
                    attempt=attempt,
                )
                return reply

            except Exception as exc:
                retryable = self._is_retryable_error(exc)
                log_event(
                    logger,
                    logging.WARNING if retryable else logging.ERROR,
                    "gemini_api_error",
                    model=self._model,
                    attempt=attempt,
                    error=str(exc),
                    retryable=retryable,
                )

                if not retryable:
                    raise GeminiAPIError(str(exc), model=self._model) from exc

                last_error = GeminiAPIError(str(exc), model=self._model)
                if attempt < 3:
                    wait = 2**attempt
                    log_event(
                        logger,
                        logging.INFO,
                        "gemini_retry_wait",
                        model=self._model,
                        wait_seconds=wait,
                        attempt=attempt,
                    )
                    await asyncio.sleep(wait)

        if last_error:
            raise last_error
        raise GeminiAPIError("Gemini request failed", model=self._model)

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            match = _JSON_BLOCK.search(raw)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None

    async def classify_comment_intent(
        self,
        comment_text: str,
        *,
        post_context: str | None = None,
    ) -> str:
        """
        Classify an Instagram comment into a single intent label.

        Returns one of COMMENT_INTENTS. Defaults to Question on failure.
        """
        allowed = ", ".join(COMMENT_INTENTS)
        system = (
            "You classify Instagram comments for a medical clinic page.\n"
            f"Return ONLY valid JSON: {{\"intent\": \"<one of: {allowed}>\"}}\n\n"
            "Rules:\n"
            "- Supportive = praise, emoji reactions (🔥❤️👏), Mashallah, Zo'r, Gap yo'q, Respect — NOT leads\n"
            "- Greeting = hello / salom only\n"
            "- Question = asks for info\n"
            "- Consultation Inquiry = wants consultation / qabul\n"
            "- Operation Inquiry = surgery / operatsiya interest\n"
            "- Lead Magnet Trigger = commenting a CTA keyword from the caption\n"
            "- Lead = clearly wants to book / leave contacts\n"
            "- Complaint = unhappy / negative\n"
            "- Spam = promo / irrelevant scrape\n"
        )
        blocks: list[str] = []
        if post_context:
            blocks.append(post_context)
        blocks.append(f"Comment to classify:\n{comment_text}")
        blocks.append('Respond with JSON only, e.g. {"intent":"Supportive"}')

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents="\n\n".join(blocks),
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.1,
                    max_output_tokens=40,
                ),
            )
            parsed = self._parse_json_object(response.text or "")
            intent = str((parsed or {}).get("intent") or "").strip()
            for label in COMMENT_INTENTS:
                if intent.lower() == label.lower():
                    log_event(
                        logger,
                        logging.INFO,
                        "comment_intent_classified",
                        intent=label,
                        comment_text=comment_text,
                    )
                    return label
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "comment_intent_classify_failed",
                error=str(exc),
                comment_text=comment_text,
            )

        return "Question"

    async def extract_lead(
        self,
        *,
        conversation_history: str,
        latest_message: str,
        instagram_username: str | None = None,
    ) -> dict[str, Any]:
        """
        Extract structured lead fields from a DM thread.

        Gemini only returns JSON — never writes to Sheets.
        """
        system = (
            "You extract patient lead data from an Instagram DM with a clinic admin assistant.\n"
            "Return ONLY valid JSON with this shape:\n"
            "{\n"
            '  "lead_collected": true|false,\n'
            '  "name": "string or empty",\n'
            '  "age": "string or empty",\n'
            '  "marital_status": "Married|Single|empty",\n'
            '  "phone": "string or empty",\n'
            '  "city": "string or empty",\n'
            '  "problem": "short clinical summary",\n'
            '  "problem_duration": "string or empty",\n'
            '  "category": "Hormonal|Urology|Operation|Monitoring|Unknown",\n'
            '  "service": "Day Consultation|Evening Consultation|Monthly Monitoring|Operation|Online Consultation|Unknown",\n'
            '  "preferred_date": "Morning|Daytime|Evening|or empty",\n'
            '  "conversation_summary": "2-4 short sentences for the clinic admin"\n'
            "}\n\n"
            "Set lead_collected=true ONLY when name AND age AND marital_status AND city "
            "AND phone AND problem are all present.\n"
            "If anything required is missing, lead_collected=false and still fill what you know.\n"
            "conversation_summary MUST include age, marital status, problem duration, and "
            "preferred time when known — concise for a human admin (Sherzod), not raw chat.\n"
            "Do not invent phone numbers, names, age, or other fields."
        )
        user_content = (
            f"Instagram username: {instagram_username or 'unknown'}\n\n"
            f"Conversation:\n{conversation_history or '(none)'}\n\n"
            f"Latest user message:\n{latest_message}"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.1,
                    max_output_tokens=350,
                ),
            )
            parsed = self._parse_json_object(response.text or "")
            if not parsed:
                log_event(logger, logging.WARNING, "lead_extract_parse_failed", raw=response.text)
                return {"lead_collected": False}

            # Fold intake fields the CRM schema does not store as columns into the summary.
            summary = str(parsed.get("conversation_summary") or "").strip()
            extras: list[str] = []
            age = str(parsed.get("age") or "").strip()
            marital = str(parsed.get("marital_status") or "").strip()
            duration = str(parsed.get("problem_duration") or "").strip()
            if age and "age" not in summary.lower() and "yosh" not in summary.lower():
                extras.append(f"Age: {age}")
            if marital and "marital" not in summary.lower() and "oilaviy" not in summary.lower():
                extras.append(f"Marital status: {marital}")
            if duration and "duration" not in summary.lower() and "davomiylik" not in summary.lower():
                extras.append(f"Problem duration: {duration}")
            if extras:
                parsed["conversation_summary"] = (
                    f"{summary} {' '.join(extras)}".strip() if summary else " ".join(extras)
                )

            # Enforce minimum intake before export (conversation policy).
            required_ok = all(
                str(parsed.get(key) or "").strip()
                for key in ("name", "age", "marital_status", "city", "phone", "problem")
            )
            parsed["lead_collected"] = bool(parsed.get("lead_collected")) and required_ok

            log_event(
                logger,
                logging.INFO,
                "lead_extract_ok",
                lead_collected=bool(parsed.get("lead_collected")),
                has_name=bool(parsed.get("name")),
                has_age=bool(age),
                has_marital_status=bool(marital),
                has_city=bool(parsed.get("city")),
                has_phone=bool(parsed.get("phone")),
                has_problem=bool(parsed.get("problem")),
            )
            return parsed
        except Exception as exc:
            log_event(logger, logging.WARNING, "lead_extract_failed", error=str(exc))
            return {"lead_collected": False}

    async def plan_behavior_targets(
        self,
        *,
        instructions: str,
        current_files: dict[str, str],
        editable_files: list[str],
        protected_files: list[str],
    ) -> dict[str, Any] | None:
        """
        Decide which editable behavior files an admin instruction should affect.

        Does not return full rewritten file bodies — only targets + change intents.
        """
        editable = ", ".join(editable_files)
        protected = ", ".join(protected_files)
        file_blocks: list[str] = []
        for name in editable_files:
            body = (current_files.get(name) or "").strip() or "(empty file)"
            # Keep prompt smaller: truncate very long files for targeting only.
            if len(body) > 4000:
                body = body[:4000] + "\n…(truncated for targeting)"
            file_blocks.append(f"### {name}\n```markdown\n{body}\n```")

        system = (
            "You analyze admin instructions for a clinic Instagram assistant.\n"
            "Return ONLY valid JSON (no markdown fences):\n"
            "{\n"
            '  "refuse": false,\n'
            '  "refuse_reason": "",\n'
            '  "notes": ["optional notes"],\n'
            '  "targets": [\n'
            "    {\n"
            '      "filename": "booking.md",\n'
            '      "intent": "short merge goal for this file",\n'
            '      "changes": [\n'
            '        {"type": "added|removed|updated", "rule": "short description"}\n'
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            f"Editable files ONLY: {editable}\n"
            f"Protected factual files (never target): {protected}\n"
            "If the admin asks to change prices, services, doctor bio, website, labs, "
            "or other factual clinic data, set refuse=true.\n"
            "policies.md: behavioral policies only; keep medical/safety non-negotiables.\n"
            "Pick ONLY files that need changes. Do not invent clinic facts."
        )
        user_content = (
            f"Administrator instructions:\n{instructions.strip()}\n\n"
            "Current editable behavior files:\n\n"
            + "\n\n".join(file_blocks)
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
                max_output_tokens=2000,
            ),
        )
        parsed = self._parse_json_object(response.text or "")
        if not parsed:
            log_event(
                logger,
                logging.WARNING,
                "behavior_targets_parse_failed",
                raw=(response.text or "")[:500],
            )
            return None
        log_event(
            logger,
            logging.INFO,
            "behavior_targets_ok",
            target_count=len(parsed.get("targets") or [])
            if isinstance(parsed.get("targets"), list)
            else 0,
            refuse=bool(parsed.get("refuse")),
        )
        return parsed

    async def merge_behavior_file(
        self,
        *,
        instructions: str,
        filename: str,
        current_content: str,
        merge_intent: str,
    ) -> dict[str, Any] | None:
        """Intelligently merge admin instructions into one behavior markdown file."""
        system = (
            "You merge administrator behavior instructions into ONE markdown knowledge file.\n"
            "Return ONLY valid JSON (no markdown fences):\n"
            "{\n"
            '  "new_content": "FULL merged markdown for this file",\n'
            '  "changes": [\n'
            '    {"type": "added|removed|updated", "rule": "short description"}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- MERGE — do not wipe unrelated existing rules.\n"
            "- Keep useful headings and structure when possible.\n"
            "- For policies.md: preserve medical/safety non-negotiables; "
            "only adjust behavioral policy wording.\n"
            "- Never add prices, service catalogs, doctor biography, website URLs, "
            "or laboratory facts.\n"
            "- new_content must be the complete file after merge."
        )
        user_content = (
            f"File: {filename}\n"
            f"Merge intent: {merge_intent or '(from instructions)'}\n\n"
            f"Administrator instructions:\n{instructions.strip()}\n\n"
            f"Current file contents:\n```markdown\n{(current_content or '').strip() or '(empty)'}\n```"
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
                max_output_tokens=4096,
            ),
        )
        parsed = self._parse_json_object(response.text or "")
        if not parsed:
            log_event(
                logger,
                logging.WARNING,
                "behavior_merge_parse_failed",
                filename=filename,
                raw=(response.text or "")[:500],
            )
            return None
        return parsed
