"""Google Sheets CRM export via service account (backend-only writes)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

SHEET_HEADERS = [
    "Time",
    "Instagram Username",
    "Name",
    "Phone",
    "City",
    "Problem",
    "Category",
    "Interested Service",
    "Preferred Date",
    "Status",
    "Conversation Summary",
    "Conversation Link",
]

# Columns that must be present before a row is appended.
REQUIRED_LEAD_FIELDS = ("name", "phone", "problem")


class SheetsExportError(Exception):
    """Raised when Google Sheets append fails."""


def lead_has_required_fields(data: dict[str, Any]) -> bool:
    """Return True when name, phone, and problem are non-empty."""
    for key in REQUIRED_LEAD_FIELDS:
        value = data.get(key)
        if value is None or not str(value).strip():
            return False
    return True


class GoogleSheetsService:
    """
    Append validated lead rows to a Google Spreadsheet.

    Uses a Google Cloud service account — never OAuth user login, never Gemini.
    Share the spreadsheet with the service account email as Editor.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._worksheet = None

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.google_sheets_enabled
            and self._settings.google_sheets_spreadsheet_id.strip()
            and (
                self._settings.google_service_account_json.strip()
                or self._settings.google_service_account_file.strip()
            )
        )

    def _load_credentials_info(self) -> dict[str, Any]:
        inline = self._settings.google_service_account_json.strip()
        if inline:
            return json.loads(inline)

        path = Path(self._settings.google_service_account_file.strip()).expanduser()
        if not path.is_file():
            raise SheetsExportError(f"Service account file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _get_worksheet(self):
        if self._worksheet is not None:
            return self._worksheet

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise SheetsExportError(
                "Install gspread and google-auth: pip install gspread google-auth"
            ) from exc

        info = self._load_credentials_info()
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(info, scopes=scopes)
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(self._settings.google_sheets_spreadsheet_id.strip())
        sheet_name = self._settings.google_sheets_worksheet_name.strip() or "Leads"
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(SHEET_HEADERS))

        # Ensure header row exists.
        existing = worksheet.row_values(1)
        if not existing:
            worksheet.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")

        self._client = client
        self._worksheet = worksheet
        return worksheet

    def _append_row_sync(self, row: list[str]) -> None:
        worksheet = self._get_worksheet()
        worksheet.append_row(row, value_input_option="USER_ENTERED")

    async def append_lead(
        self,
        *,
        instagram_username: str | None,
        name: str,
        phone: str,
        city: str | None,
        problem: str,
        category: str,
        interested_service: str | None,
        preferred_date: str | None,
        status: str,
        conversation_summary: str,
        conversation_link: str = "",
        timestamp: datetime | None = None,
    ) -> bool:
        """
        Append one CRM row. Returns True on success.

        No-ops (returns False) when Sheets is disabled or required fields missing.
        """
        payload = {"name": name, "phone": phone, "problem": problem}
        if not lead_has_required_fields(payload):
            log_event(
                logger,
                logging.INFO,
                "sheets_export_skipped_incomplete",
                has_name=bool(name and name.strip()),
                has_phone=bool(phone and phone.strip()),
                has_problem=bool(problem and problem.strip()),
            )
            return False

        if not self.enabled:
            log_event(logger, logging.INFO, "sheets_export_disabled")
            return False

        ts = timestamp or datetime.now(timezone.utc)
        row = [
            ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
            (instagram_username or "").strip(),
            name.strip(),
            phone.strip(),
            (city or "").strip(),
            problem.strip(),
            (category or "Unknown").strip(),
            (interested_service or "").strip(),
            (preferred_date or "").strip(),
            (status or "New Lead").strip(),
            (conversation_summary or "").strip(),
            (conversation_link or "").strip(),
        ]

        try:
            await asyncio.to_thread(self._append_row_sync, row)
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "sheets_export_failed",
                error=str(exc),
                phone=phone,
            )
            raise SheetsExportError(str(exc)) from exc

        log_event(
            logger,
            logging.INFO,
            "sheets_export_ok",
            phone=phone,
            name=name,
            username=instagram_username,
        )
        return True
