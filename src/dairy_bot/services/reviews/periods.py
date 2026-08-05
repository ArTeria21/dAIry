from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dairy_bot.services.enrichment import discover_daily_notes, parse_daily_entries

from .models import ReviewKind, ReviewPeriod


def period_for(
    moment: datetime | date,
    *,
    kind: ReviewKind,
    timezone: ZoneInfo,
) -> ReviewPeriod:
    if isinstance(moment, datetime):
        local = (
            moment.replace(tzinfo=timezone)
            if moment.tzinfo is None
            else moment.astimezone(timezone)
        )
        day = local.date()
    else:
        day = moment

    if kind == "week":
        # Python weekdays start on Monday; reviews start on Sunday.
        start = day - timedelta(days=(day.weekday() + 1) % 7)
        end = start + timedelta(days=6)
        return ReviewPeriod(
            kind=kind,
            period=start.isoformat(),
            start_date=start,
            end_date=end,
        )
    if kind == "month":
        start = day.replace(day=1)
        end = day.replace(day=calendar.monthrange(day.year, day.month)[1])
        return ReviewPeriod(
            kind=kind,
            period=start.strftime("%Y-%m"),
            start_date=start,
            end_date=end,
        )
    raise ValueError(f"Unsupported review kind: {kind}")


def discover_closed_periods(
    vault: Path,
    *,
    now: datetime,
    timezone: ZoneInfo,
) -> list[ReviewPeriod]:
    local_now = (
        now.replace(tzinfo=timezone)
        if now.tzinfo is None
        else now.astimezone(timezone)
    )
    periods: dict[tuple[str, str], ReviewPeriod] = {}

    for path in discover_daily_notes(vault):
        try:
            entries = parse_daily_entries(path.read_text(encoding="utf-8"), path)
            entry_day = date.fromisoformat(path.stem)
        except (OSError, UnicodeError, ValueError):
            continue
        if not entries:
            continue
        for kind in ("week", "month"):
            period = period_for(entry_day, kind=kind, timezone=timezone)
            if period.end_date < local_now.date():
                periods[(period.kind, period.period)] = period

    return sorted(periods.values(), key=lambda item: (item.start_date, item.kind))
