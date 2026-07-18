"""Build rich Instagram comment context and detect caption CTAs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.models.campaign import Campaign
from app.models.media import Media


@dataclass
class CampaignPlan:
    """
    In-memory campaign action (stored Campaign or auto-detected from caption).

    Not a Beanie document — safe to construct without Mongo init.
    """

    name: str
    media_id: Optional[str] = None
    goal: str = "lead_magnet"
    intent: str = "lead_magnet"
    trigger_keywords: list[str] = field(default_factory=list)
    public_reply: str = ""
    dm_text: str = ""
    dm_attachment_url: Optional[str] = None
    ask_name_after_dm: bool = True
    ask_phone_after_dm: bool = True
    offer_consultation: bool = True

    @classmethod
    def from_document(cls, row: Campaign) -> "CampaignPlan":
        return cls(
            name=row.name,
            media_id=row.media_id,
            goal=row.goal,
            intent=row.intent,
            trigger_keywords=list(row.trigger_keywords or []),
            public_reply=row.public_reply or "",
            dm_text=row.dm_text or "",
            dm_attachment_url=row.dm_attachment_url,
            ask_name_after_dm=row.ask_name_after_dm,
            ask_phone_after_dm=row.ask_phone_after_dm,
            offer_consultation=row.offer_consultation,
        )

# Quoted CTA: Kommentga "TIKLANISH" / «ANALIZ»
_QUOTED_CTA = re.compile(
    r"""(?ix)
    (?:komment(?:ga|da)?|comment(?:\s+below)?|коммент(?:у|е)?|yozing|напишите)
    [\s\S]{0,80}?
    [\"«“]([A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_\-]{3,40})[\"»”]
    """,
)
# Same quote, then "yozing" after (quote-first order)
_QUOTED_THEN_WRITE = re.compile(
    r"""(?ix)
    [\"«“]([A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_\-]{3,40})[\"»”]
    [\s\S]{0,40}?
    (?:yozing|напишите|write|comment)
    """,
)
# Unquoted ALL-CAPS keyword after komment / before yozing|DM
_CAPS_CTA = re.compile(
    r"""(?x)
    (?i:komment(?:ga|da)?|comment|yozing|deb\s+yoz)
    [\s\S]{0,100}?
    (?:^|[\n\r\s])
    (?-i:([A-ZА-ЯЁЎҚҒҲ]{3,30}))
    (?=[\s\n\r\"«».,!]|$)
    """,
)

_STOPWORDS = frozenset(
    {
        "komment",
        "kommentga",
        "comment",
        "yozing",
        "напишите",
        "write",
        "deb",
        "bilan",
        "uchun",
        "haqida",
        "keyin",
        "oldingi",
        "qollanma",
        "qo'llanma",
        "royxat",
        "ro'yxat",
        "yuboramiz",
        "yuboriladi",
        "analizlar",
        "malumot",
        "ma'lumot",
        "llanmani",
    }
)


def extract_caption_triggers(caption: str) -> list[str]:
    """Extract CTA keywords from a caption (e.g. TIKLANISH, ANALIZ)."""
    text = caption or ""
    found: list[str] = []

    def _add(word: str) -> None:
        cleaned = word.strip().strip("\"'«»“”'")
        if len(cleaned) < 3:
            return
        if cleaned.lower() in _STOPWORDS:
            return
        if cleaned.lower() in {w.lower() for w in found}:
            return
        found.append(cleaned)

    for pattern in (_QUOTED_CTA, _QUOTED_THEN_WRITE):
        for match in pattern.finditer(text):
            _add(match.group(1))

    # Caps fallback only if nothing quoted was found (avoids noise).
    if not found:
        for match in _CAPS_CTA.finditer(text):
            _add(match.group(1))

    return found


def classify_post_intent(caption: str, *, campaign: CampaignPlan | Campaign | None = None) -> str:
    if campaign and campaign.intent:
        return campaign.intent

    text = (caption or "").lower()
    if extract_caption_triggers(caption):
        return "lead_magnet"
    if any(token in text for token in ("operatsiya", "операц", "surgery", "фаллопротез", "микро-тезе")):
        return "operation"
    if any(token in text for token in ("oylik", "monitoring", "мониторинг", "telegram support")):
        return "monthly_monitoring"
    if any(token in text for token in ("konsultatsiya", "консультац", "consultation", "qabul")):
        return "consultation"
    if any(token in text for token in ("before", "after", "oldin", "keyin", "до/после")):
        return "before_after"
    if any(token in text for token in ("narx", "цена", "price", "chegirma", "aksiya")):
        return "promotion"
    if "?" in text or any(token in text for token in ("savol", "faq", "вопрос")):
        return "faq"
    if any(token in text for token in ("bilib oling", "haqida", "nima uchun", "почему", "about")):
        return "awareness"
    return "education"


def build_comment_context_package(
    media: Media | None,
    comment_text: str,
    comment_id: str,
    *,
    username: str = "",
    display_name: str = "",
    from_id: str | None = None,
    business_name: str = "Dr. Sultonbek",
    page_persona: str = "Clinic administrator assistant",
    campaign: CampaignPlan | Campaign | None = None,
    memory_context: str | None = None,
    previous_comments: str | None = None,
    comment_intent: str | None = None,
) -> str:
    """Assemble the full context package for Gemini comment replies."""
    sections: list[str] = []

    sections.append("POST INFORMATION")
    if media:
        sections.append(f"Media Type: {media.media_type or 'unknown'}")
        sections.append(f"Caption:\n{media.caption or '(no caption)'}")
        sections.append(f"Posted At: {media.timestamp or 'unknown'}")
        sections.append(f"Likes: {media.like_count if media.like_count is not None else 'unknown'}")
        sections.append(f"Permalink: {media.permalink or 'unknown'}")
        sections.append(f"Post Intent: {media.intent}")
    else:
        sections.append("Media Type: unknown")
        sections.append("Caption: (unavailable)")
        sections.append("Post Intent: unknown")

    sections.append("")
    sections.append("COMMENT")
    sections.append(f"Text: {comment_text}")
    sections.append(f"Comment ID: {comment_id}")
    if comment_intent:
        sections.append(f"Comment Intent: {comment_intent}")

    sections.append("")
    sections.append("COMMENT AUTHOR")
    sections.append(f"Username: {username or 'unknown'}")
    sections.append(f"Display Name: {display_name or 'unknown'}")
    sections.append(f"Instagram ID: {from_id or 'unknown'}")

    sections.append("")
    sections.append("ACCOUNT")
    sections.append(f"Business Name: {business_name}")
    sections.append(f"Page Persona: {page_persona}")

    if campaign:
        sections.append("")
        sections.append("CAMPAIGN")
        sections.append(f"Name: {campaign.name}")
        sections.append(f"Goal: {campaign.goal}")
        sections.append(f"Intent: {campaign.intent}")
        sections.append(f"CTA triggers: {', '.join(campaign.trigger_keywords)}")
        if campaign.public_reply:
            sections.append(f"Suggested public reply: {campaign.public_reply}")
        if campaign.dm_text:
            sections.append("This is a lead-magnet style CTA — acknowledge and confirm DM delivery.")

    if previous_comments:
        sections.append("")
        sections.append("PREVIOUS COMMENTS FROM THIS USER")
        sections.append(previous_comments)

    if memory_context:
        sections.append("")
        sections.append("CONVERSATION MEMORY (DMs)")
        sections.append(memory_context)

    sections.append("")
    sections.append(
        "INSTRUCTIONS FOR THIS REPLY:\n"
        "- You already have the full post caption and intent above — reply in that context.\n"
        "- If the caption asked people to comment a keyword for a guide/list, "
        "and this comment matches that CTA, thank them and confirm the material was/will be sent in DM.\n"
        "- Keep the public comment reply short (1–2 sentences). No intimate medical details.\n"
        "- Do not invent that a DM was sent unless the campaign flow is handling DM delivery.\n"
        "- Supportive comments (praise/emojis) are engagement, not leads — thank only, never sell."
    )

    if comment_intent:
        from app.utils.comment_intent import intent_reply_instructions

        sections.append("")
        sections.append(intent_reply_instructions(comment_intent))

    return "\n".join(sections)


def build_campaign_followup_dm(campaign: CampaignPlan | Campaign) -> str:
    """Compose DM text after a CTA comment match."""
    parts: list[str] = []
    body = (campaign.dm_text or "").strip()
    if body:
        parts.append(body)
    if campaign.dm_attachment_url:
        parts.append(campaign.dm_attachment_url)

    followups: list[str] = []
    if campaign.ask_name_after_dm:
        followups.append("Ismingizni yozib yuboring.")
    if campaign.ask_phone_after_dm:
        followups.append("Telefon raqamingizni qoldiring.")
    if campaign.offer_consultation:
        followups.append("Xohlasangiz konsultatsiyaga yozib qo'yaman.")

    if followups:
        parts.append("\n".join(followups))

    return "\n\n".join(p for p in parts if p).strip()
