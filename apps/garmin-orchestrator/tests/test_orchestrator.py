from datetime import date
from types import SimpleNamespace

import pytest
from prefect.states import Failed
from typer.testing import CliRunner

from garmin_orchestrator import deployments, tasks
from garmin_orchestrator.cli import app
from garmin_orchestrator.flows import (
    MAX_BACKFILL_CHUNK_DAYS,
    _aggregate_result_dicts,
    _backfill_chunk_end,
    _summary_markdown,
    _summarize_failures,
    _task_state_result,
    garmin_archive_flow,
    garmin_archive_user_flow,
    garmin_backfill_flow,
)
from garmin_sync.ingest.results import IngestResult
from garmin_sync.ingest.runners import GarminTokenLoadError


class FakeState:
    def __init__(self, value, *, completed=True):
        self.value = value
        self.completed = completed

    def is_completed(self):
        return self.completed

    def result(self, *, raise_on_failure=True):
        if not self.completed and raise_on_failure:
            raise self.value
        return self.value


def test_retry_policy_skips_stored_token_failures():
    state = Failed(data=GarminTokenLoadError("invalid tokens"))

    assert tasks._retry_garmin_api_failure(None, None, state) is False


def test_retry_policy_keeps_transient_failures():
    state = Failed(data=RuntimeError("Garmin unavailable"))

    assert tasks._retry_garmin_api_failure(None, None, state) is True


def test_configure_work_pool_serializes_jobs_and_prioritizes_schedules(monkeypatch):
    calls = []

    class FakeClient:
        def update_work_pool(self, name, update):
            calls.append(("pool", name, update.concurrency_limit))

        def read_work_queue_by_name(self, name, *, work_pool_name):
            calls.append(("read", name, work_pool_name))
            if name == "backfill":
                raise deployments.ObjectNotFound("missing")
            return SimpleNamespace(id="scheduled-id")

        def create_work_queue(self, *, name, priority, work_pool_name):
            calls.append(("create", name, priority, work_pool_name))

        def update_work_queue(self, queue_id, *, priority):
            calls.append(("update", queue_id, priority))

    class FakeClientContext:
        def __enter__(self):
            return FakeClient()

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(
        deployments,
        "get_client",
        lambda *, sync_client: FakeClientContext(),
    )

    deployments.configure_work_pool()

    assert calls == [
        ("pool", "garmin-docker", 1),
        ("read", "scheduled", "garmin-docker"),
        ("update", "scheduled-id", 1),
        ("read", "backfill", "garmin-docker"),
        ("create", "backfill", 10, "garmin-docker"),
    ]


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


def test_failed_task_state_becomes_error_result_without_raising():
    result = _task_state_result(
        FakeState(RuntimeError("Garmin unavailable"), completed=False),
        data_type="daily_summary",
    )

    assert result == {
        "status": "error",
        "rows": 0,
        "errors": 1,
        "error": "Garmin unavailable",
    }


def test_user_flow_runs_selected_objects_sequentially(monkeypatch):
    calls = []

    def fake_daily_task(*, user_id, calendar_date, dry_run, return_state):
        assert return_state is True
        calls.append(("daily", user_id, calendar_date, dry_run))
        return FakeState({"status": "success", "rows": 1, "errors": 0})

    def fake_list_activities_task(
        *,
        user_id,
        start_date,
        end_date,
        dry_run,
        return_state,
    ):
        assert return_state is True
        calls.append(("list", user_id, start_date, end_date, dry_run))
        return FakeState([{"activityId": 1001}, {"activityId": 1002}])

    def fake_activity_summary_task(
        *,
        user_id,
        activity_id,
        dry_run,
        activity_summary,
        return_state,
    ):
        assert return_state is True
        calls.append((
            "activity-summary",
            user_id,
            activity_id,
            dry_run,
            activity_summary,
        ))
        return FakeState({
            "status": "success",
            "rows": 1,
            "errors": 0,
        })

    def fake_activity_detail_task(
        *,
        user_id,
        activity_id,
        dry_run,
        return_state,
    ):
        assert return_state is True
        calls.append(("activity-detail", user_id, activity_id, dry_run))
        return FakeState({
            "status": "success",
            "rows": 0,
            "errors": 0,
            "detail_rows": 1,
            "detail_errors": 0,
        })

    def fake_records_task(*, user_id, dry_run, return_state):
        assert return_state is True
        calls.append(("records", user_id, dry_run))
        return FakeState({"status": "success", "rows": 3, "errors": 0})

    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_daily_summary_day_task",
        fake_daily_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.list_activity_summaries_task",
        fake_list_activities_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_summary_task",
        fake_activity_summary_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_detail_task",
        fake_activity_detail_task,
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
        ("list", 7, date(2026, 6, 1), date(2026, 6, 2), True),
        ("activity-summary", 7, 1001, True, {"activityId": 1001}),
        ("activity-detail", 7, 1001, True),
        ("activity-summary", 7, 1002, True, {"activityId": 1002}),
        ("activity-detail", 7, 1002, True),
        ("records", 7, True),
    ]


