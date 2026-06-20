from __future__ import annotations

import logging
from datetime import date
from typing import Any

from prefect import flow

from garmin_sync.ingest.date_windows import iter_dates
from garmin_sync.ingest.object_registry import (
    ACTIVITIES,
    DAILY_SUMMARY,
    PERSONAL_RECORDS,
    normalize_data_types,
)
from garmin_sync.ingest.results import IngestResult, aggregate_results

from garmin_orchestrator.tasks import (
    ensure_database_ready_task,
    ingest_activity_task,
    ingest_daily_summary_day_task,
    ingest_personal_records_task,
    list_activity_summaries_task,
    resolve_active_users_task,
    resolve_date_window_task,
)


RESULT_METADATA_KEYS = {"status", "rows", "errors", "error"}
logger = logging.getLogger(__name__)


def _dict_to_ingest_result(data_type: str, result: dict[str, Any]) -> IngestResult:
    metrics = {
        key: value
        for key, value in result.items()
        if key not in RESULT_METADATA_KEYS and isinstance(value, int)
    }
    return IngestResult(
        data_type=data_type,
        status=result["status"],
        rows=int(result.get("rows", 0)),
        errors=int(result.get("errors", 0)),
        metrics=metrics,
        error=result.get("error"),
    )


def _aggregate_result_dicts(
    data_type: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not results:
        return IngestResult.success(data_type).as_dict()

    return aggregate_results(
        data_type,
        [_dict_to_ingest_result(data_type, result) for result in results],
    ).as_dict()


def _summarize_failures(
    results: list[dict[str, Any]],
    *,
    fail_on_partial: bool,
) -> tuple[int, int]:
    errors = 0
    partials = 0
    for user_result in results:
        for data_type, info in user_result.items():
            if data_type == "user":
                continue
            status = info.get("status")
            if status == "error":
                errors += 1
            elif status == "partial":
                partials += 1

    if errors or (fail_on_partial and partials):
        raise RuntimeError(
            "Garmin archive flow completed with "
            f"{errors} error object(s) and {partials} partial object(s)"
        )

    return errors, partials


@flow(name="garmin-archive-user")
def garmin_archive_user_flow(
    *,
    user_ref: dict[str, Any],
    data_types: list[str],
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    include_details: bool = True,
    include_files: bool = True,
) -> dict[str, Any]:
    user_id = int(user_ref["id"])
    result: dict[str, Any] = {"user": user_ref["display_name"]}

    if DAILY_SUMMARY in data_types:
        daily_results = [
            ingest_daily_summary_day_task(
                user_id=user_id,
                calendar_date=calendar_date,
                dry_run=dry_run,
            )
            for calendar_date in iter_dates(start_date, end_date)
        ]
        result[DAILY_SUMMARY] = _aggregate_result_dicts(
            DAILY_SUMMARY,
            daily_results,
        )

    if ACTIVITIES in data_types:
        activity_summaries = list_activity_summaries_task(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
        )
        logger.info(
            "Resolved %s activity summary(s) for %s",
            len(activity_summaries),
            user_ref["display_name"],
        )
        activity_results = [
            ingest_activity_task(
                user_id=user_id,
                activity_id=int(activity_summary["activityId"]),
                dry_run=dry_run,
                include_details=include_details,
                include_files=include_files,
                activity_summary=activity_summary,
            )
            for activity_summary in activity_summaries
        ]
        result[ACTIVITIES] = _aggregate_result_dicts(ACTIVITIES, activity_results)

    if PERSONAL_RECORDS in data_types:
        result[PERSONAL_RECORDS] = ingest_personal_records_task(
            user_id=user_id,
            dry_run=dry_run,
        )

    return result


@flow(name="garmin-archive")
def garmin_archive_flow(
    *,
    user: str | None = None,
    data_types: list[str] | None = None,
    days_back: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = False,
    fail_on_partial: bool = False,
    include_details: bool = True,
    include_files: bool = True,
) -> dict[str, Any]:
    ensure_database_ready_task()
    window = resolve_date_window_task(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
    )
    selected_data_types = normalize_data_types(data_types)
    users = resolve_active_users_task(user_filter=user)

    results = [
        garmin_archive_user_flow(
            user_ref=user_ref,
            data_types=selected_data_types,
            start_date=window["start_date"],
            end_date=window["end_date"],
            dry_run=dry_run,
            include_details=include_details,
            include_files=include_files,
        )
        for user_ref in users
    ]

    errors, partials = _summarize_failures(
        results,
        fail_on_partial=fail_on_partial,
    )
    return {
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
