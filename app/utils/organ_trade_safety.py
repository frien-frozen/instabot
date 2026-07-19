"""Organ trade safety — highest priority, deterministic (not Gemini)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Exact reply required by policy. Do not paraphrase.
ORGAN_TRADE_REFUSAL_REPLY = (
    "Assalomu alaykum.\n\n"
    "Doktor Sultonbek organlarni sotib olish yoki sotish bilan shug‘ullanmaydi.\n\n"
    "Instagramdagi video faqat tibbiy-ma'lumot berish maqsadida tayyorlangan. "
    "U organ savdosini targ‘ib qilmaydi.\n\n"
    "Odam organlarini sotish yoki sotib olish noqonuniy hisoblanadi va biz bu borada "
    "yordam bera olmaymiz.\n\n"
    "Agar sizda buyrak, jigar yoki boshqa sog‘liq bilan bog‘liq tibbiy muammo bo‘lsa, "
    "mamnuniyat bilan tibbiy konsultatsiya bo‘yicha yordam beramiz."
)

# Redacted placeholder — never persist raw trade-attempt text.
REDACTED_ORGAN_TRADE_TEXT = "[REDACTED_ORGAN_TRADE_ATTEMPT]"

_ORGAN = (
    r"(?:buyrak|buyrag|pochka|pochki|kidney|jigar|liver|yurak|heart|"
    r"organ|орган|почка|почки|печень|сердце|donor\s*organ)"
)

_TRADE_VERB = (
    r"(?:sotmoq|sotaman|sotmoqchi|sotish|sotib|sotadi|sotasiz|sotay|"
    r"sell|selling|sold|buyer|seller|purchase|purchas|buy\b|buying|"
    r"oladi|olasiz|olaman|olmoqchi|sotuv|savdo|broker|брокер|"
    r"qora\s*bozor|black\s*market|traffick)"
)

_PRICE_TRADE = (
    r"(?:narxi|narx|price|cost|qancha\s*turadi|mingga|ming\s*\$|ming\s*dollar|"
    r"\$\s*\d|\d+\s*(?:ming|k|usd|dollar|\$)|olasizmi|berasizmi|"
    r"kim\s*oladi|kimga\s*sot|where\s*(?:can|to)\s*(?:i\s*)?sell|"
    r"need\s*(?:a\s*)?(?:kidney|liver|organ)\s*buyer|"
    r"who\s*buys|who\s*pays|pay\s*for\s*(?:my\s*)?(?:kidney|liver|organ))"
)

# Explicit commercial phrases (high confidence).
_STRONG_PATTERNS = (
    re.compile(rf"(?i)\b{_ORGAN}\w*\b.{{0,40}}\b{_TRADE_VERB}", re.DOTALL),
    re.compile(rf"(?i)\b{_TRADE_VERB}.{{0,40}}\b{_ORGAN}", re.DOTALL),
    re.compile(rf"(?i)\b{_ORGAN}\w*\b.{{0,40}}\b{_PRICE_TRADE}", re.DOTALL),
    re.compile(rf"(?i)\b{_PRICE_TRADE}.{{0,40}}\b{_ORGAN}", re.DOTALL),
    re.compile(
        r"(?i)\b(?:buyragimni|buyrakni|jigarimni|organimni)\s+"
        r"(?:sot|ol|ber)",
    ),
    re.compile(r"(?i)\b(?:sell|purchase|buy)\s+my\s+(?:kidney|liver|organ|heart)\b"),
    re.compile(r"(?i)\b(?:kidney|liver|organ)\s+buyer\b"),
    re.compile(r"(?i)\bwhere\s+can\s+i\s+sell\s+(?:my\s+)?(?:kidney|liver|organ)\b"),
    re.compile(r"(?i)\bkimga\s+sotaman\b"),
    re.compile(r"(?i)\bbuyrakni\s+kim\s+oladi\b"),
    re.compile(r"(?i)\b(?:160|100|50|200)\s*mingga\b"),
)

# Medical context that MUST stay allowed even with organ + price words.
_MEDICAL_SAFE = re.compile(
    r"(?i)\b(?:"
    r"og['ʻ’`]?ri|ogriyapti|tosh|stone|kreatinin|creatinine|"
    r"konsultatsiya|consultation|qabul|appointment|"
    r"transplantatsiya\s+haqida|transplantation\s+info|"
    r"donor\s+bo['ʻ’`]?lsam|otamga|onamga|akamga|ukamga|"
    r"kasallik|kasalligi|infection|infektsiya|davolash|treatment|"
    r"tahlil|analiz|lab\b|sog['ʻ’`]?liq|pain|shiшish|shish|"
    r"dialysis|gemodializ|ckd|pn|"
    r"tibbiy|medical"
    r")\b"
)

# Short price-only pings common under the viral organ-price reel.
# "DM qilaman" alone is NOT enough — needs organ/trade context elsewhere.
_BARE_PRICE_PING = re.compile(
    r"(?ix)^\s*(?:"
    r"narxi\s*qancha\s*\??|"
    r"narx\s*qancha\s*\??|"
    r"qancha\s*(?:turadi|bo['ʻ’`]?ladi|\$|dollar)?\s*\??|"
    r"price\s*\??|"
    r"how\s*much\s*\??|"
    r"\d{2,4}\s*mingga\s*(?:olasizmi)?\s*\??"
    r")\s*$"
)

_CAPTION_ORGAN_PRICE_TOPIC = re.compile(
    rf"(?i)\b{_ORGAN}\w*\b.{{0,80}}\b(?:narx|price|sot|savdo|trade|qiymat)",
)


def is_illegal_organ_trade_intent(text: str, *, caption: str | None = None) -> bool:
    """
    Return True only for commercial organ buy/sell/broker intent.

    Legitimate medical kidney/liver talk must return False.
    """
    raw = (text or "").strip()
    if not raw:
        return False

    # Strong commercial patterns first.
    for pattern in _STRONG_PATTERNS:
        if pattern.search(raw):
            # Allow "buyrak kasalligi uchun konsultatsiya narxi"
            if _MEDICAL_SAFE.search(raw) and not re.search(
                r"(?i)\b(?:sotmoq|sotaman|sell|buyer|purchase|kimga\s+sot|kim\s+oladi)\b",
                raw,
            ):
                # Medical + price for consultation → allow
                if re.search(r"(?i)\b(?:konsultatsiya|consultation|qabul|tahlil|analiz)\b", raw):
                    return False
                # Still selling language → block
                if re.search(r"(?i)\b(?:sot|sell|buyer|purchase|broker)\b", raw):
                    return True
                # Organ + price without medical consultation marker → block
                if re.search(rf"(?i)\b{_ORGAN}", raw) and re.search(rf"(?i)\b{_PRICE_TRADE}", raw):
                    if re.search(r"(?i)\b(?:konsultatsiya|consultation)\s+narx", raw):
                        return False
            return True

    # Bare price under an organ-price video / DM after that reel.
    if _BARE_PRICE_PING.match(raw) and not _MEDICAL_SAFE.search(raw):
        if caption and _CAPTION_ORGAN_PRICE_TOPIC.search(caption):
            return True
        # No caption (DM): treat bare organ-price pings as trade intent
        # after the viral organ-price content.
        if caption is None:
            return True

    return False

def log_illegal_organ_trade_attempt(*, instagram_user_id: str | None) -> None:
    """Log policy event without message text, phones, names, or prices."""
    log_event(
        logger,
        logging.WARNING,
        "illegal_organ_trade_attempt",
        event_type="illegal_organ_trade_attempt",
        instagram_user_id=instagram_user_id or "unknown",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
