"""Lead CRM record — structured patient inquiry exported to Google Sheets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel

LEAD_STATUSES = (
    "New Lead",
    "Waiting",
    "Called",
    "Booked",
    "Completed",
    "Cancelled",
)

LEAD_CATEGORIES = (
    "Hormonal",
    "Urology",
    "Operation",
    "Monitoring",
    "Unknown",
)

LEAD_SERVICES = (
    "Day Consultation",
    "Evening Consultation",
    "Monthly Monitoring",
    "Operation",
    "Online Consultation",
    "Unknown",
)


class Lead(Document):
    """
    Structured lead collected from Instagram DMs.

    Gemini extracts fields; the backend validates and exports to Google Sheets.
    """

    id: Optional[int] = None
    instagram_user_id: Indexed(str)
    instagram_username: Optional[str] = None
    name: str
    phone: Indexed(str)
    city: Optional[str] = None
    problem: str
    category: str = "Unknown"
    interested_service: Optional[str] = None
    preferred_date: Optional[str] = None
    status: str = "New Lead"
    conversation_summary: str = ""
    conversation_id: Optional[int] = None
    account_id: Optional[str] = None
    exported_to_sheets: bool = False
    sheets_exported_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "leads"
        indexes = [
            IndexModel(
                [("instagram_user_id", ASCENDING), ("phone", ASCENDING)],
                unique=True,
            ),
            IndexModel([("exported_to_sheets", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]
