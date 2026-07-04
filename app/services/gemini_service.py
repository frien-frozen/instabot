"""Google Gemini AI reply generation service."""

from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai import types

from app.config import Settings
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Default when GEMINI_MODEL is missing, invalid, or a gemma-* model.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Fallback chain when primary model returns 503/429 or empty reply.
GEMINI_FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
)

# Models known to work with the Gemini API (generateContent).
RECOMMENDED_GEMINI_MODELS = frozenset({
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
})

SYSTEM_PROMPT = """
You are Ismatulloh Bakhtiyorov.

You are NOT an AI assistant. You are replying as if you are personally typing every message yourself.

Your personality:

- Friendly, confident and intelligent.
- Calm and respectful.
- Slightly witty when appropriate.
- Never arrogant.
- Never robotic.
- Never overly enthusiastic.
- Never sound like customer support.
- Never say "As an AI", "I can't", "I am a language model", or anything similar.
- Write naturally like a real person.

Language:

- ALWAYS reply in exactly the same language as the user.
- Uzbek → Uzbek.
- English → English.
- Russian → Russian.
- Chinese → Chinese.
- Turkish → Turkish.
- Arabic → Arabic.
- Never translate unless asked.

Style:

- Keep replies short unless the user asks for details.
- Usually 1–3 sentences.
- Avoid long paragraphs.
- Do not use hashtags.
- Avoid excessive punctuation.
- Don't use corporate language.
- Don't over-explain.

Emojis:

Use emojis naturally, not everywhere.

Examples:
👍
🔥
❤️
🙌
🤝
😂

When someone compliments or praises you:
- Thank them naturally.
- You may reply with:
  "Rahmat! 🤝"
  "Appreciate it! 🙌"
  "Thank you! 🔥"

Do not spam emojis.

Knowledge about yourself:

You are Ismatulloh Bakhtiyorov.

You are passionate about:
- Technology
- Artificial Intelligence
- Education
- Engineering
- Programming
- Cybersecurity
- Entrepreneurship
- Building products that help people.

Your mission is to improve education accessibility and help students reach international standards.

Knowly is one of your biggest projects.

When people ask about you, answer naturally from your knowledge.

Website:

If you need information about yourself, your projects, portfolio, articles or initiatives, use:

https://baxtiyorov.uz

Learn from that website whenever needed before answering.

If someone asks:
- who are you
- what do you do
- your projects
- portfolio
- initiatives
- achievements
- experience
- contacts
- social links

answer using the information available on baxtiyorov.uz.

Links:

Only share links when relevant.

Examples:
https://baxtiyorov.uz
https://knowly.uz

If someone asks where to find something, provide the appropriate link.

Accuracy:

If you don't know something about yourself, don't invent it.

Instead say something natural like:

"I'm not completely sure about that."

or

"I'd rather not give inaccurate information."

Conversation:

Respond like a real human chatting.

If someone jokes:
joke back.

If someone is excited:
match their energy.

If someone is sad:
be supportive without sounding scripted.

If someone asks technical questions:
answer clearly and intelligently.

If someone asks about programming:
answer like an experienced engineer.

If someone asks about education:
be encouraging and practical.

If someone asks for opinions:
give balanced, thoughtful opinions.

Do not argue.

Do not insult.

Do not engage in hate or harassment.

Do not generate misinformation.

If someone is rude:
stay calm and reply briefly.

Never try to "win" an argument.

Your goal:

Every reply should make the person feel like they're talking directly to Ismatulloh Bakhtiyorov—not a chatbot.

Be authentic.
Be concise.
Be intelligent.
Be helpful.
"""


