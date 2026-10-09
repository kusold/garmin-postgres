"""Reconcile Garmin's full activity list with the local archive."""

from __future__ import annotations

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_sync.ingest.results import IngestResult
from garmin_sync.ingest.runners import (
    _client_for_user,
    _get_user,
    _save_tokens_and_mark_ingested,
    _session_scope,
    ingest_activity,
)


PAGE_SIZE = 1000
RECONCILIATION = "activity_reconciliation"


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


def reconcile_activity_archive(
    *,
    user_id: int,
    dry_run: bool = False,
    session: Session | None = None,
) -> IngestResult:
    """Archive missing activities and refresh changed activity rows."""
    with _session_scope(session) as current_session:
        user = _get_user(current_session, user_id)
        client = _client_for_user(current_session, user)
        if client is None:
            return IngestResult.error_result(
                RECONCILIATION, error="Failed to load tokens"
            )

        scanned = 0
        missing = 0
        changed = 0
        rows = 0
        errors = 0
        error_messages: list[str] = []
        seen_ids: set[int] = set()
        offset = 0
        while True:
            try:
                page = client.get_activities(offset, PAGE_SIZE)
            except Exception as exc:
                error_messages.append(f"Archive scan failed at offset {offset}: {exc}")
                errors += 1
                break
            scanned += len(page)
            page_items: list[tuple[int, dict]] = []
            for summary in page:
                try:
                    activity_id = int(summary["activityId"])
                except (KeyError, TypeError, ValueError) as exc:
                    errors += 1
                    error_messages.append(f"Invalid activity summary: {exc}")
                    continue
                if activity_id in seen_ids:
                    continue
                seen_ids.add(activity_id)
                page_items.append((activity_id, summary))

            stored_by_id: dict[int, Activity] = {}
            if page_items:
                page_ids = [activity_id for activity_id, _ in page_items]
                stored_by_id = {
                    row.activity_id: row
                    for row in current_session.scalars(
                        select(Activity).where(
                            Activity.user_id == user_id,
                            Activity.activity_id.in_(page_ids),
                        )
                    ).all()
                }
            candidates: list[tuple[int, dict, bool]] = []
            for activity_id, summary in page_items:
                stored = stored_by_id.get(activity_id)
                if stored is not None and not _summary_changed(summary, stored):
                    continue
                candidates.append((activity_id, summary, stored is None))

            for activity_id, summary, is_missing in candidates:
                if is_missing:
                    missing += 1
                else:
                    changed += 1
                if dry_run:
                    rows += 1
                    continue
                result = ingest_activity(
                    user_id=user_id,
                    activity_id=activity_id,
                    dry_run=False,
                    include_details=is_missing,
                    include_files=is_missing,
                    activity_summary=summary,
                    session=current_session,
                )
                rows += result.rows
                errors += result.errors
                if result.errors:
                    error_messages.append(
                        result.error or f"Activity {activity_id} had {result.errors} error(s)"
                    )
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        try:
            _save_tokens_and_mark_ingested(
                current_session, user, client, dry_run=dry_run
            )
        except Exception as exc:
            current_session.rollback()
            errors += 1
            error_messages.append(f"Failed to save Garmin tokens: {exc}")

        return IngestResult(
            data_type=RECONCILIATION,
            status="partial" if errors and rows else "error" if errors else "success",
            rows=rows,
            errors=errors,
            metrics={"scanned": scanned, "missing": missing, "changed": changed},
            error="; ".join(error_messages) if error_messages else None,
        )
