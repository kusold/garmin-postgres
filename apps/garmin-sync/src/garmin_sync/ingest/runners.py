from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from garmin_postgres.db import get_engine
from garmin_postgres.models.activity import Activity
from garmin_postgres.models.activity_detail import ActivityDetail
from garmin_postgres.models.activity_file import ActivityFile
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from garmin_postgres.models.user import User
from garmin_sync.auth import load_user_client, save_tokens
from garmin_sync.ingest.client import GarminClient
from garmin_sync.ingest.date_windows import iter_dates
from garmin_sync.ingest.object_registry import (
    ACTIVITIES,
    DAILY_SUMMARY,
    PERSONAL_RECORDS,
    normalize_data_types,
)
from garmin_sync.ingest.parsers.activity import parse_activity
from garmin_sync.ingest.parsers.daily_summary import parse_daily_summary
from garmin_sync.ingest.parsers.personal_record import parse_personal_record
from garmin_sync.ingest.results import IngestResult, IngestSummary, aggregate_results


logger = logging.getLogger(__name__)

ACTIVITY_DETAIL_MAX_CHART_SIZE = 2000
ACTIVITY_DETAIL_MAX_POLYLINE_SIZE = 4000


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return

    engine = get_engine()
    with Session(engine) as managed_session:
        yield managed_session


def _get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    return user


def _client_for_user(session: Session, user: User) -> GarminClient | None:
    garmin = load_user_client(session, user)
    if garmin is None:
        return None
    return GarminClient(garmin)


def _save_tokens_and_mark_ingested(
    session: Session,
    user: User,
    client: GarminClient,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    save_tokens(session, user, client.garmin)
    user.last_ingest_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()


def upsert_daily_summary(session: Session, summary: DailySummary) -> DailySummary:
    """Insert or update a daily summary row using ON CONFLICT DO UPDATE."""
    data = {
        "user_id": summary.user_id,
        "calendar_date": summary.calendar_date,
        "raw_json": summary.raw_json,
    }

    stmt = insert(DailySummary).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "calendar_date"],
        set_={
            "raw_json": stmt.excluded.raw_json,
        },
    )
    session.execute(stmt)
    session.flush()
    return summary


def upsert_activity(session: Session, activity: Activity) -> Activity:
    """Insert or update an activity row using ON CONFLICT DO UPDATE."""
    data = {
        "user_id": activity.user_id,
        "activity_id": activity.activity_id,
        "activity_type": activity.activity_type,
        "start_time": activity.start_time,
        "raw_json": activity.raw_json,
    }

    stmt = insert(Activity).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "activity_id"],
        set_={
            "activity_type": stmt.excluded.activity_type,
            "start_time": stmt.excluded.start_time,
            "raw_json": stmt.excluded.raw_json,
        },
    )
    session.execute(stmt)
    session.flush()

    existing = session.scalars(
        select(Activity).where(
            Activity.user_id == activity.user_id,
            Activity.activity_id == activity.activity_id,
        )
    ).first()
    if existing:
        activity.id = existing.id
    return activity


def upsert_activity_file(session: Session, activity_file: ActivityFile) -> ActivityFile:
    """Insert or update an activity file row using ON CONFLICT DO UPDATE."""
    data = {
        "activity_id": activity_file.activity_id,
        "file_format": activity_file.file_format,
        "file_data": activity_file.file_data,
        "raw_json": activity_file.raw_json,
    }

    stmt = insert(ActivityFile).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["activity_id", "file_format"],
        set_={
            "file_data": stmt.excluded.file_data,
            "raw_json": stmt.excluded.raw_json,
        },
    )
    session.execute(stmt)
    session.flush()
    return activity_file


def upsert_activity_detail(
    session: Session,
    activity_detail: ActivityDetail,
) -> ActivityDetail:
    """Insert or update an activity detail row using ON CONFLICT DO UPDATE."""
    data = {
        "activity_id": activity_detail.activity_id,
        "max_chart_size": activity_detail.max_chart_size,
        "max_polyline_size": activity_detail.max_polyline_size,
        "raw_json": activity_detail.raw_json,
    }

    stmt = insert(ActivityDetail).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["activity_id"],
        set_={
            "max_chart_size": stmt.excluded.max_chart_size,
            "max_polyline_size": stmt.excluded.max_polyline_size,
            "raw_json": stmt.excluded.raw_json,
        },
    )
    session.execute(stmt)
    session.flush()
    return activity_detail


