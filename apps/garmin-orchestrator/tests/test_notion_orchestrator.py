from datetime import date
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from garmin_orchestrator import notion_flows, notion_tasks
from garmin_orchestrator.cli import app
from garmin_orchestrator.notion_flows import (
    _summary_markdown,
    normalize_notion_data_types,
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


def test_notion_user_task_bounds_dated_rows_but_replays_all_personal_records(
    monkeypatch,
):
    calls = []
    session = object()

    class FakeSession:
        def __init__(self, engine):
            assert engine == "engine"

        def __enter__(self):
            return session

        def __exit__(self, *_):
            return None

    def fake_run_sync(current_session, sink, settings, **kwargs):
        assert current_session is session
        assert sink == "sink"
        assert settings.token == "secret"
        calls.append(kwargs)
        return {
            data_type: _sync_result()
            for data_type in kwargs["data_types"]
        }

    monkeypatch.setattr(
        notion_tasks,
        "get_notion_settings",
        lambda: SimpleNamespace(token="secret"),
    )
    monkeypatch.setattr(notion_tasks, "Client", lambda *, auth: ("client", auth))
    monkeypatch.setattr(
        notion_tasks,
        "NotionSink",
        lambda client, *, dry_run: "sink",
    )
    monkeypatch.setattr(notion_tasks, "get_engine", lambda: "engine")
    monkeypatch.setattr(notion_tasks, "Session", FakeSession)
    monkeypatch.setattr(notion_tasks, "run_sync", fake_run_sync)

    result = notion_tasks.sync_notion_user_task.fn(
        user="mike",
        data_types=["activities", "daily_steps", "personal_records"],
        start_date=date(2026, 7, 29),
        end_date=date(2026, 7, 30),
    )

    assert list(result) == ["activities", "daily_steps", "personal_records"]
    assert calls == [
        {
            "data_types": ["activities", "daily_steps"],
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
            "user_filter": "mike",
        },
        {
            "data_types": ["personal_records"],
            "user_filter": "mike",
        },
    ]


def test_notion_user_task_requires_token_for_writes(monkeypatch):
    monkeypatch.setattr(
        notion_tasks,
        "get_notion_settings",
        lambda: SimpleNamespace(token=None),
    )

    with pytest.raises(ValueError, match="NOTION_TOKEN"):
        notion_tasks.sync_notion_user_task.fn(
            user="mike",
            data_types=["activities"],
            start_date=date(2026, 7, 29),
            end_date=date(2026, 7, 30),
        )


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
            "user": "mike",
            "data_types": [
                "activities",
                "daily_steps",
                "personal_records",
            ],
            "start_date": date(2026, 7, 29),
            "end_date": date(2026, 7, 30),
            "dry_run": False,
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


def test_notion_flow_requires_user_when_multiple_are_active(monkeypatch):
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
            {"id": 2, "display_name": "other"},
        ],
    )

    with pytest.raises(ValueError, match="single-user"):
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