def test_list_activity_summaries_task_preserves_dry_run(monkeypatch):
    calls = []

    def fake_list_activity_summaries(
        *,
        user_id,
        start_date,
        end_date,
        dry_run,
        raise_on_error,
    ):
        calls.append((user_id, start_date, end_date, dry_run, raise_on_error))
        return [{"activityId": 1001}]

    monkeypatch.setattr(tasks, "list_activity_summaries", fake_list_activity_summaries)

    result = tasks.list_activity_summaries_task.fn(
        user_id=7,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        dry_run=True,
    )

    assert result == [{"activityId": 1001}]
    assert calls == [(7, date(2026, 6, 1), date(2026, 6, 2), True, True)]


def test_ingest_activity_summary_task_passes_summary_fallback(monkeypatch):
    calls = []
    summary = {
        "activityId": 1004,
        "activityName": "Summary Run",
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-06-01 14:30:00",
    }

    def fake_ingest_activity(
        *,
        user_id,
        activity_id,
        dry_run,
        include_details,
        include_files,
        activity_summary,
        raise_on_error,
    ):
        calls.append((
            user_id,
            activity_id,
            dry_run,
            include_details,
            include_files,
            activity_summary,
            raise_on_error,
        ))
        return IngestResult.success("activities", rows=1)

    monkeypatch.setattr(tasks, "ingest_activity", fake_ingest_activity)

    result = tasks.ingest_activity_summary_task.fn(
        user_id=7,
        activity_id=1004,
        dry_run=True,
        activity_summary=summary,
    )

    assert result == {"status": "success", "rows": 1, "errors": 0}
    assert calls == [(7, 1004, True, False, False, summary, True)]