def upsert_personal_record(
    session: Session,
    personal_record: PersonalRecord,
) -> PersonalRecord:
    """Insert or update a personal record row using ON CONFLICT DO UPDATE."""
    data = {
        "user_id": personal_record.user_id,
        "type_id": personal_record.type_id,
        "record_date": personal_record.record_date,
        "activity_type": personal_record.activity_type,
        "value_text": personal_record.value_text,
        "raw_json": personal_record.raw_json,
    }

    stmt = insert(PersonalRecord).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "type_id", "record_date", "value_text"],
        set_={
            "activity_type": stmt.excluded.activity_type,
            "value_text": stmt.excluded.value_text,
            "raw_json": stmt.excluded.raw_json,
        },
    )
    session.execute(stmt)
    session.flush()
    return personal_record


def _fetch_and_store_activity_detail(
    session: Session,
    client: GarminClient,
    activity: Activity,
    *,
    dry_run: bool = False,
) -> bool:
    try:
        raw_detail = client.get_activity_details(
            str(activity.activity_id),
            maxchart=ACTIVITY_DETAIL_MAX_CHART_SIZE,
            maxpoly=ACTIVITY_DETAIL_MAX_POLYLINE_SIZE,
        )
        if not dry_run:
            detail = ActivityDetail(
                activity_id=activity.id,
                max_chart_size=ACTIVITY_DETAIL_MAX_CHART_SIZE,
                max_polyline_size=ACTIVITY_DETAIL_MAX_POLYLINE_SIZE,
                raw_json=raw_detail,
            )
            upsert_activity_detail(session, detail)
        return True
    except Exception as e:
        logger.warning(
            "Failed to fetch details for activity %s: %s",
            activity.activity_id,
            e,
        )
        return False


