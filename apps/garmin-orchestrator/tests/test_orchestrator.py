from datetime import date

import pytest
from typer.testing import CliRunner

from garmin_orchestrator.cli import app
from garmin_orchestrator.flows import (
    _aggregate_result_dicts,
    _summarize_failures,
    garmin_archive_flow,
    garmin_archive_user_flow,
)


def test_aggregates_child_results_without_losing_metrics():
    result = _aggregate_result_dicts(
        "activities",
        [
            {
                "status": "success",
                "rows": 1,
                "errors": 0,
                "detail_rows": 1,
                "detail_errors": 0,
            },
            {
                "status": "partial",
                "rows": 1,
                "errors": 0,
                "detail_rows": 0,
                "detail_errors": 1,
            },
        ],
    )

    assert result == {
        "status": "partial",
        "rows": 2,
        "errors": 0,
        "detail_errors": 1,
        "detail_rows": 1,
    }


def test_failure_policy_allows_partial_by_default():
    assert _summarize_failures(
        [{"user": "mike", "activities": {"status": "partial"}}],
        fail_on_partial=False,
    ) == (0, 1)


def test_failure_policy_can_fail_on_partial():
    with pytest.raises(RuntimeError, match="0 error object\\(s\\) and 1 partial"):
        _summarize_failures(
            [{"user": "mike", "activities": {"status": "partial"}}],
            fail_on_partial=True,
        )


def test_failure_policy_fails_on_error():
    with pytest.raises(RuntimeError, match="1 error object"):
        _summarize_failures(
            [{"user": "mike", "daily_summary": {"status": "error"}}],
            fail_on_partial=False,
        )


def test_user_flow_runs_selected_objects_sequentially(monkeypatch):
    calls = []

    def fake_daily_task(*, user_id, calendar_date, dry_run):
        calls.append(("daily", user_id, calendar_date, dry_run))
        return {"status": "success", "rows": 1, "errors": 0}

    def fake_list_activities_task(*, user_id, start_date, end_date):
        calls.append(("list", user_id, start_date, end_date))
        return [1001, 1002]

    def fake_activity_task(
        *,
        user_id,
        activity_id,
        dry_run,
        include_details,
        include_files,
    ):
        calls.append((
            "activity",
            user_id,
            activity_id,
            dry_run,
            include_details,
            include_files,
        ))
        return {
            "status": "success",
            "rows": 1,
            "errors": 0,
            "detail_rows": 1,
            "detail_errors": 0,
        }

    def fake_records_task(*, user_id, dry_run):
        calls.append(("records", user_id, dry_run))
        return {"status": "success", "rows": 3, "errors": 0}

    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_daily_summary_day_task",
        fake_daily_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.list_activity_ids_task",
        fake_list_activities_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_task",
        fake_activity_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_personal_records_task",
        fake_records_task,
    )

    result = garmin_archive_user_flow.fn(
        user_ref={"id": 7, "display_name": "mike"},
        data_types=["daily_summary", "activities", "personal_records"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        dry_run=True,
        include_details=True,
        include_files=False,
    )

    assert result["user"] == "mike"
    assert result["daily_summary"] == {"status": "success", "rows": 2, "errors": 0}
    assert result["activities"] == {
        "status": "success",
        "rows": 2,
        "errors": 0,
        "detail_errors": 0,
        "detail_rows": 2,
    }
    assert result["personal_records"] == {
        "status": "success",
        "rows": 3,
        "errors": 0,
    }
    assert calls == [
        ("daily", 7, date(2026, 6, 1), True),
        ("daily", 7, date(2026, 6, 2), True),
        ("list", 7, date(2026, 6, 1), date(2026, 6, 2)),
        ("activity", 7, 1001, True, True, False),
        ("activity", 7, 1002, True, True, False),
        ("records", 7, True),
    ]


def test_archive_flow_returns_structured_summary(monkeypatch):
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ensure_database_ready_task",
        lambda: None,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.resolve_date_window_task",
        lambda **_: {"start_date": date(2026, 6, 1), "end_date": date(2026, 6, 1)},
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.resolve_active_users_task",
        lambda **_: [{"id": 7, "display_name": "mike"}],
    )

    def fake_user_flow(**kwargs):
        assert kwargs["data_types"] == ["daily_summary"]
        return {
            "user": "mike",
            "daily_summary": {"status": "success", "rows": 1, "errors": 0},
        }

    monkeypatch.setattr(
        "garmin_orchestrator.flows.garmin_archive_user_flow",
        fake_user_flow,
    )

    result = garmin_archive_flow.fn(
        data_types=["daily-summary"],
        days_back=1,
        dry_run=True,
    )

    assert result == {
        "window": {
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
        },
        "data_types": ["daily_summary"],
        "dry_run": True,
        "errors": 0,
        "partials": 0,
        "results": [
            {
                "user": "mike",
                "daily_summary": {"status": "success", "rows": 1, "errors": 0},
            }
        ],
    }


def test_cli_rejects_invalid_data_type_before_flow(monkeypatch):
    def fail_flow(**_):
        raise AssertionError("flow should not run")

    monkeypatch.setattr("garmin_orchestrator.cli.garmin_archive_flow", fail_flow)

    result = CliRunner().invoke(
        app,
        ["run", "archive", "--data-type", "sleep"],
    )

    assert result.exit_code == 1
    assert "Unknown ingest data type 'sleep'" in result.output
