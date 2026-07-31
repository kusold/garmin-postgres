from __future__ import annotations

import logging
from datetime import date
from typing import Any

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.exceptions import MissingContextError

from notion_sync.sync import DATA_TYPES

from garmin_orchestrator.notion_tasks import sync_notion_user_task
from garmin_orchestrator.tasks import (
    ensure_database_ready_task,
    resolve_active_users_task,
    resolve_date_window_task,
)


DEFAULT_NOTION_SYNC_DAYS_BACK = 2
NOTION_FLOW_TIMEOUT_SECONDS = 2 * 60 * 60
logger = logging.getLogger(__name__)


def _get_logger():
    """Use Prefect's run logger, while keeping direct function calls usable."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


def normalize_notion_data_types(data_types: list[str] | None) -> list[str]:
    selected = list(dict.fromkeys(data_types or DATA_TYPES))
    invalid = sorted(set(selected) - set(DATA_TYPES))
    if invalid:
        raise ValueError(
            "Unsupported Notion data type(s): "
            f"{', '.join(invalid)}. Expected one of: {', '.join(DATA_TYPES)}"
        )
    return selected


def _failure_counts(results: list[dict[str, Any]]) -> tuple[int, int]:
    errors = 0
    partials = 0
    for user_result in results:
        for data_type, info in user_result.items():
            if data_type == "user":
                continue
            if info.get("status") == "error":
                errors += 1
            elif info.get("status") == "partial":
                partials += 1
    return errors, partials


def _escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _summary_markdown(summary: dict[str, Any]) -> str:
    window = summary["window"]
    lines = [
        "# Garmin to Notion sync",
        "",
        (
            f"Dated row window: `{window['start_date']}` through `{window['end_date']}`  \n"
            f"Dry run: `{summary['dry_run']}`  \n"
            f"Errors: `{summary['errors']}`  \n"
            f"Partials: `{summary['partials']}`"
        ),
        "",
        "| User | Object | Status | Rows | Created | Updated | Skipped | Errors | Error |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for user_result in summary["results"]:
        user = user_result["user"]
        for data_type, info in user_result.items():
            if data_type == "user":
                continue
            lines.append(
                "| "
                + " | ".join(
                    _escape_markdown_cell(value)
                    for value in (
                        user,
                        data_type,
                        info.get("status", ""),
                        info.get("rows", 0),
                        info.get("created", 0),
                        info.get("updated", 0),
                        info.get("skipped", 0),
                        info.get("errors", 0),
                        info.get("error", ""),
                    )
                )
                + " |"
            )
    return "\n".join(lines)


def _publish_summary_artifact(summary: dict[str, Any]) -> None:
    try:
        create_markdown_artifact(
            key="garmin-notion-sync-summary",
            markdown=_summary_markdown(summary),
            description="Per-object PostgreSQL to Notion sync results",
        )
    except Exception:
        logger.warning("Failed to publish Notion sync summary artifact", exc_info=True)


@flow(name="garmin-notion-sync", timeout_seconds=NOTION_FLOW_TIMEOUT_SECONDS)
def notion_sync_flow(
    *,
    user: str | None = None,
    data_types: list[str] | None = None,
    days_back: int | None = DEFAULT_NOTION_SYNC_DAYS_BACK,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = False,
    fail_on_partial: bool = False,
) -> dict[str, Any]:
    """Sync archived PostgreSQL data for one Garmin user to Notion."""
    run_logger = _get_logger()
    selected_data_types = normalize_notion_data_types(data_types)

    ensure_database_ready_task()
    window = resolve_date_window_task(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
    )
    users = resolve_active_users_task(user_filter=user)
    if not users:
        raise ValueError(f"No active Garmin user matched user={user!r}")
    if user is None and len(users) != 1:
        raise ValueError(
            "Notion sync is single-user; pass user when more than one active "
            "Garmin user exists"
        )

    run_logger.info(
        "Starting PostgreSQL to Notion flow: window=%s..%s user=%s "
        "data_types=%s dry_run=%s fail_on_partial=%s",
        window["start_date"],
        window["end_date"],
        users[0]["display_name"],
        selected_data_types,
        dry_run,
        fail_on_partial,
    )
    notion_results = sync_notion_user_task(
        user=users[0]["display_name"],
        data_types=selected_data_types,
        start_date=window["start_date"],
        end_date=window["end_date"],
        dry_run=dry_run,
    )
    results = [{"user": users[0]["display_name"], **notion_results}]
    errors, partials = _failure_counts(results)
    summary = {
        "window": {
            "start_date": window["start_date"].isoformat(),
            "end_date": window["end_date"].isoformat(),
        },
        "data_types": selected_data_types,
        "dry_run": dry_run,
        "errors": errors,
        "partials": partials,
        "results": results,
    }
    _publish_summary_artifact(summary)

    if errors or (fail_on_partial and partials):
        raise RuntimeError(
            "Notion sync completed with "
            f"{errors} error object(s) and {partials} partial object(s)"
        )

    run_logger.info(
        "PostgreSQL to Notion flow completed: user=%s errors=%s partials=%s "
        "results=%s",
        users[0]["display_name"],
        errors,
        partials,
        notion_results,
    )
    return summary
