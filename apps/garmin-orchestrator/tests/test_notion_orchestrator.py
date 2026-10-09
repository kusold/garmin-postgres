from datetime import date
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from garmin_orchestrator import notion_flows, notion_tasks
from garmin_orchestrator.cli import app
from garmin_orchestrator.notion_flows import (
    MAX_NOTION_BACKFILL_CHUNK_DAYS,
    _summary_markdown,
    normalize_notion_data_types,
    notion_backfill_flow,
    notion_sync_flow,
)


def _sync_result(status: str = "success", **overrides):
    return {
        "status": status,
        "rows": 1,
        "created": 1,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        **overrides,
    }


def test_normalize_notion_data_types_defaults_deduplicates_and_validates():
    assert normalize_notion_data_types(None) == [
        "activities",
        "daily_steps",
        "personal_records",
    ]
    assert normalize_notion_data_types(["activities", "activities"]) == [
        "activities"
    ]

    with pytest.raises(ValueError, match="Unsupported Notion data type.*sleep"):
        normalize_notion_data_types(["sleep"])


def test_notion_user_task_calls_shared_run(monkeypatch):
    calls = []
    expected = {"activities": _sync_result()}
    monkeypatch.setattr(
        notion_tasks,
        "run_user_sync",
        lambda user_id, **kwargs: calls.append((user_id, kwargs)) or expected,
    )

    result = notion_tasks.sync_notion_user_task.fn(
        user_id=7,
        data_types=["activities"],
        start_date=date(2026, 7, 29),
        end_date=date(2026, 7, 30),
    )

    assert result is expected
    assert calls == [(7, {
        "data_types": ["activities"],
        "start_date": date(2026, 7, 29),
        "end_date": date(2026, 7, 30),
        "dry_run": False,
        "include_updated_activities": True,
    })]


def test_notion_flow_infers_single_active_user_and_returns_summary(monkeypatch):
    calls = []
    artifacts = []

    monkeypatch.setattr(
        notion_flows,
        "ensure_database_ready_task",
        lambda: calls.append("database"),
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_date_window_task",
        lambda **kwargs: {
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
        },
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_active_users_task",
        lambda **kwargs: [{"id": 1, "display_name": "mike"}],
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_notion_configured_users_task",
        lambda candidates: candidates,
    )

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {
            "activities": _sync_result(),
            "daily_steps": _sync_result(rows=0, created=0),
            "personal_records": _sync_result(updated=1, created=0),
        }

    monkeypatch.setattr(notion_flows, "sync_notion_user_task", fake_sync)
    monkeypatch.setattr(
        notion_flows,
        "_publish_summary_artifact",
        artifacts.append,
    )

    result = notion_sync_flow.fn()

    assert calls == [
        "database",
        {
            "user_id": 1,
            "data_types": [
                "activities",
                "daily_steps",
                "personal_records",
            ],
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
            "dry_run": False,
            "include_updated_activities": True,
        },
    ]
    assert result["window"] == {
        "start_date": "2026-07-29",
        "end_date": "2026-07-30",
    }
    assert result["errors"] == 0
    assert result["partials"] == 0
    assert result["results"][0]["user"] == "mike"
    assert artifacts == [result]


def test_notion_flow_syncs_multiple_configured_users_when_unpinned(monkeypatch):
    calls = []

    monkeypatch.setattr(notion_flows, "ensure_database_ready_task", lambda: None)
    monkeypatch.setattr(
        notion_flows,
        "resolve_date_window_task",
        lambda **kwargs: {
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
        },
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_active_users_task",
        lambda **kwargs: [
            {"id": 1, "display_name": "mike"},
            {"id": 2, "display_name": "katie"},
        ],
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_notion_configured_users_task",
        lambda candidates: candidates,
    )

    def fake_sync(**kwargs):
        calls.append(kwargs)
        results = {
            "activities": _sync_result(),
            "daily_steps": _sync_result(),
            "personal_records": _sync_result(),
        }
        if kwargs["user_id"] == 1:
            results["daily_steps"] = _sync_result(
                "partial", created=0, errors=1, error="bad | row"
            )
        return results

    monkeypatch.setattr(notion_flows, "sync_notion_user_task", fake_sync)
    monkeypatch.setattr(
        notion_flows, "_publish_summary_artifact", lambda summary: None
    )

    result = notion_sync_flow.fn()

    assert [call["user_id"] for call in calls] == [1, 2]
    assert [row["user"] for row in result["results"]] == ["mike", "katie"]
    assert result["results"][0]["daily_steps"]["status"] == "partial"
    assert result["results"][1]["daily_steps"]["status"] == "success"
    assert result["errors"] == 0
    assert result["partials"] == 1


def test_notion_flow_errors_when_no_user_has_notion_target(monkeypatch):
    monkeypatch.setattr(notion_flows, "ensure_database_ready_task", lambda: None)
    monkeypatch.setattr(
        notion_flows,
        "resolve_date_window_task",
        lambda **kwargs: {
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
        },
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_active_users_task",
        lambda **kwargs: [{"id": 1, "display_name": "mike"}],
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_notion_configured_users_task",
        lambda candidates: [],
    )

    with pytest.raises(ValueError, match="sync target"):
        notion_sync_flow.fn()


