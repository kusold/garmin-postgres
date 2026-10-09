"""Reconcile Garmin's full activity list with the local archive."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.activity_detail import ActivityDetail
from garmin_postgres.models.activity_file import ActivityFile
from garmin_sync.ingest.results import IngestResult
from garmin_sync.ingest.runners import (
    GarminTokenLoadError,
    _client_for_user,
    _get_user,
    _save_tokens_and_mark_ingested,
    _session_scope,
    ingest_activity,
    ingest_activity_detail,
    ingest_activity_file,
)


PAGE_SIZE = 1000
RECONCILIATION = "activity_reconciliation"


class ReconciliationActivityError(RuntimeError):
    """An activity failed after some archive work may have completed."""

    def __init__(self, result: IngestResult):
        self.result = result
        super().__init__(
            result.error or f"Activity archive had {result.errors} error(s)"
        )


def _summary_changed(summary: dict, stored: Activity) -> bool:
    raw = stored.raw_json or {}
    metadata = raw.get("metadataDTO")
    if not isinstance(metadata, dict):
        metadata = {}
    activity_type = summary.get("activityType") or summary.get("activityTypeDTO") or {}
    type_key = activity_type.get("typeKey") if isinstance(activity_type, dict) else None
    return (
        ("activityName" in summary and summary["activityName"] != raw.get("activityName"))
        or (type_key is not None and type_key != stored.activity_type)
        or (
            "favorite" in summary
            and bool(summary["favorite"])
            != bool(raw.get("favorite") or metadata.get("favorite"))
        )
        or (
            "pr" in summary
            and bool(summary["pr"])
            != bool(raw.get("pr") or metadata.get("personalRecord"))
        )
    )


def scan_activity_archive_page(
    *,
    user_id: int,
    offset: int,
    dry_run: bool = False,
    session: Session | None = None,
) -> dict[str, Any]:
    """Find activity work in one Garmin page without changing archive rows."""
    with _session_scope(session) as current_session:
        user = _get_user(current_session, user_id)
        client = _client_for_user(current_session, user)
        if client is None:
            raise GarminTokenLoadError("Failed to load tokens")

        page = client.get_activities(offset, PAGE_SIZE)
        page_items: list[tuple[int, dict]] = []
        errors: list[str] = []
        page_ids: set[int] = set()
        for summary in page:
            try:
                if not isinstance(summary, dict):
                    raise TypeError("activity summary is not an object")
                activity_id = int(summary["activityId"])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"Invalid activity summary: {exc}")
                continue
            if activity_id in page_ids:
                continue
            page_ids.add(activity_id)
            page_items.append((activity_id, summary))

        stored_by_id: dict[int, Activity] = {}
        if page_items:
            stored_by_id = {
                row.activity_id: row
                for row in current_session.scalars(
                    select(Activity).where(
                        Activity.user_id == user_id,
                        Activity.activity_id.in_(page_ids),
                    )
                ).all()
            }
        stored_row_ids = [row.id for row in stored_by_id.values() if row.id is not None]
        detail_ids: set[int] = set()
        file_ids: set[int] = set()
        if stored_row_ids:
            detail_ids = set(current_session.scalars(
                select(ActivityDetail.activity_id).where(
                    ActivityDetail.activity_id.in_(stored_row_ids)
                )
            ).all())
            file_ids = set(current_session.scalars(
                select(ActivityFile.activity_id).where(
                    ActivityFile.activity_id.in_(stored_row_ids),
                    ActivityFile.file_format == "fit",
                )
            ).all())

        candidates: list[dict[str, Any]] = []
        for activity_id, summary in page_items:
            stored = stored_by_id.get(activity_id)
            is_missing = stored is None
            is_changed = stored is not None and _summary_changed(summary, stored)
            needs_detail = is_missing or (
                not is_changed and stored.id not in detail_ids
            )
            needs_file = is_missing or (
                not is_changed and stored.id not in file_ids
            )
            is_incomplete = (
                not is_missing and not is_changed and (needs_detail or needs_file)
            )
            if is_missing or is_changed or is_incomplete:
                candidates.append({
                    "activity_id": activity_id,
                    "summary": summary,
                    "missing": is_missing,
                    "changed": is_changed,
                    "incomplete": is_incomplete,
                    "needs_detail": needs_detail,
                    "needs_file": needs_file,
                })

        if not dry_run:
            _save_tokens_and_mark_ingested(
                current_session, user, client, dry_run=False
            )
        return {"scanned": len(page), "errors": errors, "candidates": candidates}


def reconcile_activity_candidate(
    *,
    user_id: int,
    candidate: dict[str, Any],
    session: Session | None = None,
    raise_on_error: bool = False,
) -> IngestResult:
    """Apply one candidate without refreshing an unchanged activity row."""
    activity_id = candidate["activity_id"]
    if candidate["missing"] or candidate["changed"]:
        return ingest_activity(
            user_id=user_id,
            activity_id=activity_id,
            include_details=candidate["missing"],
            include_files=candidate["missing"],
            activity_summary=candidate["summary"],
            session=session,
            raise_on_error=raise_on_error,
        )

    steps: list[IngestResult] = []
    if candidate["needs_detail"]:
        steps.append(
            ingest_activity_detail(
                user_id=user_id, activity_id=activity_id, session=session,
            )
        )
    if candidate["needs_file"]:
        steps.append(
            ingest_activity_file(
                user_id=user_id, activity_id=activity_id, session=session,
            )
        )
    errors = sum(step.errors for step in steps)
    rows = int(any(step.status == "success" for step in steps))
    return IngestResult(
        data_type=RECONCILIATION,
        status="partial" if errors and rows else "error" if errors else "success",
        rows=rows,
        errors=errors,
        error="; ".join(step.error for step in steps if step.error) or None,
    )


def reconcile_activity_archive(
    *,
    user_id: int,
    dry_run: bool = False,
    session: Session | None = None,
) -> IngestResult:
    """Archive missing activities and refresh changed or incomplete ones."""
    with _session_scope(session) as current_session:
        return run_activity_reconciliation(
            dry_run=dry_run,
            scan_page=lambda offset: scan_activity_archive_page(
                user_id=user_id, offset=offset, dry_run=dry_run,
                session=current_session,
            ),
            archive_candidate=lambda candidate: reconcile_activity_candidate(
                user_id=user_id, candidate=candidate, session=current_session,
            ),
        )


def run_activity_reconciliation(
    *,
    dry_run: bool,
    scan_page: Callable[[int], dict[str, Any]],
    archive_candidate: Callable[[dict[str, Any]], IngestResult],
) -> IngestResult:
    """Apply the same pagination, counting, and failure policy for every runner."""
    scanned = missing = changed = incomplete = rows = errors = 0
    error_messages: list[str] = []
    seen_ids: set[int] = set()
    offset = 0
    while True:
        try:
            page = scan_page(offset)
        except Exception as exc:
            errors += 1
            error_messages.append(f"Archive scan failed at offset {offset}: {exc}")
            break
        scanned += page["scanned"]
        errors += len(page["errors"])
        error_messages.extend(page["errors"])
        for candidate in page["candidates"]:
            activity_id = candidate["activity_id"]
            if activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)
            missing += candidate["missing"]
            changed += candidate["changed"]
            incomplete += candidate["incomplete"]
            if dry_run:
                rows += 1
                continue
            try:
                result = archive_candidate(candidate)
            except Exception as exc:
                errors += 1
                error_messages.append(f"Activity {activity_id} failed: {exc}")
                continue
            rows += result.rows
            errors += result.errors
            if result.errors:
                error_messages.append(
                    result.error or f"Activity {activity_id} had {result.errors} error(s)"
                )
        if page["scanned"] < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return IngestResult(
        data_type=RECONCILIATION,
        status="partial" if errors and rows else "error" if errors else "success",
        rows=rows,
        errors=errors,
        metrics={
            "scanned": scanned,
            "missing": missing,
            "changed": changed,
            "incomplete": incomplete,
        },
        error="; ".join(error_messages) if error_messages else None,
    )
