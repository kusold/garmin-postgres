from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from prefect import get_run_logger, task
from prefect.exceptions import MissingContextError
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.config import get_settings
from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User
from garmin_sync.ingest.date_windows import resolve_date_window
from garmin_sync.ingest.results import IngestResult
from garmin_sync.ingest.runners import (
    ingest_activity,
    ingest_activity_detail,
    ingest_activity_file,
    ingest_daily_summary_day,
    ingest_personal_records,
    list_activity_summaries,
)


GARMIN_API_RETRIES = 3
GARMIN_API_RETRY_DELAYS = [60, 300, 900]
GARMIN_API_TAGS = ["garmin-api"]
GARMIN_API_TIMEOUT_SECONDS = 15 * 60
DATABASE_TIMEOUT_SECONDS = 5 * 60
DATABASE_QUERY_TIMEOUT_SECONDS = 2 * 60
logger = logging.getLogger(__name__)


def _get_logger():
    """Use Prefect's run logger, while keeping direct function calls usable."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


def _alembic_script_location():
    packaged_location = files("garmin_postgres").joinpath("alembic")
    if packaged_location.is_dir():
        return packaged_location
    raise FileNotFoundError("Could not find packaged Alembic migrations")


def _result_dict(result: IngestResult) -> dict[str, Any]:
    return result.as_dict()


@task(name="ensure-database-ready", timeout_seconds=DATABASE_TIMEOUT_SECONDS)
def ensure_database_ready_task() -> None:
    run_logger = _get_logger()
    try:
        engine = get_engine()
        run_logger.info("Checking database connection and applying migrations")
        with Session(engine) as session:
            session.connection()

        alembic_cfg = Config()
        alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
        script_location = _alembic_script_location()
        script_location_context = (
            nullcontext(script_location)
            if isinstance(script_location, Path)
            else as_file(script_location)
        )
        with script_location_context as location:
            alembic_cfg.set_main_option("script_location", str(location))
            command.upgrade(alembic_cfg, "head")
    except Exception:
        run_logger.exception("Database readiness check or migration failed")
        raise
    run_logger.info("Database is ready and migrations are current")


@task(name="resolve-date-window", timeout_seconds=DATABASE_QUERY_TIMEOUT_SECONDS)
def resolve_date_window_task(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days_back: int | None = None,
) -> dict[str, date]:
    run_logger = _get_logger()
    settings = get_settings()
    window = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
        default_days_back=settings.ingest_days_back,
    )
    result = {"start_date": window.start_date, "end_date": window.end_date}
    run_logger.info(
        "Resolved archive window=%s..%s from start_date=%s end_date=%s days_back=%s "
        "default_days_back=%s",
        window.start_date,
        window.end_date,
        start_date,
        end_date,
        days_back,
        settings.ingest_days_back,
    )
    return result


@task(name="resolve-active-users", timeout_seconds=DATABASE_QUERY_TIMEOUT_SECONDS)
def resolve_active_users_task(user_filter: str | None = None) -> list[dict[str, Any]]:
    run_logger = _get_logger()
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(User).where(User.is_active == True)  # noqa: E712
        if user_filter:
            stmt = stmt.where(User.garmin_display_name == user_filter)

        users = session.scalars(stmt).all()
        result = [
            {"id": user.id, "display_name": user.garmin_display_name}
            for user in users
        ]
    run_logger.info(
        "Resolved %s active user(s) for user_filter=%r: %s",
        len(result),
        user_filter,
        [user["display_name"] for user in result],
    )
    return result


@task(
    name="ingest-daily-summary-day",
    task_run_name="daily-summary-{user_id}-{calendar_date}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def ingest_daily_summary_day_task(
    *,
    user_id: int,
    calendar_date: date,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_logger = _get_logger()
    run_logger.info(
        "Ingesting daily summary: user_id=%s calendar_date=%s dry_run=%s",
        user_id,
        calendar_date,
        dry_run,
    )
    try:
        result = _result_dict(
            ingest_daily_summary_day(
                user_id=user_id,
                calendar_date=calendar_date,
                dry_run=dry_run,
                raise_on_error=True,
            )
        )
    except Exception:
        run_logger.exception(
            "Daily-summary ingestion failed: user_id=%s calendar_date=%s dry_run=%s",
            user_id,
            calendar_date,
            dry_run,
        )
        raise
    run_logger.info(
        "Daily-summary ingestion finished: user_id=%s calendar_date=%s result=%s",
        user_id,
        calendar_date,
        result,
    )
    return result


@task(
    name="list-activity-summaries",
    task_run_name="activity-list-{user_id}-{start_date}-{end_date}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def list_activity_summaries_task(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    run_logger = _get_logger()
    run_logger.info(
        "Listing activity summaries: user_id=%s window=%s..%s dry_run=%s",
        user_id,
        start_date,
        end_date,
        dry_run,
    )
    try:
        summaries = list_activity_summaries(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            raise_on_error=True,
        )
    except Exception:
        run_logger.exception(
            "Activity-summary listing failed: user_id=%s window=%s..%s",
            user_id,
            start_date,
            end_date,
        )
        raise
    run_logger.info(
        "Listed %s activity summary(s): user_id=%s",
        len(summaries),
        user_id,
    )
    return summaries


@task(
    name="ingest-activity-summary",
    task_run_name="activity-summary-{user_id}-{activity_id}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def ingest_activity_summary_task(
    *,
    user_id: int,
    activity_id: int,
    dry_run: bool = False,
    activity_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_logger = _get_logger()
    run_logger.info(
        "Ingesting activity summary: user_id=%s activity_id=%s dry_run=%s",
        user_id,
        activity_id,
        dry_run,
    )
    try:
        result = _result_dict(
            ingest_activity(
                user_id=user_id,
                activity_id=activity_id,
                dry_run=dry_run,
                include_details=False,
                include_files=False,
                activity_summary=activity_summary,
                raise_on_error=True,
            )
        )
    except Exception:
        run_logger.exception(
            "Activity-summary ingestion failed: user_id=%s activity_id=%s",
            user_id,
            activity_id,
        )
        raise
    run_logger.info(
        "Activity-summary ingestion finished: user_id=%s activity_id=%s result=%s",
        user_id,
        activity_id,
        result,
    )
    return result


@task(
    name="ingest-activity-detail",
    task_run_name="activity-detail-{user_id}-{activity_id}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def ingest_activity_detail_task(
    *,
    user_id: int,
    activity_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_logger = _get_logger()
    run_logger.info(
        "Ingesting activity detail: user_id=%s activity_id=%s dry_run=%s",
        user_id,
        activity_id,
        dry_run,
    )
    try:
        result = _result_dict(
            ingest_activity_detail(
                user_id=user_id,
                activity_id=activity_id,
                dry_run=dry_run,
                raise_on_error=True,
            )
        )
    except Exception:
        run_logger.exception(
            "Activity-detail ingestion failed: user_id=%s activity_id=%s",
            user_id,
            activity_id,
        )
        raise
    run_logger.info(
        "Activity-detail ingestion finished: user_id=%s activity_id=%s result=%s",
        user_id,
        activity_id,
        result,
    )
    return result


@task(
    name="download-activity-file",
    task_run_name="activity-file-{user_id}-{activity_id}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def ingest_activity_file_task(
    *,
    user_id: int,
    activity_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_logger = _get_logger()
    run_logger.info(
        "Downloading activity file: user_id=%s activity_id=%s dry_run=%s",
        user_id,
        activity_id,
        dry_run,
    )
    try:
        result = _result_dict(
            ingest_activity_file(
                user_id=user_id,
                activity_id=activity_id,
                dry_run=dry_run,
                raise_on_error=True,
            )
        )
    except Exception:
        run_logger.exception(
            "Activity-file download failed: user_id=%s activity_id=%s",
            user_id,
            activity_id,
        )
        raise
    run_logger.info(
        "Activity-file download finished: user_id=%s activity_id=%s result=%s",
        user_id,
        activity_id,
        result,
    )
    return result


@task(
    name="ingest-personal-records",
    task_run_name="personal-records-{user_id}",
    retries=GARMIN_API_RETRIES,
    retry_delay_seconds=GARMIN_API_RETRY_DELAYS,
    tags=GARMIN_API_TAGS,
    timeout_seconds=GARMIN_API_TIMEOUT_SECONDS,
)
def ingest_personal_records_task(
    *,
    user_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_logger = _get_logger()
    run_logger.info(
        "Ingesting personal records: user_id=%s dry_run=%s",
        user_id,
        dry_run,
    )
    try:
        result = _result_dict(
            ingest_personal_records(
                user_id=user_id,
                dry_run=dry_run,
                raise_on_error=True,
            )
        )
    except Exception:
        run_logger.exception("Personal-record ingestion failed: user_id=%s", user_id)
        raise
    run_logger.info(
        "Personal-record ingestion finished: user_id=%s result=%s",
        user_id,
        result,
    )
    return result
