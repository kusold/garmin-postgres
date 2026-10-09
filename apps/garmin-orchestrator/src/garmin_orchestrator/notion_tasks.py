from __future__ import annotations

import logging
from datetime import date
from typing import Any

from prefect import get_run_logger, task
from prefect.exceptions import MissingContextError
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.db import get_engine
from garmin_postgres.models.sync_target import SyncTarget
from notion_sync.run import run_user_sync


NOTION_SYNC_TIMEOUT_SECONDS = 60 * 60
logger = logging.getLogger(__name__)


def _get_logger():
    """Use Prefect's run logger, while keeping direct function calls usable."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


@task(name="resolve-notion-configured-users")
def resolve_notion_configured_users_task(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter candidate users down to those with a 'notion' sync target row."""
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(SyncTarget.user_id).where(SyncTarget.target == "notion")
        configured_ids = set(session.scalars(stmt).all())
    return [u for u in candidates if u["id"] in configured_ids]


@task(
    name="sync-notion-user",
    task_run_name="notion-sync-{user_id}",
    timeout_seconds=NOTION_SYNC_TIMEOUT_SECONDS,
)
def sync_notion_user_task(
    *,
    user_id: int,
    data_types: list[str],
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    include_updated_activities: bool = True,
) -> dict[str, dict[str, Any]]:
    """Run one user's Notion sync within a Prefect task."""
    run_logger = _get_logger()
    run_logger.info(
        "Starting Notion sync: user_id=%s window=%s..%s data_types=%s dry_run=%s",
        user_id,
        start_date,
        end_date,
        data_types,
        dry_run,
    )
    results = run_user_sync(
        user_id,
        data_types=data_types,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        include_updated_activities=include_updated_activities,
    )
    run_logger.info("Notion sync finished: user_id=%s results=%s", user_id, results)
    return results
