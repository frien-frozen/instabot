"""Format Instagram user profile data for Gemini prompts."""

from __future__ import annotations

from typing import Any


def format_profile_context(profile: dict[str, Any] | None) -> str:
    """Turn Graph API profile fields into a short warm-context block."""
    if not profile:
        return ""

    lines: list[str] = []

    name = (profile.get("name") or "").strip()
    username = (profile.get("username") or "").strip()
    if name:
        lines.append(f"Name: {name}")
    if username:
        lines.append(f"Username: @{username.lstrip('@')}")

    biography = (profile.get("biography") or profile.get("bio") or "").strip()
    if biography:
        lines.append(f"Bio: {biography}")

    follower_count = profile.get("follower_count")
    if follower_count is not None:
        lines.append(f"Followers: {follower_count}")

    if profile.get("is_verified_user"):
        lines.append("Verified account: yes")

    if profile.get("is_user_follow_business") is True:
        lines.append("They follow your account")

    if profile.get("is_business_follow_user") is True:
        lines.append("You follow them")

    return "\n".join(lines)
