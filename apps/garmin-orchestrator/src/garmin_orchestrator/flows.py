from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.context import FlowRunContext
from prefect.deployments import run_deployment
from prefect.exceptions import MissingContextError

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
    ingest_activity_detail_task,
    ingest_activity_file_task,
    ingest_activity_summary_task,
    ingest_daily_summary_day_task,
    ingest_personal_records_task,
    list_activity_summaries_task,
    resolve_active_users_task,
    resolve_date_window_task,
)


RESULT_METADATA_KEYS = {"status", "rows", "errors", "error"}
ARCHIVE_FLOW_TIMEOUT_SECONDS = 3 * 60 * 60
ARCHIVE_USER_FLOW_TIMEOUT_SECONDS = ARCHIVE_FLOW_TIMEOUT_SECONDS - (15 * 60)
BACKFILL_FLOW_TIMEOUT_SECONDS = ARCHIVE_FLOW_TIMEOUT_SECONDS + (15 * 60)
DEFAULT_BACKFILL_CHUNK_DAYS = 30
MAX_BACKFILL_CHUNK_DAYS = 90
BACKFILL_DEPLOYMENT_NAME = "garmin-backfill/backfill"
logger = logging.getLogger(__name__)


def _get_logger():
    """Use Prefect's run logger, while keeping direct function calls usable."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logger


def _resolve_backfill_chain_id(chain_id: str | None) -> str:
    if chain_id:
        return chain_id

    context = FlowRunContext.get()
    if context and context.flow_run:
        return str(context.flow_run.id)
    return str(uuid4())


def _backfill_chunk_end(
    start_date: date,
    end_date: date,
    *,
    chunk_days: int,
) -> date:
    if not 1 <= chunk_days <= MAX_BACKFILL_CHUNK_DAYS:
        raise ValueError(f"chunk_days must be between 1 and {MAX_BACKFILL_CHUNK_DAYS}")
    return min(end_date, start_date + timedelta(days=chunk_days - 1))


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

    aggregated = aggregate_results(
        data_type,
        [_dict_to_ingest_result(data_type, result) for result in results],
    ).as_dict()
    error_messages = list(
        dict.fromkeys(
            result["error"]
            for result in results
            if result.get("error")
        )
    )
    if error_messages:
        aggregated["error"] = "; ".join(error_messages)
    return aggregated


def _task_state_result(
    state: Any,
    *,
    data_type: str,
    failure_metrics: dict[str, int] | None = None,
) -> dict[str, Any]:
    if state.is_completed():
        return state.result()

    error = state.result(raise_on_failure=False)
    return IngestResult.error_result(
        data_type,
        error=str(error),
        metrics=failure_metrics,
    ).as_dict()


def _count_failures(results: list[dict[str, Any]]) -> tuple[int, int]:
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
    return errors, partials


def _summarize_failures(
    results: list[dict[str, Any]],
    *,
    fail_on_partial: bool,
) -> tuple[int, int]:
    errors, partials = _count_failures(results)

    if errors or (fail_on_partial and partials):
        raise RuntimeError(
            "Garmin archive flow completed with "
            f"{errors} error object(s) and {partials} partial object(s)"
        )

    return errors, partials


def _escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _summary_markdown(summary: dict[str, Any]) -> str:
    window = summary["window"]
    lines = [
        "# Garmin archive ingestion",
        "",
        (
            f"Window: `{window['start_date']}` through `{window['end_date']}`  \n"
            f"Dry run: `{summary['dry_run']}`  \n"
            f"Errors: `{summary['errors']}`  \n"
            f"Partials: `{summary['partials']}`"
        ),
        "",
        "| User | Object | Status | Rows | Errors | Metrics | Error |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for user_result in summary["results"]:
        user = user_result["user"]
        for data_type, info in user_result.items():
            if data_type == "user":
                continue
            metrics = ", ".join(
                f"{key}={value}"
                for key, value in sorted(info.items())
                if key not in RESULT_METADATA_KEYS and isinstance(value, int)
            )
            lines.append(
                "| "
                + " | ".join(
                    _escape_markdown_cell(value)
                    for value in (
                        user,
                        data_type,
                        info["status"],
                        info.get("rows", 0),
                        info.get("errors", 0),
                        metrics,
                        info.get("error", ""),
                    )
                )
                + " |"
            )
    return "\n".join(lines)


def _publish_summary_artifact(summary: dict[str, Any]) -> None:
    try:
        create_markdown_artifact(
            key="garmin-archive-summary",
            markdown=_summary_markdown(summary),
            description="Per-user Garmin archive ingestion results",
        )
    except Exception:
        logger.warning("Failed to publish Garmin archive summary artifact", exc_info=True)


@flow(
    name="garmin-archive-user",
    timeout_seconds=ARCHIVE_USER_FLOW_TIMEOUT_SECONDS,
)
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
    run_logger = _get_logger()
    result: dict[str, Any] = {"user": user_ref["display_name"]}

    run_logger.info(
        "Starting archive for user=%s user_id=%s window=%s..%s data_types=%s "
        "dry_run=%s include_details=%s include_files=%s",
        user_ref["display_name"],
        user_id,
        start_date,
        end_date,
        data_types,
        dry_run,
        include_details,
        include_files,
    )

    if DAILY_SUMMARY in data_types:
        day_count = (end_date - start_date).days + 1
        run_logger.info(
            "Ingesting %s daily summary day(s) for user=%s",
            day_count,
            user_ref["display_name"],
        )
        daily_results = []
        for calendar_date in iter_dates(start_date, end_date):
            state = ingest_daily_summary_day_task(
                user_id=user_id,
                calendar_date=calendar_date,
                dry_run=dry_run,
                return_state=True,
            )
            daily_results.append(
                _task_state_result(state, data_type=DAILY_SUMMARY)
            )
        result[DAILY_SUMMARY] = _aggregate_result_dicts(
            DAILY_SUMMARY,
            daily_results,
        )
        run_logger.info(
            "Daily-summary archive result for user=%s: %s",
            user_ref["display_name"],
            result[DAILY_SUMMARY],
        )

    if ACTIVITIES in data_types:
        run_logger.info(
            "Listing activity summaries for user=%s window=%s..%s",
            user_ref["display_name"],
            start_date,
            end_date,
        )
        list_state = list_activity_summaries_task(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            return_state=True,
        )
        activity_results: list[dict[str, Any]] = []
        if not list_state.is_completed():
            activity_results.append(
                _task_state_result(
                    list_state,
                    data_type=ACTIVITIES,
                    failure_metrics={
                        "detail_rows": 0,
                        "detail_errors": 0,
                        "file_rows": 0,
                        "file_errors": 0,
                    },
                )
            )
        else:
            activity_summaries = list_state.result()
            run_logger.info(
                "Resolved %s activity summary(s) for user=%s",
                len(activity_summaries),
                user_ref["display_name"],
            )
            for activity_summary in activity_summaries:
                try:
                    activity_id = int(activity_summary["activityId"])
                except Exception as exc:
                    activity_results.append(
                        IngestResult.error_result(
                            ACTIVITIES,
                            error=str(exc),
                            metrics={
                                "detail_rows": 0,
                                "detail_errors": 0,
                                "file_rows": 0,
                                "file_errors": 0,
                            },
                        ).as_dict()
                    )
                    continue

                summary_state = ingest_activity_summary_task(
                    user_id=user_id,
                    activity_id=activity_id,
                    dry_run=dry_run,
                    activity_summary=activity_summary,
                    return_state=True,
                )
                activity_parts = [
                    _task_state_result(
                        summary_state,
                        data_type=ACTIVITIES,
                        failure_metrics={
                            "detail_rows": 0,
                            "detail_errors": 0,
                            "file_rows": 0,
                            "file_errors": 0,
                        },
                    )
                ]
                if activity_parts[0]["status"] == "success":
                    if include_details:
                        detail_state = ingest_activity_detail_task(
                            user_id=user_id,
                            activity_id=activity_id,
                            dry_run=dry_run,
                            return_state=True,
                        )
                        activity_parts.append(
                            _task_state_result(
                                detail_state,
                                data_type=ACTIVITIES,
                                failure_metrics={
                                    "detail_rows": 0,
                                    "detail_errors": 1,
                                },
                            )
                        )

                    if include_files and not dry_run:
                        file_state = ingest_activity_file_task(
                            user_id=user_id,
                            activity_id=activity_id,
                            dry_run=dry_run,
                            return_state=True,
                        )
                        activity_parts.append(
                            _task_state_result(
                                file_state,
                                data_type=ACTIVITIES,
                                failure_metrics={"file_rows": 0, "file_errors": 1},
                            )
                        )

                activity_results.append(
                    _aggregate_result_dicts(ACTIVITIES, activity_parts)
                )
        result[ACTIVITIES] = _aggregate_result_dicts(ACTIVITIES, activity_results)
        run_logger.info(
            "Activity archive result for user=%s: %s",
            user_ref["display_name"],
            result[ACTIVITIES],
        )

    if PERSONAL_RECORDS in data_types:
        run_logger.info(
            "Ingesting personal records for user=%s",
            user_ref["display_name"],
        )
        records_state = ingest_personal_records_task(
            user_id=user_id,
            dry_run=dry_run,
            return_state=True,
        )
        result[PERSONAL_RECORDS] = _task_state_result(
            records_state,
            data_type=PERSONAL_RECORDS,
        )

        run_logger.info(
            "Personal-record archive result for user=%s: %s",
            user_ref["display_name"],
            result[PERSONAL_RECORDS],
        )

    run_logger.info(
        "Finished archive for user=%s user_id=%s: %s",
        user_ref["display_name"],
        user_id,
        result,
    )

    return result


@flow(name="garmin-archive", timeout_seconds=ARCHIVE_FLOW_TIMEOUT_SECONDS)
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
    run_logger = _get_logger()
    run_logger.info("Checking database connectivity and applying pending migrations")
    ensure_database_ready_task()
    window = resolve_date_window_task(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
    )
    selected_data_types = normalize_data_types(data_types)
    users = resolve_active_users_task(user_filter=user)

    run_logger.info(
        "Starting Garmin archive: window=%s..%s users=%s data_types=%s dry_run=%s "
        "fail_on_partial=%s include_details=%s include_files=%s user_filter=%r",
        window["start_date"],
        window["end_date"],
        len(users),
        selected_data_types,
        dry_run,
        fail_on_partial,
        include_details,
        include_files,
        user,
    )
    if not users:
        run_logger.warning("No active users matched the archive request; no data was ingested")

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

    errors, partials = _count_failures(results)
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
    try:
        _summarize_failures(results, fail_on_partial=fail_on_partial)
    except RuntimeError:
        run_logger.exception(
            "Garmin archive completed with failures: results=%s fail_on_partial=%s",
            results,
            fail_on_partial,
        )
        raise

    run_logger.info(
        "Garmin archive completed: users=%s errors=%s partials=%s results=%s",
        len(users),
        errors,
        partials,
        results,
    )
    return summary


@flow(name="garmin-backfill", timeout_seconds=BACKFILL_FLOW_TIMEOUT_SECONDS)
def garmin_backfill_flow(
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
    chunk_days: int = DEFAULT_BACKFILL_CHUNK_DAYS,
    chain_id: str | None = None,
) -> dict[str, Any]:
    """Run one bounded backfill chunk and enqueue the remaining window.

    Each continuation is a separate deployment run. Completed chunks therefore
    remain completed if a later chunk fails or the worker host restarts.
    """
    run_logger = _get_logger()
    window = resolve_date_window_task(
        start_date=start_date,
        end_date=end_date,
        days_back=days_back,
    )
    selected_data_types = normalize_data_types(data_types)
    resolved_chain_id = _resolve_backfill_chain_id(chain_id)
    temporal_data_types = [
        data_type
        for data_type in selected_data_types
        if data_type in {DAILY_SUMMARY, ACTIVITIES}
    ]
    chunk_end = (
        _backfill_chunk_end(
            window["start_date"],
            window["end_date"],
            chunk_days=chunk_days,
        )
        if temporal_data_types
        else window["end_date"]
    )

    run_logger.info(
        "Starting backfill chunk: chain_id=%s requested_window=%s..%s "
        "chunk_window=%s..%s chunk_days=%s data_types=%s",
        resolved_chain_id,
        window["start_date"],
        window["end_date"],
        window["start_date"],
        chunk_end,
        chunk_days,
        selected_data_types,
    )
    summary = garmin_archive_flow(
        user=user,
        data_types=selected_data_types,
        start_date=window["start_date"],
        end_date=chunk_end,
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=include_details,
        include_files=include_files,
    )

    continuation_run_id: str | None = None
    next_start_date = chunk_end + timedelta(days=1)
    continuation_data_types = [
        data_type for data_type in selected_data_types if data_type != PERSONAL_RECORDS
    ]
    if next_start_date <= window["end_date"] and continuation_data_types:
        continuation_parameters = {
            "user": user,
            "data_types": continuation_data_types,
            "days_back": None,
            "start_date": next_start_date,
            "end_date": window["end_date"],
            "dry_run": dry_run,
            "fail_on_partial": fail_on_partial,
            "include_details": include_details,
            "include_files": include_files,
            "chunk_days": chunk_days,
            "chain_id": resolved_chain_id,
        }
        continuation = run_deployment(
            BACKFILL_DEPLOYMENT_NAME,
            parameters=continuation_parameters,
            flow_run_name=(
                f"backfill-{next_start_date.isoformat()}-through-"
                f"{window['end_date'].isoformat()}"
            ),
            timeout=0,
            tags=[
                "garmin-backfill",
                f"garmin-backfill-chain:{resolved_chain_id}",
            ],
            idempotency_key=(
                f"garmin-backfill:{resolved_chain_id}:{next_start_date.isoformat()}"
            ),
            as_subflow=False,
        )
        continuation_run_id = str(continuation.id)
        run_logger.info(
            "Enqueued next backfill chunk: chain_id=%s run_id=%s "
            "remaining_window=%s..%s data_types=%s",
            resolved_chain_id,
            continuation_run_id,
            next_start_date,
            window["end_date"],
            continuation_data_types,
        )
    else:
        run_logger.info(
            "Backfill chain completed: chain_id=%s requested_window=%s..%s",
            resolved_chain_id,
            window["start_date"],
            window["end_date"],
        )

    return {
        **summary,
        "backfill": {
            "chain_id": resolved_chain_id,
            "requested_window": {
                "start_date": window["start_date"].isoformat(),
                "end_date": window["end_date"].isoformat(),
            },
            "chunk_days": chunk_days,
            "continuation_run_id": continuation_run_id,
        },
    }
