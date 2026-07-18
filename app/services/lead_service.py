"""Lead persistence and Google Sheets export orchestration."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.lead import LEAD_CATEGORIES, LEAD_SERVICES, LEAD_STATUSES, Lead
from app.services.google_sheets_service import GoogleSheetsService, lead_has_required_fields
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Uzbek / international phone signals in free text.
_PHONE_HINT = re.compile(
    r"(?:\+?998[\s\-]?)?(?:90|91|93|94|95|97|98|99|33|88|77)[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|\+?\d{9,15}"
)


def conversation_may_contain_lead(text: str) -> bool:
    """Cheap gate: only run Gemini lead extraction when a phone-like token appears."""
    return bool(_PHONE_HINT.search(text or ""))


def normalize_category(value: str | None) -> str:
    raw = (value or "Unknown").strip()
    for item in LEAD_CATEGORIES:
        if raw.lower() == item.lower():
            return item
    mapping = {
        "urolog": "Urology",
        "urology": "Urology",
        "hormon": "Hormonal",
        "hormonal": "Hormonal",
        "steroid": "Hormonal",
        "operat": "Operation",
        "surgery": "Operation",
        "monitor": "Monitoring",
    }
    lower = raw.lower()
    for key, mapped in mapping.items():
        if key in lower:
            return mapped
    return "Unknown"


def normalize_service(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    for item in LEAD_SERVICES:
        if raw.lower() == item.lower():
            return item
    lower = raw.lower()
    if "evening" in lower or "kechki" in lower or "19" in lower:
        return "Evening Consultation"
    if "day" in lower or "kunduz" in lower or "09" in lower:
        return "Day Consultation"
    if "monitor" in lower or "oylik" in lower:
        return "Monthly Monitoring"
    if "operat" in lower or "surgery" in lower:
        return "Operation"
    if "online" in lower or "onlayn" in lower:
        return "Online Consultation"
    return raw


class LeadService:
    """Validate Gemini lead payloads, upsert Mongo leads, export to Sheets."""

    def __init__(self, session: MongoSession, sheets: GoogleSheetsService) -> None:
        self._session = session
        self._sheets = sheets

    async def process_extraction(
        self,
        payload: dict[str, Any],
        *,
        instagram_user_id: str,
        instagram_username: str | None = None,
        conversation_id: int | None = None,
        account_id: str | None = None,
        conversation_link: str = "",
    ) -> Lead | None:
        """
        Persist + export when Gemini signals a complete lead.

        Returns the Lead document when created/updated, else None.
        """
        if not payload.get("lead_collected"):
            return None

        name = str(payload.get("name") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        problem = str(payload.get("problem") or "").strip()
        city = str(payload.get("city") or "").strip() or None
        preferred_date = str(payload.get("preferred_date") or "").strip() or None
        summary = str(payload.get("conversation_summary") or "").strip()
        category = normalize_category(payload.get("category"))
        service = normalize_service(payload.get("service") or payload.get("interested_service"))

        if not lead_has_required_fields({"name": name, "phone": phone, "problem": problem}):
            log_event(
                logger,
                logging.INFO,
                "lead_incomplete",
                user_id=instagram_user_id,
                has_name=bool(name),
                has_phone=bool(phone),
                has_problem=bool(problem),
            )
            return None

        if not summary:
            summary = (
                f"Patient: {name}. {problem}."
                + (f" City: {city}." if city else "")
                + (f" Service: {service}." if service else "")
                + (f" Preferred: {preferred_date}." if preferred_date else "")
                + " Phone collected. Needs doctor review."
            )

        now = datetime.now(timezone.utc)
        existing = await Lead.find_one(
            Lead.instagram_user_id == instagram_user_id,
            Lead.phone == phone,
        )

        if existing is None:
            lead = Lead(
                id=await next_id("leads"),
                instagram_user_id=instagram_user_id,
                instagram_username=instagram_username,
                name=name,
                phone=phone,
                city=city,
                problem=problem,
                category=category,
                interested_service=service,
                preferred_date=preferred_date,
                status="New Lead",
                conversation_summary=summary,
                conversation_id=conversation_id,
                account_id=account_id,
                created_at=now,
                updated_at=now,
            )
            await lead.insert()
            log_event(
                logger,
                logging.INFO,
                "lead_created",
                lead_id=lead.id,
                user_id=instagram_user_id,
                phone=phone,
            )
        else:
            lead = existing
            lead.instagram_username = instagram_username or lead.instagram_username
            lead.name = name
            lead.city = city or lead.city
            lead.problem = problem
            lead.category = category
            lead.interested_service = service or lead.interested_service
            lead.preferred_date = preferred_date or lead.preferred_date
            lead.conversation_summary = summary
            lead.conversation_id = conversation_id or lead.conversation_id
            lead.account_id = account_id or lead.account_id
            lead.updated_at = now
            if lead.status not in LEAD_STATUSES:
                lead.status = "New Lead"
            await lead.save()
            log_event(
                logger,
                logging.INFO,
                "lead_updated",
                lead_id=lead.id,
                user_id=instagram_user_id,
                already_exported=lead.exported_to_sheets,
            )

        if not lead.exported_to_sheets:
            exported = await self._sheets.append_lead(
                instagram_username=lead.instagram_username,
                name=lead.name,
                phone=lead.phone,
                city=lead.city,
                problem=lead.problem,
                category=lead.category,
                interested_service=lead.interested_service,
                preferred_date=lead.preferred_date,
                status=lead.status,
                conversation_summary=lead.conversation_summary,
                conversation_link=conversation_link,
                timestamp=lead.created_at,
            )
            if exported:
                lead.exported_to_sheets = True
                lead.sheets_exported_at = datetime.now(timezone.utc)
                await lead.save()

        return lead
