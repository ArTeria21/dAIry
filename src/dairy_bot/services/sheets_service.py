"""Google Sheets integration for survey data sync."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Column headers for the spreadsheet (grouped thematically)
HEADERS = [
    "date",
    # Mood & Mental state
    "mood_morning",
    "mood_evening",
    "energy",
    "anxiety",
    "focus",
    # Sleep
    "sleep_duration",
    "sleep_score",
    "bedtime",
    "wake_time",
    # Food & Cravings
    "cravings",
    "no_junk_food",
    "no_eating_out",
    # Physical activity
    "sport",
    "steps_8k",
    # Habits & Routine
    "supplements",
    "tea_time",
    "english_words",
    "zero_spending",
    "reading",
]


class SheetsService:
    """Service for syncing survey data to Google Sheets."""

    def __init__(
        self,
        enabled: bool = False,
        spreadsheet_id: str | None = None,
        creds_file: Path | None = None,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self.enabled = enabled
        self.spreadsheet_id = spreadsheet_id
        self.creds_file = creds_file
        self.timezone = timezone
        self._client = None
        self._sheet = None

        if not enabled:
            logger.info("Google Sheets integration is disabled")
            return

        if not spreadsheet_id or not creds_file:
            logger.warning(
                "Google Sheets enabled but missing GOOGLE_SHEETS_ID or GOOGLE_CREDS_FILE"
            )
            self.enabled = False
            return

        if not creds_file.exists():
            logger.warning("Google credentials file not found: %s", creds_file)
            self.enabled = False
            return

        self._init_client()

    def _init_client(self) -> None:
        """Initialize Google Sheets client."""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials

            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                str(self.creds_file), scope
            )
            self._client = gspread.authorize(creds)
            self._sheet = self._client.open_by_key(self.spreadsheet_id).sheet1
            self._ensure_headers()
            logger.info("Google Sheets client initialized successfully")
        except Exception as e:
            logger.error("Failed to initialize Google Sheets client: %s", e)
            self.enabled = False

    def _ensure_headers(self) -> None:
        """Ensure the spreadsheet has correct headers."""
        if not self._sheet:
            return

        try:
            existing = self._sheet.row_values(1)
            if existing != HEADERS:
                # Update headers if they don't match
                self._sheet.update("A1", [HEADERS])
                logger.info("Updated spreadsheet headers")
        except Exception as e:
            logger.warning("Failed to check/update headers: %s", e)

    def _find_row_by_date(self, date_str: str) -> int | None:
        """Find row number for a specific date, or None if not found."""
        if not self._sheet:
            return None

        try:
            cell = self._sheet.find(date_str, in_column=1)
            return cell.row if cell else None
        except Exception:
            return None

    def _get_next_row(self) -> int:
        """Get the next available row number."""
        if not self._sheet:
            return 2

        try:
            values = self._sheet.col_values(1)
            return len(values) + 1
        except Exception:
            return 2

    def sync_survey_data(
        self,
        data: dict[str, Any],
        moment: datetime | None = None,
    ) -> bool:
        """Sync survey data to Google Sheets.

        Args:
            data: Survey data dictionary
            moment: Date for the data (defaults to today)

        Returns:
            True if sync was successful, False otherwise
        """
        if not self.enabled or not self._sheet:
            return False

        try:
            tz = self.timezone or ZoneInfo("UTC")
            current = moment or datetime.now(tz)
            date_str = current.strftime("%Y-%m-%d")

            # Build row data (must match HEADERS order)
            habits = data.get("habits", {})
            row_data = [
                date_str,
                # Mood & Mental state
                data.get("mood_morning"),
                data.get("mood_evening"),
                data.get("energy"),
                data.get("anxiety"),
                data.get("focus"),
                # Sleep
                data.get("sleep_duration"),
                data.get("sleep_score"),
                data.get("bedtime"),
                data.get("wake_time"),
                # Food & Cravings
                data.get("cravings"),
                habits.get("no_junk_food"),
                habits.get("no_eating_out"),
                # Physical activity
                data.get("sport"),
                habits.get("steps_8k"),
                # Habits & Routine
                habits.get("supplements"),
                habits.get("tea_time"),
                habits.get("english_words"),
                habits.get("zero_spending"),
                habits.get("reading"),
            ]

            # Convert None to empty string and booleans to strings for sheets
            row_data = [
                "" if v is None else ("TRUE" if v is True else ("FALSE" if v is False else v))
                for v in row_data
            ]

            # Check if row for this date exists
            existing_row = self._find_row_by_date(date_str)

            if existing_row:
                # Update existing row, but preserve non-empty values
                existing_values = self._sheet.row_values(existing_row)
                # Pad existing values if needed
                while len(existing_values) < len(row_data):
                    existing_values.append("")

                # Merge: keep existing value if new value is empty
                merged = []
                for i, (new, old) in enumerate(zip(row_data, existing_values)):
                    if new == "" and old != "":
                        merged.append(old)
                    else:
                        merged.append(new)

                self._sheet.update(f"A{existing_row}", [merged])
                logger.info("Updated existing row %d for date %s", existing_row, date_str)
            else:
                # Append new row
                next_row = self._get_next_row()
                self._sheet.update(f"A{next_row}", [row_data])
                logger.info("Added new row %d for date %s", next_row, date_str)

            return True

        except Exception as e:
            logger.error("Failed to sync survey data to Google Sheets: %s", e)
            return False