def _download_and_store_file(
    session: Session,
    client: GarminClient,
    activity: Activity,
) -> bool:
    try:
        file_data = client.download_activity(str(activity.activity_id))
        activity_file = ActivityFile(
            activity_id=activity.id,
            file_format="fit",
            file_data=file_data,
            raw_json={
                "source_format": "original",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        upsert_activity_file(session, activity_file)
        return True
    except Exception as e:
        logger.warning(
            "Failed to download file for activity %s: %s",
            activity.activity_id,
            e,
        )
        return False


def ingest_daily_summary_day(
    *,
    user_id: int,
    calendar_date: date,
    dry_run: bool = False,
    session: Session | None = None,
    raise_on_error: bool = False,
) -> IngestResult:
    with _session_scope(session) as current_session:
        try:
            user = _get_user(current_session, user_id)
            client = _client_for_user(current_session, user)
            if client is None:
                return IngestResult.error_result(
                    DAILY_SUMMARY,
                    error="Failed to load tokens",
                )

            raw = client.get_daily_summary(calendar_date.isoformat())
            summary = parse_daily_summary(raw, user.id)
            if not dry_run:
                upsert_daily_summary(current_session, summary)
            _save_tokens_and_mark_ingested(
                current_session,
                user,
                client,
                dry_run=dry_run,
            )
            logger.debug(
                "Ingested daily summary for %s (user %s)",
                calendar_date,
                user.garmin_display_name,
            )
            return IngestResult.success(DAILY_SUMMARY, rows=1)
        except Exception as e:
            current_session.rollback()
            if raise_on_error:
                raise
            logger.warning(
                "Failed to fetch daily summary for %s (user %s): %s",
                calendar_date,
                user_id,
                e,
            )
            return IngestResult.error_result(DAILY_SUMMARY, error=str(e))


def ingest_daily_summary_range(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    session: Session | None = None,
) -> IngestResult:
    results = [
        ingest_daily_summary_day(
            user_id=user_id,
            calendar_date=calendar_date,
            dry_run=dry_run,
            session=session,
        )
        for calendar_date in iter_dates(start_date, end_date)
    ]
    return aggregate_results(DAILY_SUMMARY, results)


def list_activity_ids(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    session: Session | None = None,
    raise_on_error: bool = True,
) -> list[int]:
    with _session_scope(session) as current_session:
        try:
            user = _get_user(current_session, user_id)
            client = _client_for_user(current_session, user)
            if client is None:
                raise RuntimeError("Failed to load tokens")

            raw_activities = client.get_activities_by_date(
                start_date.isoformat(),
                end_date.isoformat(),
            )
            if not raw_activities:
                return []
            if not dry_run:
                save_tokens(current_session, user, client.garmin)
            return [int(raw_activity["activityId"]) for raw_activity in raw_activities]
        except Exception:
            current_session.rollback()
            if raise_on_error:
                raise
            return []


def ingest_activity(
    *,
    user_id: int,
    activity_id: int,
    dry_run: bool = False,
    include_details: bool = True,
    include_files: bool = True,
    session: Session | None = None,
    raise_on_error: bool = False,
) -> IngestResult:
    with _session_scope(session) as current_session:
        detail_rows = 0
        detail_errors = 0
        file_failed = False
        try:
            user = _get_user(current_session, user_id)
            client = _client_for_user(current_session, user)
            if client is None:
                return IngestResult.error_result(
                    ACTIVITIES,
                    error="Failed to load tokens",
                    metrics={"detail_rows": 0, "detail_errors": 0},
                )

            raw_activity = client.get_activity(str(activity_id))
            activity = parse_activity(raw_activity, user.id)

            if not dry_run:
                upsert_activity(current_session, activity)

            if include_details:
                if _fetch_and_store_activity_detail(
                    current_session,
                    client,
                    activity,
                    dry_run=dry_run,
                ):
                    detail_rows += 1
                else:
                    detail_errors += 1

            if include_files and not dry_run:
                file_failed = not _download_and_store_file(
                    current_session,
                    client,
                    activity,
                )

            _save_tokens_and_mark_ingested(
                current_session,
                user,
                client,
                dry_run=dry_run,
            )

            status = "partial" if detail_errors or file_failed else "success"
            logger.debug(
                "Ingested activity %s (%s) for user %s",
                activity.activity_id,
                activity.activity_type,
                user.garmin_display_name,
            )
            return IngestResult(
                data_type=ACTIVITIES,
                status=status,
                rows=1,
                errors=0,
                metrics={
                    "detail_rows": detail_rows,
                    "detail_errors": detail_errors,
                },
            )
        except Exception as e:
            current_session.rollback()
            if raise_on_error:
                raise
            logger.warning(
                "Failed to process activity %s for user %s: %s",
                activity_id,
                user_id,
                e,
            )
            return IngestResult.error_result(
                ACTIVITIES,
                error=str(e),
                metrics={"detail_rows": detail_rows, "detail_errors": detail_errors},
            )


def ingest_activities_range(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    include_details: bool = True,
    include_files: bool = True,
    session: Session | None = None,
) -> IngestResult:
    try:
        activity_ids = list_activity_ids(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            session=session,
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch activities for user %s: %s",
            user_id,
            e,
        )
        return IngestResult.error_result(
            ACTIVITIES,
            error=str(e),
            metrics={"detail_rows": 0, "detail_errors": 0},
        )

    results: list[IngestResult] = []
    for activity_id in activity_ids:
        results.append(
            ingest_activity(
                user_id=user_id,
                activity_id=activity_id,
                dry_run=dry_run,
                include_details=include_details,
                include_files=include_files,
                session=session,
            )
        )
        time.sleep(1)

    if not results:
        return IngestResult.success(
            ACTIVITIES,
            rows=0,
            metrics={"detail_rows": 0, "detail_errors": 0},
        )
    return aggregate_results(ACTIVITIES, results)


def ingest_personal_records(
    *,
    user_id: int,
    dry_run: bool = False,
    session: Session | None = None,
    raise_on_error: bool = False,
) -> IngestResult:
    with _session_scope(session) as current_session:
        rows = 0
        errors = 0
        try:
            user = _get_user(current_session, user_id)
            client = _client_for_user(current_session, user)
            if client is None:
                return IngestResult.error_result(
                    PERSONAL_RECORDS,
                    error="Failed to load tokens",
                )

            raw_records = client.get_personal_records()
            for raw_record in raw_records:
                try:
                    personal_record = parse_personal_record(raw_record, user.id)
                    if not dry_run:
                        upsert_personal_record(current_session, personal_record)
                    rows += 1
                except Exception as e:
                    errors += 1
                    logger.warning(
                        "Failed to process personal record for user %s: %s",
                        user.garmin_display_name,
                        e,
                    )

            _save_tokens_and_mark_ingested(
                current_session,
                user,
                client,
                dry_run=dry_run,
            )
            return IngestResult.from_counts(
                PERSONAL_RECORDS,
                rows=rows,
                errors=errors,
            )
        except Exception as e:
            current_session.rollback()
            if raise_on_error:
                raise
            logger.warning(
                "Failed to fetch personal records for user %s: %s",
                user_id,
                e,
            )
            return IngestResult.error_result(PERSONAL_RECORDS, error=str(e))


def ingest_selected_objects(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    data_types: list[str] | None = None,
    include_details: bool = True,
    include_files: bool = True,
    session: Session | None = None,
) -> IngestSummary:
    selected_data_types = normalize_data_types(data_types)
    results: list[IngestResult] = []

    for data_type in selected_data_types:
        if data_type == DAILY_SUMMARY:
            results.append(
                ingest_daily_summary_range(
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    dry_run=dry_run,
                    session=session,
                )
            )
        elif data_type == ACTIVITIES:
            results.append(
                ingest_activities_range(
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    dry_run=dry_run,
                    include_details=include_details,
                    include_files=include_files,
                    session=session,
                )
            )
        elif data_type == PERSONAL_RECORDS:
            results.append(
                ingest_personal_records(
                    user_id=user_id,
                    dry_run=dry_run,
                    session=session,
                )
            )

    return IngestSummary(tuple(results))