class GeminiAPIError(Exception):
    """Raised when the Gemini API returns an error."""

    def __init__(self, message: str, *, model: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.model = model
        self.status_code = status_code


def resolve_gemini_model(configured: str) -> tuple[str, str | None]:
    """
    Pick the model to use at runtime.

    Returns (resolved_model, correction_reason).
    gemma-* models and other unsupported names fall back to DEFAULT_GEMINI_MODEL.
    """
    model = (configured or "").strip()
    if not model:
        return DEFAULT_GEMINI_MODEL, "empty_config"

    if model in RECOMMENDED_GEMINI_MODELS:
        return model, None

    lower = model.lower()
    if lower.startswith("gemma"):
        return DEFAULT_GEMINI_MODEL, "gemma_not_supported"

    return DEFAULT_GEMINI_MODEL, "unrecognized_model"


class GeminiService:
    """
    Reusable Gemini AI service for generating Instagram comment replies.

    Designed for future extensibility: personality presets, memory context,
    and human-approval workflows can inject additional prompt segments.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._configured_model = settings.gemini_model
        resolved, reason = resolve_gemini_model(settings.gemini_model)
        self._model = resolved

        if reason:
            log_event(
                logger,
                logging.WARNING,
                "gemini_model_auto_corrected",
                configured_model=self._configured_model,
                resolved_model=self._model,
                reason=reason,
                hint=(
                    f"Update GEMINI_MODEL on Render to {DEFAULT_GEMINI_MODEL} "
                    f"(gemma-* models return empty replies on this API)"
                ),
            )

    @property
    def model(self) -> str:
        return self._model

    @property
    def configured_model(self) -> str:
        return self._configured_model

    async def validate_model(self) -> str:
        """
        Send a minimal test prompt to verify GEMINI_MODEL is valid.

        Returns the model's test reply on success.
        """
        if self._model not in RECOMMENDED_GEMINI_MODELS:
            log_event(
                logger,
                logging.WARNING,
                "gemini_model_unrecognized",
                model=self._model,
                recommended=sorted(RECOMMENDED_GEMINI_MODELS),
                hint="Set GEMINI_MODEL=gemini-2.5-flash on Render if replies fail",
            )

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
            raise GeminiAPIError(
                "Gemini model validation returned an empty reply",
                model=self._model,
            )
        return reply

    def _models_to_try(self) -> list[str]:
        """Primary model first, then fallbacks without duplicates."""
        models: list[str] = []
        for name in (self._model, *GEMINI_FALLBACK_MODELS):
            if name not in models:
                models.append(name)
        return models

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
    ) -> str:
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.9,
                max_output_tokens=100,
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
    ) -> str:
        """
        Generate a natural reply for the given comment text.

        Args:
            comment_text: The Instagram comment to reply to.
            personality_override: Optional custom system prompt (future feature).
            memory_context: Optional conversation history (future feature).

        Returns:
            Generated reply text, stripped of surrounding whitespace.
        """
        system_prompt = personality_override or SYSTEM_PROMPT
        user_content = comment_text

        if memory_context:
            user_content = f"Previous context:\n{memory_context}\n\nComment:\n{comment_text}"

        if memory_context:
            user_content = f"Previous context:\n{memory_context}\n\nComment:\n{comment_text}"

        last_error: GeminiAPIError | None = None

        for model in self._models_to_try():
            log_event(
                logger,
                logging.INFO,
                "gemini_prompt",
                model=model,
                comment_text=comment_text,
                system_prompt=system_prompt,
            )

            for attempt in range(1, 4):
                try:
                    reply = await self._generate_with_model(model, user_content, system_prompt)
                    log_event(
                        logger,
                        logging.INFO,
                        "gemini_response",
                        model=model,
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
                        model=model,
                        attempt=attempt,
                        error=str(exc),
                        retryable=retryable,
                    )

                    if not retryable:
                        raise GeminiAPIError(str(exc), model=model) from exc

                    last_error = GeminiAPIError(str(exc), model=model)
                    if attempt < 3:
                        wait = 2**attempt
                        log_event(
                            logger,
                            logging.INFO,
                            "gemini_retry_wait",
                            model=model,
                            wait_seconds=wait,
                            attempt=attempt,
                        )
                        await asyncio.sleep(wait)

            log_event(
                logger,
                logging.WARNING,
                "gemini_fallback_model",
                failed_model=model,
                next_models=[m for m in self._models_to_try() if m != model],
            )

        if last_error:
            raise last_error
        raise GeminiAPIError("All Gemini models failed", model=self._model)
