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
            '  "phone": "string or empty",\n'
            '  "city": "string or empty",\n'
            '  "problem": "short clinical summary",\n'
            '  "category": "Hormonal|Urology|Operation|Monitoring|Unknown",\n'
            '  "service": "Day Consultation|Evening Consultation|Monthly Monitoring|Operation|Online Consultation|Unknown",\n'
            '  "preferred_date": "string or empty",\n'
            '  "conversation_summary": "2-4 short sentences for the clinic admin"\n'
            "}\n\n"
            "Set lead_collected=true ONLY when name AND phone AND problem are all present.\n"
            "If anything required is missing, lead_collected=false and still fill what you know.\n"
            "conversation_summary must be concise for a human admin (Sherzod) — not raw chat.\n"
            "Do not invent phone numbers or names."
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

            log_event(
                logger,
                logging.INFO,
                "lead_extract_ok",
                lead_collected=bool(parsed.get("lead_collected")),
                has_name=bool(parsed.get("name")),
                has_phone=bool(parsed.get("phone")),
                has_problem=bool(parsed.get("problem")),
            )
            return parsed
        except Exception as exc:
            log_event(logger, logging.WARNING, "lead_extract_failed", error=str(exc))
            return {"lead_collected": False}
