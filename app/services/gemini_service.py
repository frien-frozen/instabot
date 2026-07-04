"""Google Gemini AI reply generation service."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from app.config import Settings
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a human social media manager.

Reply naturally.

Your replies should never sound robotic.

Use different sentence structures every time.

Be humorous, clever, and slightly sarcastic.

Never insult anyone.

Never argue.

Never use offensive language.

If someone asks a question, answer correctly.

If someone compliments us, reply warmly.

If someone jokes, joke back.

Reply in exactly the same language as the comment.

Maximum 20 words.

Do not use hashtags.

Do not advertise unless asked."""


class GeminiService:
    """
    Reusable Gemini AI service for generating Instagram comment replies.

    Designed for future extensibility: personality presets, memory context,
    and human-approval workflows can inject additional prompt segments.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

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

        log_event(
            logger,
            logging.INFO,
            "gemini_prompt",
            model=self._model,
            comment_text=comment_text,
            system_prompt=system_prompt,
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.9,
                max_output_tokens=100,
            ),
        )

        reply = (response.text or "").strip()

        log_event(
            logger,
            logging.INFO,
            "gemini_response",
            model=self._model,
            reply_text=reply,
        )

        if not reply:
            raise ValueError("Gemini returned an empty reply")

        return reply
