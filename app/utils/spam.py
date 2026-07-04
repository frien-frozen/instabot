"""Spam detection heuristics for incoming Instagram comments."""

from __future__ import annotations

import re
import unicodedata

# Common spam patterns (case-insensitive)
_SPAM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(follow\s+me|check\s+my\s+profile|dm\s+me\s+for)"),
    re.compile(r"(?i)(click\s+the\s+link|free\s+followers|buy\s+followers)"),
    re.compile(r"(?i)(crypto|bitcoin|nft)\s+(giveaway|airdrop)"),
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"(.)\1{6,}"),  # same character repeated 7+ times
]

# Emoji detection via Unicode categories
_EMOJI_CATEGORIES = frozenset({"So", "Sk", "Sm"})


def _is_emoji_char(char: str) -> bool:
    """Return True if the character is an emoji or emoji modifier."""
    if not char.strip():
        return True
    category = unicodedata.category(char)
    if category in _EMOJI_CATEGORIES:
        return True
    code = ord(char)
    # Supplementary emoji ranges
    return (
        0x1F300 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0xFE00 <= code <= 0xFE0F
    )


def is_emoji_only(text: str) -> bool:
    """Return True if the comment contains only emojis and whitespace."""
    stripped = text.strip()
    if not stripped:
        return True
    return all(_is_emoji_char(c) for c in stripped)


def is_single_character(text: str) -> bool:
    """Return True if the comment is a single non-whitespace character."""
    stripped = text.strip()
    return len(stripped) == 1


def has_repeated_characters(text: str, threshold: int = 5) -> bool:
    """
    Return True if the comment is dominated by repeated characters.

    Example: 'hahahahaha' or '!!!!!!!!!!'
    """
    stripped = text.strip()
    if len(stripped) < threshold:
        return False
    # Check if a single character makes up most of the string
    for char in set(stripped):
        if stripped.count(char) >= threshold and stripped.count(char) / len(stripped) > 0.7:
            return True
    return False


def matches_spam_pattern(text: str) -> bool:
    """Return True if the comment matches known spam patterns."""
    return any(pattern.search(text) for pattern in _SPAM_PATTERNS)


def is_spam(text: str) -> tuple[bool, str | None]:
    """
    Evaluate whether a comment should be ignored as spam.

    Returns:
        (is_spam, reason) — reason is None when the comment is not spam.
    """
    if not text or not text.strip():
        return True, "empty_comment"

    if is_emoji_only(text):
        return True, "emoji_only"

    if is_single_character(text):
        return True, "single_character"

    if has_repeated_characters(text):
        return True, "repeated_characters"

    if matches_spam_pattern(text):
        return True, "spam_pattern"

    return False, None
