"""Organ trade safety — highest priority, deterministic (not Gemini)."""

from __future__ import annotations

import logging
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
    r"organ|орган|почка|почки|печень|сердце|donor\s*organ|"
    r"буйрак|буйраг|жигар)"
)

# Include conjugations: sotsam, sotaman, sotmoqchi, ...
_TRADE_VERB = (
    r"(?:sot(?:moq|aman|moqchi|ish|ib|adi|asiz|ay|sam|aman|moqchiman)?|"
    r"sell(?:ing|s)?|sold|buyer|seller|purchase|purchas(?:e|ing)?|buy(?:ing)?|"
    r"oladi|olasiz|olaman|olmoqchi|sotuv|savdo|broker|брокер|"
    r"qora\s*bozor|black\s*market|traffick|"
    r"ким\s*олади|сотаман|продать|куплю|продам)"
)

_PRICE_TRADE = (
    r"(?:narxi|narx|price|cost|qancha\s*turadi|mingga|ming\s*\$|ming\s*dollar|"
    r"\$\s*\d|\d+\s*(?:ming|k|usd|dollar|\$)|olasizmi|berasizmi|"
    r"kim\s*oladi|kimga\s*sot|where\s*(?:can|to)\s*(?:i\s*)?sell|"
    r"need\s*(?:a\s*)?(?:kidney|liver|organ)\s*buyer|"
    r"who\s*buys|who\s*pays|pay\s*for\s*(?:my\s*)?(?:kidney|liver|organ)|"
    r"ким\s*олади|кимга\s*сотаман|qayerga\s+muroja)"
)

# Explicit commercial phrases (high confidence). Distance up to 220 chars —
# real DMs often pad "buyragimni ... sotsam" with a long sob story.
_STRONG_PATTERNS = (
    re.compile(rf"(?i)\b{_ORGAN}\w*\b[\s\S]{{0,220}}\b{_TRADE_VERB}", re.DOTALL),
    re.compile(rf"(?i)\b{_TRADE_VERB}[\s\S]{{0,220}}\b{_ORGAN}", re.DOTALL),
    re.compile(rf"(?i)\b{_ORGAN}\w*\b[\s\S]{{0,220}}\b{_PRICE_TRADE}", re.DOTALL),
    re.compile(rf"(?i)\b{_PRICE_TRADE}[\s\S]{{0,220}}\b{_ORGAN}", re.DOTALL),
    re.compile(
        r"(?i)\b(?:buyragimni|buyrakni|jigarimni|organimni|буйрагимни)\b[\s\S]{0,220}\b"
        r"(?:sot|ol|ber|прод|куп)",
    ),
    re.compile(r"(?i)\b(?:sell|purchase|buy)\s+my\s+(?:kidney|liver|organ|heart)\b"),
    re.compile(r"(?i)\b(?:kidney|liver|organ)\s+buyer\b"),
    re.compile(r"(?i)\bwhere\s+can\s+i\s+sell\s+(?:my\s+)?(?:kidney|liver|organ)\b"),
    re.compile(r"(?i)\bkimga\s+sotaman\b"),
    re.compile(r"(?i)\bbuyrakni\s+kim\s+oladi\b"),
    re.compile(r"(?i)\bким\s+олади\b"),
    re.compile(r"(?i)\b(?:160|100|50|200)\s*mingga\b"),
    # Soft-sell: organ + "qayerga murojat / where to apply"
    re.compile(
        rf"(?i)\b{_ORGAN}\w*\b[\s\S]{{0,220}}\b"
        r"(?:qayerga\s+muroja|where\s+(?:can|do)\s+i\s+(?:apply|go|sell)|"
        r"кимга\s+мурожаат|куда\s+(?:обрат|продать))",
    ),
)

_MEDICAL_SAFE = re.compile(
    r"(?i)\b(?:"
    r"og['ʻ’`]?ri|ogriyapti|tosh|stone|kreatinin|creatinine|"
    r"konsultatsiya|consultation|qabul|appointment|"
    r"transplantatsiya\s+haqida|transplantation\s+info|"
    r"donor\s+bo['ʻ’`]?lsam|otamga|onamga|akamga|ukamga|"
    r"kasallik|kasalligi|infection|infektsiya|davolash|treatment|"
    r"tahlil|analiz|lab\b|sog['ʻ’`]?liq|pain|"
    r"dialysis|gemodializ|ckd|"
    r"tibbiy|medical"
    r")\b"
)

_BARE_PRICE_PING = re.compile(
    r"(?ix)^\s*(?:"
    r"narxi\s*qancha\s*\??|"
    r"narx\s*qancha\s*\??|"
    r"qancha\s*(?:turadi|bo['ʻ’`]?ladi|\$|dollar)?\s*\??|"
    r"price\s*\??|"
    r"how\s*much\s*\??|"
    r"\d{2,4}\s*mingga\s*(?:olasizmi)?\s*\??|"
    r"kim\s*oladi\s*\??|"
    r"ким\s*олади\s*\??|"
    r"kimga\s*sotaman\s*\??"
    r")\s*$"
)

_CAPTION_ORGAN_PRICE_TOPIC = re.compile(
    rf"(?i)\b{_ORGAN}\w*\b.{{0,80}}\b(?:narx|price|sot|savdo|trade|qiymat|орган)",
)


def is_illegal_organ_trade_intent(text: str, *, caption: str | None = None) -> bool:
    """
    Return True only for commercial organ buy/sell/broker intent.

    Legitimate medical kidney/liver talk must return False.
    """
    raw = (text or "").strip()
    if not raw:
        return False

    for pattern in _STRONG_PATTERNS:
        if pattern.search(raw):
            if _MEDICAL_SAFE.search(raw) and not re.search(
                r"(?i)\b(?:sot\w*|sell|buyer|purchase|kimga\s+sot|kim\s+oladi|ким\s+олади)\b",
                raw,
            ):
                if re.search(r"(?i)\b(?:konsultatsiya|consultation|qabul|tahlil|analiz)\b", raw):
                    return False
                if re.search(r"(?i)\b(?:sot|sell|buyer|purchase|broker)\b", raw):
                    return True
                if re.search(rf"(?i)\b{_ORGAN}", raw) and re.search(rf"(?i)\b{_PRICE_TRADE}", raw):
                    if re.search(r"(?i)\b(?:konsultatsiya|consultation)\s+narx", raw):
                        return False
            return True

    if _BARE_PRICE_PING.match(raw) and not _MEDICAL_SAFE.search(raw):
        # Only under an organ-trade caption. Never in DMs (caption=None) —
        # otherwise "Pul" / "narxi qancha" falsely blocks real patients.
        if caption and _CAPTION_ORGAN_PRICE_TOPIC.search(caption):
            return True
        if caption and re.search(r"(?i)^\s*(?:kim\s*oladi|ким\s*олади)\s*\??\s*$", raw):
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
