from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator


DEFAULT_INCREMENTAL_END_OFFSET_DAYS = 1


@dataclass(frozen=True)
class DateWindow:
    start_date: date
    end_date: date


def parse_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def resolve_date_window(
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    days_back: int | None = None,
    default_days_back: int | None = None,
    today: date | None = None,
) -> DateWindow:
    """Resolve an inclusive ingest date window.

    Incremental ingestion intentionally defaults through yesterday. Garmin's
    current-day summaries are often incomplete until the day closes.
    """
    if days_back is not None and days_back < 1:
        raise ValueError("days_back must be at least 1")
    if default_days_back is not None and default_days_back < 1:
        raise ValueError("default_days_back must be at least 1")

    resolved_today = today or date.today()
    resolved_end = parse_date(end_date) or (
        resolved_today - timedelta(days=DEFAULT_INCREMENTAL_END_OFFSET_DAYS)
    )
    resolved_start = parse_date(start_date)

    if resolved_start is None:
        lookback_days = days_back if days_back is not None else default_days_back
        if lookback_days is None:
            raise ValueError("days_back or default_days_back is required")
        resolved_start = resolved_end - timedelta(days=lookback_days - 1)

    if resolved_start > resolved_end:
        raise ValueError("start_date must be on or before end_date")

    return DateWindow(start_date=resolved_start, end_date=resolved_end)


def iter_dates(start_date: date, end_date: date) -> Iterator[date]:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    current_date = start_date
    while current_date <= end_date:
        yield current_date
        current_date += timedelta(days=1)
