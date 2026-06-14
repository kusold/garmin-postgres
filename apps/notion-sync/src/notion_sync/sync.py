from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from garmin_postgres.models.user import User
from notion_sync.config import NotionSettings
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page
from notion_sync.notion import NotionSink


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


def sync_activities(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
) -> SyncResult:
    if not database_id:
        return SyncResult(status="skipped", skipped=1, error="NOTION_ACTIVITIES_DB_ID is not configured")

    stmt = select(Activity).order_by(Activity.start_time)
    stmt = _users_clause(stmt, user_filter)
    if start_date:
        start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Activity.start_time >= start_at)
    if end_date:
        end_before = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        stmt = stmt.where(Activity.start_time < end_before)

    rows = created = updated = errors = 0
    for activity in session.scalars(stmt).all():
        rows += 1
        try:
            properties, filter_payload, icon = activity_page(activity)
            action = sink.upsert_page(database_id, filter_payload=filter_payload, properties=properties, icon=icon)
            created += action == "created"
            updated += action == "updated"
        except Exception:
            errors += 1
    return SyncResult(status=_status(rows, errors), rows=rows, created=created, updated=updated, errors=errors)


def sync_daily_steps(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
) -> SyncResult:
    if not database_id:
        return SyncResult(status="skipped", skipped=1, error="NOTION_DAILY_STEPS_DB_ID is not configured")

    stmt = select(DailySummary).order_by(DailySummary.calendar_date)
    stmt = _users_clause(stmt, user_filter)
    if start_date:
        stmt = stmt.where(DailySummary.calendar_date >= start_date)
    if end_date:
        stmt = stmt.where(DailySummary.calendar_date <= end_date)

    rows = created = updated = errors = 0
    for summary in session.scalars(stmt).all():
        rows += 1
        try:
            properties, filter_payload = daily_steps_page(summary)
            action = sink.upsert_page(database_id, filter_payload=filter_payload, properties=properties)
            created += action == "created"
            updated += action == "updated"
        except Exception:
            errors += 1
    return SyncResult(status=_status(rows, errors), rows=rows, created=created, updated=updated, errors=errors)


def sync_personal_records(
    session: Session,
    sink: NotionSink,
    database_id: str | None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    user_filter: str | None = None,
) -> SyncResult:
    if not database_id:
        return SyncResult(status="skipped", skipped=1, error="NOTION_PERSONAL_RECORDS_DB_ID is not configured")

    stmt = select(PersonalRecord).order_by(PersonalRecord.record_date)
    stmt = _users_clause(stmt, user_filter)
    if start_date:
        stmt = stmt.where(PersonalRecord.record_date >= start_date)
    if end_date:
        stmt = stmt.where(PersonalRecord.record_date <= end_date)

    rows = created = updated = errors = 0
    for record in session.scalars(stmt).all():
        rows += 1
        try:
            properties, filter_payload, icon = personal_record_page(record)
            action = sink.upsert_page(database_id, filter_payload=filter_payload, properties=properties, icon=icon)
            created += action == "created"
            updated += action == "updated"
        except Exception:
            errors += 1
    return SyncResult(status=_status(rows, errors), rows=rows, created=created, updated=updated, errors=errors)


def run_sync(
    session: Session,
    sink: NotionSink,
    settings: NotionSettings,
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
            settings.activities_database_id,
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    if "daily_steps" in selected:
        results["daily_steps"] = sync_daily_steps(
            session,
            sink,
            settings.daily_steps_database_id,
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    if "personal_records" in selected:
        results["personal_records"] = sync_personal_records(
            session,
            sink,
            settings.personal_records_database_id,
            start_date=start_date,
            end_date=end_date,
            user_filter=user_filter,
        ).as_dict()
    return results