def test_user_flow_continues_after_exhausted_daily_task(monkeypatch):
    calls = []

    def fake_daily_task(*, user_id, calendar_date, dry_run, return_state):
        calls.append(calendar_date)
        if calendar_date == date(2026, 6, 1):
            return FakeState(RuntimeError("first day failed"), completed=False)
        return FakeState({"status": "success", "rows": 1, "errors": 0})

    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_daily_summary_day_task",
        fake_daily_task,
    )

    result = garmin_archive_user_flow.fn(
        user_ref={"id": 7, "display_name": "mike"},
        data_types=["daily_summary"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        dry_run=False,
    )

    assert calls == [date(2026, 6, 1), date(2026, 6, 2)]
    assert result["daily_summary"] == {
        "status": "partial",
        "rows": 1,
        "errors": 1,
        "error": "first day failed",
    }


def test_activity_file_runs_after_detail_retries_are_exhausted(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "garmin_orchestrator.flows.list_activity_summaries_task",
        lambda **kwargs: FakeState([{"activityId": 1001}]),
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_summary_task",
        lambda **kwargs: FakeState({
            "status": "success",
            "rows": 1,
            "errors": 0,
        }),
    )

    def fake_detail_task(**kwargs):
        calls.append("detail")
        return FakeState(RuntimeError("detail unavailable"), completed=False)

    def fake_file_task(**kwargs):
        calls.append("file")
        return FakeState({
            "status": "success",
            "rows": 0,
            "errors": 0,
            "file_rows": 1,
            "file_errors": 0,
        })

    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_detail_task",
        fake_detail_task,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.ingest_activity_file_task",
        fake_file_task,
    )

    result = garmin_archive_user_flow.fn(
        user_ref={"id": 7, "display_name": "mike"},
        data_types=["activities"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        dry_run=False,
        include_details=True,
        include_files=True,
    )

    assert calls == ["detail", "file"]
    assert result["activities"] == {
        "status": "partial",
        "rows": 1,
        "errors": 1,
        "detail_errors": 1,
        "detail_rows": 0,
        "file_errors": 0,
        "file_rows": 1,
        "error": "detail unavailable",
    }


def test_summary_markdown_includes_object_metrics_and_errors():
    markdown = _summary_markdown({
        "window": {"start_date": "2026-06-01", "end_date": "2026-06-02"},
        "dry_run": False,
        "errors": 0,
        "partials": 1,
        "results": [
            {
                "user": "mike",
                "activities": {
                    "status": "partial",
                    "rows": 2,
                    "errors": 1,
                    "detail_errors": 1,
                    "error": "detail | unavailable",
                },
            }
        ],
    })

    assert "| mike | activities | partial | 2 | 1 | detail_errors=1 |" in markdown
    assert "detail \\| unavailable" in markdown


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


def test_backfill_chunk_size_is_bounded():
    assert _backfill_chunk_end(
        date(2026, 1, 1),
        date(2026, 12, 31),
        chunk_days=30,
    ) == date(2026, 1, 30)

    with pytest.raises(ValueError, match=f"between 1 and {MAX_BACKFILL_CHUNK_DAYS}"):
        _backfill_chunk_end(
            date(2026, 1, 1),
            date(2026, 12, 31),
            chunk_days=MAX_BACKFILL_CHUNK_DAYS + 1,
        )


def test_backfill_runs_one_chunk_and_enqueues_the_full_remainder(monkeypatch):
    archive_calls = []
    deployment_calls = []

    monkeypatch.setattr(
        "garmin_orchestrator.flows.resolve_date_window_task",
        lambda **_: {
            "start_date": date(2020, 1, 1),
            "end_date": date(2026, 12, 31),
        },
    )

    def fake_archive_flow(**kwargs):
        archive_calls.append(kwargs)
        return {
            "window": {
                "start_date": kwargs["start_date"].isoformat(),
                "end_date": kwargs["end_date"].isoformat(),
            },
            "data_types": kwargs["data_types"],
            "dry_run": kwargs["dry_run"],
            "errors": 0,
            "partials": 0,
            "results": [],
        }

    def fake_run_deployment(name, **kwargs):
        deployment_calls.append((name, kwargs))
        return SimpleNamespace(id="next-run-id")

    monkeypatch.setattr(
        "garmin_orchestrator.flows.garmin_archive_flow",
        fake_archive_flow,
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.run_deployment",
        fake_run_deployment,
    )

    result = garmin_backfill_flow.fn(
        data_types=["daily_summary", "activities", "personal_records"],
        days_back=2557,
        chunk_days=30,
        chain_id="chain-123",
    )

    assert archive_calls == [
        {
            "user": None,
            "data_types": [
                "daily_summary",
                "activities",
                "personal_records",
            ],
            "start_date": date(2020, 1, 1),
            "end_date": date(2020, 1, 30),
            "dry_run": False,
            "fail_on_partial": False,
            "include_details": True,
            "include_files": True,
        }
    ]
    assert deployment_calls[0][0] == "garmin-backfill/backfill"
    continuation = deployment_calls[0][1]
    assert continuation["parameters"]["start_date"] == date(2020, 1, 31)
    assert continuation["parameters"]["end_date"] == date(2026, 12, 31)
    assert continuation["parameters"]["data_types"] == [
        "daily_summary",
        "activities",
    ]
    assert continuation["parameters"]["chain_id"] == "chain-123"
    assert continuation["timeout"] == 0
    assert continuation["as_subflow"] is False
    assert continuation["idempotency_key"] == ("garmin-backfill:chain-123:2020-01-31")
    assert result["backfill"] == {
        "chain_id": "chain-123",
        "requested_window": {
            "start_date": "2020-01-01",
            "end_date": "2026-12-31",
        },
        "chunk_days": 30,
        "continuation_run_id": "next-run-id",
    }


def test_backfill_does_not_chain_date_independent_personal_records(monkeypatch):
    monkeypatch.setattr(
        "garmin_orchestrator.flows.resolve_date_window_task",
        lambda **_: {
            "start_date": date(2020, 1, 1),
            "end_date": date(2026, 12, 31),
        },
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.garmin_archive_flow",
        lambda **kwargs: {
            "window": {
                "start_date": kwargs["start_date"].isoformat(),
                "end_date": kwargs["end_date"].isoformat(),
            },
            "data_types": kwargs["data_types"],
            "dry_run": kwargs["dry_run"],
            "errors": 0,
            "partials": 0,
            "results": [],
        },
    )
    monkeypatch.setattr(
        "garmin_orchestrator.flows.run_deployment",
        lambda *args, **kwargs: pytest.fail("should not enqueue a continuation"),
    )

    result = garmin_backfill_flow.fn(
        data_types=["personal_records"],
        days_back=2557,
        chain_id="chain-123",
    )

    assert result["window"] == {
        "start_date": "2020-01-01",
        "end_date": "2026-12-31",
    }
    assert result["backfill"]["continuation_run_id"] is None


def test_archive_flow_logs_request_and_result_summary(monkeypatch, caplog):
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
    monkeypatch.setattr(
        "garmin_orchestrator.flows.garmin_archive_user_flow",
        lambda **_: {
            "user": "mike",
            "daily_summary": {"status": "success", "rows": 1, "errors": 0},
        },
    )

    with caplog.at_level("INFO", logger="garmin_orchestrator.flows"):
        garmin_archive_flow.fn(data_types=["daily_summary"], days_back=1)

    messages = [record.getMessage() for record in caplog.records]
    assert any("Starting Garmin archive:" in message for message in messages)
    assert any("Garmin archive completed:" in message for message in messages)


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