def test_notion_flow_failure_policy_raises_for_partial_when_requested(monkeypatch):
    artifact_summaries = []
    monkeypatch.setattr(notion_flows, "ensure_database_ready_task", lambda: None)
    monkeypatch.setattr(
        notion_flows,
        "resolve_date_window_task",
        lambda **kwargs: {
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
        },
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_active_users_task",
        lambda **kwargs: [{"id": 1, "display_name": "mike"}],
    )
    monkeypatch.setattr(
        notion_flows,
        "resolve_notion_configured_users_task",
        lambda candidates: candidates,
    )
    monkeypatch.setattr(
        notion_flows,
        "sync_notion_user_task",
        lambda **kwargs: {
            "activities": _sync_result(
                "partial",
                created=0,
                errors=1,
                error="bad | row",
            )
        },
    )
    monkeypatch.setattr(
        notion_flows,
        "_publish_summary_artifact",
        artifact_summaries.append,
    )

    with pytest.raises(RuntimeError, match="1 partial object"):
        notion_sync_flow.fn(
            data_types=["activities"],
            fail_on_partial=True,
        )

    assert artifact_summaries[0]["partials"] == 1
    assert "bad \\| row" in _summary_markdown(artifact_summaries[0])


def test_notion_sync_cli_calls_flow_with_parsed_options(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "garmin_orchestrator.cli.notion_sync_flow",
        lambda **kwargs: calls.append(kwargs),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "notion-sync",
            "--user",
            "mike",
            "--days-back",
            "3",
            "--start-date",
            "2026-07-01",
            "--end-date",
            "2026-07-03",
            "--data-type",
            "activities",
            "--fail-on-partial",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "user": "mike",
            "data_types": ["activities"],
            "days_back": 3,
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 3),
            "dry_run": False,
            "fail_on_partial": True,
        }
    ]


def test_notion_backfill_syncs_one_chunk_and_queues_remaining_dates(monkeypatch):
    sync_calls = []
    queued = []
    monkeypatch.setattr(notion_flows, "resolve_date_window_task", lambda **_: {
        "start_date": date(2020, 1, 1), "end_date": date(2020, 12, 31),
    })
    monkeypatch.setattr(notion_flows, "notion_sync_flow", lambda **kw: (
        sync_calls.append(kw) or {"window": {}, "errors": 0, "partials": 0}
    ))
    monkeypatch.setattr(notion_flows, "run_deployment", lambda name, **kw: (
        queued.append((name, kw)) or SimpleNamespace(id="next-id")
    ))

    result = notion_backfill_flow.fn(
        user="mike", chunk_days=180, start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31), chain_id="chain-1",
    )

    assert sync_calls == [{
        "user": "mike",
        "data_types": ["activities", "daily_steps", "personal_records"],
        "days_back": None,
        "start_date": date(2020, 1, 1),
        "end_date": date(2020, 6, 28),
        "dry_run": False,
        "fail_on_partial": True,
        "include_updated_activities": False,
    }]
    name, continuation = queued[0]
    assert name == "garmin-notion-backfill/notion-backfill"
    assert continuation["parameters"]["start_date"] == date(2020, 6, 29)
    assert continuation["parameters"]["end_date"] == date(2020, 12, 31)
    assert continuation["parameters"]["data_types"] == ["activities", "daily_steps"]
    assert continuation["parameters"]["chain_id"] == "chain-1"
    assert continuation["timeout"] == 0
    assert continuation["as_subflow"] is False
    assert continuation["idempotency_key"] == "notion-backfill:chain-1:2020-06-29"
    assert result["backfill"]["continuation_run_id"] == "next-id"


def test_notion_backfill_does_not_queue_after_failure_or_final_chunk(monkeypatch):
    calls = []
    monkeypatch.setattr(notion_flows, "resolve_date_window_task", lambda **_: {
        "start_date": date(2020, 1, 1), "end_date": date(2020, 1, 31),
    })
    monkeypatch.setattr(notion_flows, "run_deployment", lambda *a, **kw: (
        calls.append((a, kw))
    ))
    monkeypatch.setattr(notion_flows, "notion_sync_flow", lambda **_: {
        "window": {}, "errors": 0, "partials": 0,
    })
    result = notion_backfill_flow.fn(
        start_date=date(2020, 1, 1), chunk_days=31,
    )
    assert result["backfill"]["continuation_run_id"] is None
    assert calls == []

    def fail_sync(**_):
        raise RuntimeError("partial sync")

    monkeypatch.setattr(notion_flows, "notion_sync_flow", fail_sync)
    with pytest.raises(RuntimeError, match="partial sync"):
        notion_backfill_flow.fn(start_date=date(2020, 1, 1), chunk_days=1)
    assert calls == []


def test_notion_backfill_rejects_unbounded_or_invalid_window():
    with pytest.raises(ValueError, match="requires start_date or days_back"):
        notion_backfill_flow.fn()
    with pytest.raises(ValueError, match=f"between 1 and {MAX_NOTION_BACKFILL_CHUNK_DAYS}"):
        notion_backfill_flow.fn(start_date=date(2020, 1, 1), chunk_days=366)


def test_notion_backfill_cli_passes_dates_and_chunk_size(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "garmin_orchestrator.cli.notion_backfill_flow",
        lambda **kwargs: calls.append(kwargs),
    )

    result = CliRunner().invoke(app, [
        "run", "notion-backfill", "--user", "mike",
        "--start-date", "2020-01-01", "--end-date", "2020-12-31",
        "--data-type", "daily_steps", "--chunk-days", "90",
    ])

    assert result.exit_code == 0
    assert calls == [{
        "user": "mike",
        "data_types": ["daily_steps"],
        "days_back": None,
        "start_date": date(2020, 1, 1),
        "end_date": date(2020, 12, 31),
        "chunk_days": 90,
        "dry_run": False,
        "fail_on_partial": True,
    }]
