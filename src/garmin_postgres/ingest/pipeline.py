import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from garmin_postgres.auth import load_user_client, save_tokens
from garmin_postgres.config import get_settings
from garmin_postgres.ingest.client import GarminClient
from garmin_postgres.ingest.parsers.daily_summary import parse_daily_summary
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


def run_ingestion(
    session: Session,
    user: User,
    start_date: date,
    end_date: date,
    *,
    dry_run: bool = False,
) -> dict:
    """Run ingestion for a single user over a date range.

    Args:
        session: Database session.
        user: User to ingest data for.
        start_date: First date to fetch (inclusive).
        end_date: Last date to fetch (inclusive).
        dry_run: If True, fetch data but don't write to DB.

    Returns:
        Dict with ingestion results per data type.
    """
    garmin = load_user_client(session, user)
    if garmin is None:
        logger.error("Failed to load client for user %s", user.garmin_display_name)
        return {"daily_summary": {"status": "error", "error": "Failed to load tokens"}}

    client = GarminClient(garmin)
    results: dict = {}

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

    if not dry_run:
        save_tokens(session, user, client.garmin)
        user.last_ingest_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()

    status = "success" if errors == 0 else ("partial" if rows > 0 else "error")
    results["daily_summary"] = {"status": status, "rows": rows, "errors": errors}
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
) -> list[dict]:
    """Run ingestion for all active users.

    Args:
        session: Database session.
        start_date: Explicit start date. If None, calculated from days_back.
        end_date: Explicit end date. Defaults to today.
        days_back: Number of days to look back. Defaults to config setting.
        user_filter: Optional display name to filter to a single user.
        dry_run: If True, fetch but don't write.

    Returns:
        List of result dicts, one per user.
    """
    if end_date is None:
        end_date = date.today()

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
            session, user, start_date, end_date, dry_run=dry_run
        )
        all_results.append({
            "user": user.garmin_display_name,
            **result,
        })

    return all_results
