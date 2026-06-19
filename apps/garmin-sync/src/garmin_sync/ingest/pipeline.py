from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.config import get_settings
from garmin_postgres.models.user import User
from garmin_sync.ingest.date_windows import resolve_date_window
from garmin_sync.ingest.object_registry import DEFAULT_DATA_TYPES
from garmin_sync.ingest.runners import (
    ingest_activity,
    ingest_activities_range,
    ingest_daily_summary_day,
    ingest_daily_summary_range,
    ingest_personal_records,
    ingest_selected_objects,
    list_activity_ids,
    upsert_activity,
    upsert_activity_detail,
    upsert_activity_file,
    upsert_daily_summary,
    upsert_personal_record,
)


logger = logging.getLogger(__name__)


def run_ingestion(
    session: Session,
    user: User,
    start_date: date,
    end_date: date,
    *,
    dry_run: bool = False,
    data_types: list[str] | None = None,
    include_details: bool = True,
    include_files: bool = True,
) -> dict:
    """Run ingestion for a single user over a date range.

    Compatibility wrapper around the object-level runner functions.
    """
    summary = ingest_selected_objects(
        user_id=user.id,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        data_types=data_types,
        include_details=include_details,
        include_files=include_files,
        session=session,
    )
    return summary.as_dict()


def get_active_users(session: Session, display_name: str | None = None) -> list[User]:
    """Fetch active users, optionally filtered by display name."""
    stmt = select(User).where(User.is_active == True)  # noqa: E712
    if display_name:
        stmt = stmt.where(User.garmin_display_name == display_name)
    return list(session.scalars(stmt).all())


def run_for_all_users(
    session: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days_back: int | None = None,
    user_filter: str | None = None,
    dry_run: bool = False,
    data_types: list[str] | None = None,
    include_details: bool = True,
    include_files: bool = True,
) -> list[dict]:
    """Run ingestion for all active users."""
    settings = get_settings()
    window = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
        default_days_back=settings.ingest_days_back,
    )

    users = get_active_users(session, user_filter)
    if not users:
        logger.info("No active users found")
        return []

    all_results = []
    for user in users:
        logger.info(
            "Ingesting data for user %s (%s to %s)%s",
            user.garmin_display_name,
            window.start_date,
            window.end_date,
            " (dry run)" if dry_run else "",
        )
        result = run_ingestion(
            session,
            user,
            window.start_date,
            window.end_date,
            dry_run=dry_run,
            data_types=data_types,
            include_details=include_details,
            include_files=include_files,
        )
        all_results.append({
            "user": user.garmin_display_name,
            **result,
        })

    return all_results
