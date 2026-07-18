"""Comment intent classification and supportive reply helpers."""

from __future__ import annotations

import random
import re
import unicodedata

from app.utils.spam import is_emoji_only

COMMENT_INTENTS = (
    "Supportive",
    "Question",
    "Lead",
    "Complaint",
    "Spam",
    "Operation Inquiry",
    "Consultation Inquiry",
    "Lead Magnet Trigger",
    "Greeting",
)

# Short reaction phrases (Uzbek / English / Russian) — appreciation only.
_SUPPORTIVE_PHRASES: frozenset[str] = frozenset(
    {
        "mashallah",
        "mashaallah",
        "masha'allah",
        "машаллах",
        "zo'r",
        "zor",
        "зор",
        "gap yo'q",
        "gap yoq",
        "гап йўқ",
        "respect",
        "barakalla",
        "baraka",
        "omad",
        "омд",
        "nice",
        "super",
        "афзо",
        "алака",
        "alo",
        "a'lo",
        "аъло",
        "top",
        "🔥",
        "❤️",
        "❤",
        "👏",
        "💪",
        "🤩",
        "😍",
        "🥳",
        "👍",
        "🙌",
        "💯",
        "✨",
        "🙏",
        "♥️",
        "💕",
        "😊",
        "👍🏻",
        "👍🏼",
        "👍🏽",
        "👍🏾",
        "👍🏿",
    }
)

# Exact / prefix emoji → reply examples from product brief.
_EMOJI_REPLIES: dict[str, tuple[str, ...]] = {
    "🔥": ("Rahmat! 🙌❤️", "Rahmat! 🔥", "Tashakkur! 🔥"),
    "👏": ("Katta rahmat! 😊", "Rahmat! 👏", "Tashakkur! 🙌"),
    "😍": ("Juda xursandmiz ❤️", "Rahmat! 😍", "Juda xursandmiz 😊"),
    "🤩": ("Rahmat! 😊", "Tashakkur! 🤩", "Rahmat! 🙌"),
    "💪": ("Tashakkur aka! 🔥", "Rahmat! 💪", "Tashakkur! 🔥"),
    "❤️": ("Rahmat ❤️", "Rahmat! ❤️", "Katta rahmat ❤️"),
    "❤": ("Rahmat ❤️", "Rahmat! ❤️"),
    "♥️": ("Rahmat ❤️", "Rahmat! ❤️"),
    "🥳": ("Rahmat! 😊", "Tashakkur! 🥳"),
    "👍": ("Rahmat! 🙌", "Tashakkur! 👍"),
    "🙌": ("Rahmat! 🙌", "Tashakkur! ❤️"),
    "💯": ("Rahmat! 💯", "Tashakkur! 🙌"),
    "🙏": ("Rahmat! 🙏", "Tashakkur! ❤️"),
}

_PHRASE_REPLIES: dict[str, tuple[str, ...]] = {
    "mashallah": ("Rahmat! 😊", "Tashakkur! 🙌"),
    "mashaallah": ("Rahmat! 😊",),
    "masha'allah": ("Rahmat! 😊",),
    "машаллах": ("Rahmat! 😊",),
    "zo'r": ("Rahmat! Harakat qilamiz 🙌", "Rahmat! 🙌"),
    "zor": ("Rahmat! Harakat qilamiz 🙌",),
    "зор": ("Rahmat! Harakat qilamiz 🙌",),
    "gap yo'q": ("Rahmat aka ❤️", "Rahmat! ❤️"),
    "gap yoq": ("Rahmat aka ❤️",),
    "гап йўқ": ("Rahmat aka ❤️",),
    "respect": ("Tashakkur! 🤝", "Rahmat! 🤝"),
    "barakalla": ("Rahmat! 🙌", "Tashakkur! ❤️"),
    "baraka": ("Rahmat! 🙌",),
    "omad": ("Rahmat! 😊", "Tashakkur! 🙌"),
}

_DEFAULT_SUPPORTIVE_REPLIES: tuple[str, ...] = (
    "Rahmat! ❤️",
    "Tashakkur! 🙌",
    "Juda xursandmiz 😊",
    "Rahmat! 😊",
    "Katta rahmat! 🙌",
)

