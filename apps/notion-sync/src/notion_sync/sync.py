import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from garmin_postgres.models.user import User
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page
from notion_sync.notion import NotionSink

logger = logging.getLogger(__name__)


DATA_TYPES = ["activities", "daily_steps", "personal_records"]


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


def _users_clause(stmt, user_filter: str | None):
    if user_filter:
        return stmt.join(User).where(User.garmin_display_name == user_filter)
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


def _sync_table(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    label: str,
    model,
    order_column,
    date_window: Callable[..., object],
    mapper: Callable[..., tuple[dict, dict, dict | None]],
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
) -> SyncResult:
    if not database_id:
        return SyncResult(
            status="skipped",
            skipped=1,
            error=f"No Notion database configured for {label} in sync_targets",
        )

    stmt = select(model).order_by(order_column)
    stmt = _users_clause(stmt, user_filter)
    stmt = date_window(stmt, order_column, start_date, end_date)

    rows = created = updated = errors = 0
    for row in session.scalars(stmt).all():
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
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
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
        user_filter=user_filter,
    )


def sync_daily_steps(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
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
        user_filter=user_filter,
    )


def sync_personal_records(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
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
        start_date=start_date,
        end_date=end_date,
        user_filter=user_filter,
    )


def run_sync(
    session: Session,
    sink: NotionSink,
    targets: dict[str, str],
    *,
    data_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
) -> dict[str, dict]:
    selected = data_types or DATA_TYPES
    results = {}
    if "activities" in selected:
        results["activities"] = sync_activities(
            session,
            sink,
            targets.get("activities"),
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    if "daily_steps" in selected:
        results["daily_steps"] = sync_daily_steps(
            session,
            sink,
            targets.get("daily_steps"),
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    if "personal_records" in selected:
        results["personal_records"] = sync_personal_records(
            session,
            sink,
            targets.get("personal_records"),
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    return results
