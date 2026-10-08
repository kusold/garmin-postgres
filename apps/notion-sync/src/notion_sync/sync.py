import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from notion_sync.formatters import DAILY_STREAK_TYPE_ID
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page

logger = logging.getLogger(__name__)


DATA_TYPES = ["activities", "daily_steps", "personal_records"]


class _PageSink(Protocol):
    def upsert_page(
        self,
        database_id: str,
        *,
        filter_payload: dict,
        properties: dict,
        icon: dict | None = None,
    ) -> str: ...


@dataclass
class SyncResult:
    status: str
    rows: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        data = {
            "status": self.status,
            "rows": self.rows,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
        }
        if self.error:
            data["error"] = self.error
        return data


def _status(rows: int, errors: int) -> str:
    if errors == 0:
        return "success"
    return "partial" if rows > 0 else "error"


def _user_clause(stmt, model, user_id: int | None):
    if user_id is not None:
        return stmt.where(model.user_id == user_id)
    return stmt


def _apply_datetime_window(
    stmt,
    column,
    start_date: date | None,
    end_date: date | None,
):
    """Half-open [start, end+1day) window on a tz-aware datetime column (UTC)."""
    if start_date:
        start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(column >= start_at)
    if end_date:
        end_before = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        stmt = stmt.where(column < end_before)
    return stmt


def _apply_date_window(
    stmt,
    column,
    start_date: date | None,
    end_date: date | None,
):
    """Inclusive [start, end] window on a date column."""
    if start_date:
        stmt = stmt.where(column >= start_date)
    if end_date:
        stmt = stmt.where(column <= end_date)
    return stmt


def _current_streak_per_user(rows: list[PersonalRecord]) -> list[PersonalRecord]:
    """Keep only each user's latest daily-streak row; all other rows pass through.

    Garmin emits the daily-streak type (16) as one row per day — a running
    counter that resets when the streak breaks — so the page tracks the
    current streak, matching Garmin's own display. The all-time best is a
    separate record type (15, Longest Goal Streak) that syncs unchanged.
    """
    out: list[PersonalRecord] = []
    latest: dict[int, tuple[date, float, PersonalRecord]] = {}
    for row in rows:
        if row.type_id != DAILY_STREAK_TYPE_ID:
            out.append(row)
            continue
        try:
            value = float(row.value_text)
        except (TypeError, ValueError):
            continue
        current = latest.get(row.user_id)
        # Garmin can emit two rows for a reset day (0 and 1); keep the higher
        # value on the most recent date.
        if (
            current is None
            or row.record_date > current[0]
            or (row.record_date == current[0] and value >= current[1])
        ):
            latest[row.user_id] = (row.record_date, value, row)
    out.extend(row for _, _, row in latest.values())
    return out


def _sync_table(
    session: Session,
    sink: _PageSink,
    database_id: str | None,
    *,
    label: str,
    model,
    order_column,
    date_window: Callable[..., object],
    mapper: Callable[..., tuple[dict, dict, dict | None]],
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: int | None = None,
    row_filter: Callable[[list], list] | None = None,
) -> SyncResult:
    if not database_id:
        return SyncResult(
            status="skipped",
            skipped=1,
            error=f"No Notion database configured for {label} in sync_targets",
        )

    stmt = select(model).order_by(order_column)
    stmt = _user_clause(stmt, model, user_id)
    stmt = date_window(stmt, order_column, start_date, end_date)

    records = list(session.scalars(stmt).all())
    if row_filter is not None:
        records = row_filter(records)

    rows = created = updated = errors = 0
    for row in records:
        rows += 1
        try:
            properties, filter_payload, icon = mapper(row)
            action = sink.upsert_page(
                database_id,
                filter_payload=filter_payload,
                properties=properties,
                icon=icon,
            )
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
        except Exception:
            logger.exception(
                "Failed to sync %s id=%s",
                type(row).__name__,
                getattr(row, "id", "?"),
            )
            errors += 1
    return SyncResult(
        status=_status(rows, errors),
        rows=rows,
        created=created,
        updated=updated,
        errors=errors,
    )


def sync_activities(
    session: Session,
    sink: _PageSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: int | None = None,
) -> SyncResult:
    return _sync_table(
        session,
        sink,
        database_id,
        label="activities",
        model=Activity,
        order_column=Activity.start_time,
        date_window=_apply_datetime_window,
        mapper=activity_page,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )


def sync_daily_steps(
    session: Session,
    sink: _PageSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: int | None = None,
) -> SyncResult:
    return _sync_table(
        session,
        sink,
        database_id,
        label="daily_steps",
        model=DailySummary,
        order_column=DailySummary.calendar_date,
        date_window=_apply_date_window,
        mapper=daily_steps_page,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )


def sync_personal_records(
    session: Session,
    sink: _PageSink,
    database_id: str | None,
    *,
    user_id: int | None = None,
) -> SyncResult:
    return _sync_table(
        session,
        sink,
        database_id,
        label="personal_records",
        model=PersonalRecord,
        order_column=PersonalRecord.record_date,
        date_window=_apply_date_window,
        mapper=personal_record_page,
        user_id=user_id,
        row_filter=_current_streak_per_user,
    )


def run_sync(
    session: Session,
    sink: _PageSink,
    targets: dict[str, str],
    *,
    data_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: int | None = None,
) -> dict[str, dict]:
    selected = data_types or DATA_TYPES
    results: dict[str, dict] = {}
    for data_type in selected:
        database_id = targets.get(data_type)
        if data_type == "activities":
            result = sync_activities(
                session, sink, database_id,
                start_date=start_date, end_date=end_date, user_id=user_id,
            )
        elif data_type == "daily_steps":
            result = sync_daily_steps(
                session, sink, database_id,
                start_date=start_date, end_date=end_date, user_id=user_id,
            )
        elif data_type == "personal_records":
            # Personal records describe a full snapshot, even in a dated run.
            result = sync_personal_records(session, sink, database_id, user_id=user_id)
        else:
            raise ValueError(f"Unsupported Notion data type: {data_type}")
        results[data_type] = result.as_dict()
    return results