_GREETING_PHRASES: frozenset[str] = frozenset(
    {
        "salom",
        "assalomu alaykum",
        "assalom",
        "hello",
        "hi",
        "hey",
        "привет",
        "здравствуйте",
    }
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _strip_emoji_modifiers(text: str) -> str:
    # Drop variation selectors / ZWJ so ❤️ and ❤ match the same bucket.
    return "".join(c for c in text if unicodedata.category(c) not in {"Mn", "Me"} and ord(c) != 0x200D)


def is_clearly_supportive(text: str) -> bool:
    """Fast rule: short praise / reaction with no question or lead signal."""
    raw = (text or "").strip()
    if not raw:
        return False

    # Questions are never pure supportive.
    if "?" in raw or "؟" in raw:
        return False

    normalized = _normalize(raw)
    if normalized in _SUPPORTIVE_PHRASES:
        return True

    # Emoji-only reactions (🔥, ❤️❤️, 👏👏).
    if is_emoji_only(raw) and len(raw) <= 24:
        return True

    # Short phrase + optional emoji: "Zo'r 🔥"
    stripped = re.sub(
        r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F❤️❤♥️]+",
        "",
        raw,
    ).strip()
    if stripped and _normalize(stripped) in _SUPPORTIVE_PHRASES and len(raw) <= 40:
        return True

    return False


def is_clearly_greeting(text: str) -> bool:
    normalized = _normalize(text)
    if normalized in _GREETING_PHRASES:
        return True
    return any(normalized.startswith(f"{g} ") or normalized.startswith(f"{g}!") for g in _GREETING_PHRASES)


def classify_comment_intent_fast(text: str) -> str | None:
    """
    Rule-based intent when confidence is high.

    Returns None when Gemini should decide.
    """
    raw = (text or "").strip()
    if not raw:
        return "Spam"

    if is_clearly_supportive(raw):
        return "Supportive"

    if is_clearly_greeting(raw) and len(raw) <= 40 and "?" not in raw:
        return "Greeting"

    return None


def pick_supportive_reply(text: str) -> str:
    """Pick a natural appreciation reply — never sells or asks for DM."""
    raw = (text or "").strip()
    normalized = _normalize(raw)

    if normalized in _PHRASE_REPLIES:
        return random.choice(_PHRASE_REPLIES[normalized])

    stripped_phrase = _normalize(
        re.sub(
            r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F❤️❤♥️]+",
            "",
            raw,
        )
    )
    if stripped_phrase in _PHRASE_REPLIES:
        return random.choice(_PHRASE_REPLIES[stripped_phrase])

    cleaned = _strip_emoji_modifiers(raw)
    for emoji, replies in _EMOJI_REPLIES.items():
        if emoji in cleaned or emoji in raw:
            return random.choice(replies)

    return random.choice(_DEFAULT_SUPPORTIVE_REPLIES)


def supportive_reply_instructions() -> str:
    return (
        "COMMENT INTENT: Supportive\n"
        "This is engagement / appreciation only — NOT a lead.\n"
        "Reply with warm thanks only (1 short line).\n"
        "NEVER sell, NEVER recommend consultation, NEVER ask to DM, "
        "NEVER collect leads or phone numbers."
    )


def intent_reply_instructions(intent: str) -> str:
    if intent == "Supportive":
        return supportive_reply_instructions()
    if intent == "Greeting":
        return (
            "COMMENT INTENT: Greeting\n"
            "Greet warmly in 1 short line. Do not pitch services unless they ask."
        )
    if intent == "Spam":
        return "COMMENT INTENT: Spam\nDo not engage meaningfully; keep minimal or skip."
    if intent == "Complaint":
        return (
            "COMMENT INTENT: Complaint\n"
            "Acknowledge calmly, apologize briefly, invite them to DM for help. No defensiveness."
        )
    if intent == "Question":
        return (
            "COMMENT INTENT: Question\n"
            "Answer briefly from knowledge. Sensitive medical detail → invite DM."
        )
    if intent == "Consultation Inquiry":
        return (
            "COMMENT INTENT: Consultation Inquiry\n"
            "Briefly confirm consultation options; invite DM to book. Keep public reply short."
        )
    if intent == "Operation Inquiry":
        return (
            "COMMENT INTENT: Operation Inquiry\n"
            "Do not diagnose. Invite DM so admin/Sherzod can help with surgery questions."
        )
    if intent == "Lead" or intent == "Lead Magnet Trigger":
        return (
            f"COMMENT INTENT: {intent}\n"
            "Acknowledge and guide toward the intended CTA / DM follow-up. Keep public reply short."
        )
    return f"COMMENT INTENT: {intent}\nReply naturally and briefly."
