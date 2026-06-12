import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from garmin_postgres.auth import load_user_client, save_tokens
from garmin_postgres.config import get_settings
from garmin_postgres.ingest.client import GarminClient
from garmin_postgres.ingest.parsers.activity import parse_activity
from garmin_postgres.ingest.parsers.daily_summary import parse_daily_summary
from garmin_postgres.models.activity import Activity
from garmin_postgres.models.activity_file import ActivityFile
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.user import User

logger = logging.getLogger(__name__)


def upsert_daily_summary(session: Session, summary: DailySummary) -> DailySummary:
    """Insert or update a daily summary row using ON CONFLICT DO UPDATE.

    The unique key is (user_id, calendar_date). On conflict, all non-key
    columns are updated from the new data.
    """
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
    """Insert or update an activity row using ON CONFLICT DO UPDATE.

    The unique key is (user_id, activity_id). On conflict, update
    activity_type, start_time, and raw_json.
    """
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

    # Refresh to get the DB-assigned id (local PK) for FK references
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
    """Insert or update an activity file row using ON CONFLICT DO UPDATE.

    The unique key is (activity_id, file_format). On conflict, update
    file_data and raw_json.
    """
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


def _download_and_store_file(
    session: Session,
    client: GarminClient,
    activity: Activity,
) -> None:
    """Download and store an activity's original file (FIT in ZIP).

    Logs warnings on failure, never raises.
    """
    try:
        file_data = client.download_activity(str(activity.activity_id))
        af = ActivityFile(
            activity_id=activity.id,
            file_format="fit",
            file_data=file_data,
            raw_json={
                "source_format": "original",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        upsert_activity_file(session, af)
    except Exception as e:
        logger.warning(
            "Failed to download file for activity %s: %s",
            activity.activity_id,
            e,
        )


def run_ingestion(
    session: Session,
    user: User,
    start_date: date,
    end_date: date,
    *,
    dry_run: bool = False,
    data_types: list[str] | None = None,
) -> dict:
    """Run ingestion for a single user over a date range.

    Args:
        session: Database session.
        user: User to ingest data for.
        start_date: First date to fetch (inclusive).
        end_date: Last date to fetch (inclusive).
        dry_run: If True, fetch data but don't write to DB.
        data_types: List of data types to ingest (e.g. ["daily_summary", "activities"]).
                    If None, all types are ingested.

    Returns:
        Dict with ingestion results per data type.
    """
    garmin = load_user_client(session, user)
    if garmin is None:
        logger.error("Failed to load client for user %s", user.garmin_display_name)
        return {"daily_summary": {"status": "error", "error": "Failed to load tokens"}}

    client = GarminClient(garmin)
    results: dict = {}

    ingest_daily = data_types is None or "daily_summary" in data_types
    ingest_activities = data_types is None or "activities" in data_types

    # Daily summary ingestion
    if ingest_daily:
        current_date = start_date
        rows = 0
        errors = 0

        while current_date <= end_date:
            try:
                raw = client.get_daily_summary(current_date.isoformat())
                summary = parse_daily_summary(raw, user.id)

                if not dry_run:
                    upsert_daily_summary(session, summary)
                rows += 1
                logger.debug(
                    "Ingested daily summary for %s (user %s)",
                    current_date,
                    user.garmin_display_name,
                )
            except Exception as e:
                errors += 1
                logger.warning(
                    "Failed to fetch daily summary for %s (user %s): %s",
                    current_date,
                    user.garmin_display_name,
                    e,
                )
            current_date += timedelta(days=1)

        status = "success" if errors == 0 else ("partial" if rows > 0 else "error")
        results["daily_summary"] = {"status": status, "rows": rows, "errors": errors}

    # Activity ingestion
    if ingest_activities:
        try:
            raw_activities = client.get_activities_by_date(
                start_date.isoformat(),
                end_date.isoformat(),
            )
            act_rows = 0
            act_errors = 0

            for raw_act in raw_activities:
                try:
                    activity_id = raw_act.get("activityId")
                    if activity_id is None:
                        raise KeyError("activityId missing from activity summary")

                    # Fetch detailed activity data (richer than the list summary)
                    try:
                        raw_detail = client.get_activity(str(activity_id))
                    except Exception as detail_err:
                        logger.warning(
                            "Failed to fetch detail for activity %s, using summary: %s",
                            activity_id,
                            detail_err,
                        )
                        raw_detail = raw_act

                    activity = parse_activity(raw_detail, user.id)

                    if not dry_run:
                        upsert_activity(session, activity)
                        _download_and_store_file(session, client, activity)

                    act_rows += 1
                    logger.debug(
                        "Ingested activity %s (%s) for user %s",
                        activity.activity_id,
                        activity.activity_type,
                        user.garmin_display_name,
                    )

                    # Small delay between activity detail fetches to avoid rate limits
                    time.sleep(1)
                except Exception as e:
                    act_errors += 1
                    logger.warning(
                        "Failed to process activity for user %s: %s",
                        user.garmin_display_name,
                        e,
                    )
        except Exception as e:
            act_rows = 0
            act_errors = 1
            logger.warning(
                "Failed to fetch activities for user %s: %s",
                user.garmin_display_name,
                e,
            )

        status = "success" if act_errors == 0 else ("partial" if act_rows > 0 else "error")
        results["activities"] = {"status": status, "rows": act_rows, "errors": act_errors}

    if not dry_run:
        save_tokens(session, user, client.garmin)
        user.last_ingest_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()

    return results


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
) -> list[dict]:
    """Run ingestion for all active users.

    Args:
        session: Database session.
        start_date: Explicit start date. If None, calculated from days_back.
        end_date: Explicit end date. Defaults to yesterday.
        days_back: Number of days to look back. Defaults to config setting.
        user_filter: Optional display name to filter to a single user.
        dry_run: If True, fetch but don't write.
        data_types: List of data types to ingest. If None, all types.

    Returns:
        List of result dicts, one per user.
    """
    if end_date is None:
        end_date = date.today() - timedelta(days=1)

    if start_date is None:
        if days_back is None:
            settings = get_settings()
            days_back = settings.ingest_days_back
        start_date = end_date - timedelta(days=days_back)

    users = get_active_users(session, user_filter)
    if not users:
        logger.info("No active users found")
        return []

    all_results = []
    for user in users:
        logger.info(
            "Ingesting data for user %s (%s to %s)%s",
            user.garmin_display_name,
            start_date,
            end_date,
            " (dry run)" if dry_run else "",
        )
        result = run_ingestion(
            session,
            user,
            start_date,
            end_date,
            dry_run=dry_run,
            data_types=data_types,
        )
        all_results.append({
            "user": user.garmin_display_name,
            **result,
        })

    return all_results
