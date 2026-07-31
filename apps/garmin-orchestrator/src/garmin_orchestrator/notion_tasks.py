from __future__ import annotations

import logging
from datetime import date
from typing import Any

from notion_client import Client
from prefect import get_run_logger, task
from prefect.exceptions import MissingContextError
from sqlmodel import Session

from garmin_postgres.db import get_engine
from notion_sync.config import get_settings as get_notion_settings
from notion_sync.notion import NotionSink
from notion_sync.sync import run_sync


NOTION_SYNC_TIMEOUT_SECONDS = 60 * 60
PERSONAL_RECORDS = "personal_records"
logger = logging.getLogger(__name__)


def _get_logger():
    """Use Prefect's run logger, while keeping direct function calls usable."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


@task(
    name="sync-notion-user",
    task_run_name="notion-sync-{user}",
    timeout_seconds=NOTION_SYNC_TIMEOUT_SECONDS,
)
def sync_notion_user_task(
    *,
    user: str,
    data_types: list[str],
    start_date: date,
    end_date: date,
    dry_run: bool = False,
) -> dict[str, dict[str, Any]]:
    """Sync one Garmin user's archived rows to configured Notion databases.

    Activities and daily steps use the incremental date window. Personal
    records are a current snapshot in the reference integration, so every
    stored record is replayed in chronological order and the latest value wins.
    """
    run_logger = _get_logger()
    settings = get_notion_settings()
    if not settings.token and not dry_run:
        raise ValueError("NOTION_TOKEN is required unless dry_run is enabled")

    run_logger.info(
        "Starting Notion sync: user=%s window=%s..%s data_types=%s dry_run=%s",
        user,
        start_date,
        end_date,
        data_types,
        dry_run,
    )

    client = Client(auth=settings.token or "dry-run")
    sink = NotionSink(client, dry_run=dry_run)
    dated_data_types = [
        data_type for data_type in data_types if data_type != PERSONAL_RECORDS
    ]

    engine = get_engine()
    with Session(engine) as session:
        results: dict[str, dict[str, Any]] = {}
        if dated_data_types:
            results.update(
                run_sync(
                    session,
                    sink,
                    settings,
                    data_types=dated_data_types,
                    start_date=start_date,
                    end_date=end_date,
                    user_filter=user,
                )
            )
        if PERSONAL_RECORDS in data_types:
            results.update(
                run_sync(
                    session,
                    sink,
                    settings,
                    data_types=[PERSONAL_RECORDS],
                    user_filter=user,
                )
            )

    ordered_results = {
        data_type: results[data_type]
        for data_type in data_types
        if data_type in results
    }
    run_logger.info("Notion sync finished: user=%s results=%s", user, ordered_results)
    return ordered_results
