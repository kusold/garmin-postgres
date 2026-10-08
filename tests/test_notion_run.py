"""The per-user run is tested across its database and Notion seams."""

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest
from sqlmodel import Session

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from garmin_postgres.models.sync_target import SyncTarget
from garmin_postgres.models.user import User
from notion_sync import run
from notion_sync.notion import NotionSink


def _use_test_connection(monkeypatch, session):
    connection = session.connection()
    monkeypatch.setattr(run, "get_engine", lambda: connection)
    monkeypatch.setattr(run, "Session", lambda _engine: Session(bind=connection))


def _user(session, name: str, *, active: bool = True) -> User:
    user = User(garmin_display_name=name, is_active=active)
    session.add(user)
    session.flush()
    return user


def _target(session, user: User, *, token: str | None, databases: dict):
    session.add(SyncTarget(
        user_id=user.id,
        target="notion",
        config_json={"token": token, "databases": databases},
    ))
    session.flush()


def _notion_client(monkeypatch):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.databases.retrieve.return_value = {
        "data_sources": [{"id": "source-1"}],
    }
    client.data_sources.query.return_value = {"results": []}
    options = []

    def make_client(*, auth, retry):
        options.append((auth, retry))
        return client

    monkeypatch.setattr(run, "Client", make_client)
    monkeypatch.setattr(
        run, "NotionSink",
        lambda notion_client, *, dry_run: NotionSink(
            notion_client, dry_run=dry_run, min_interval=0,
        ),
    )
    return client, options


def test_user_run_filters_by_id_and_dates_but_replays_record_snapshot(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    client, options = _notion_client(monkeypatch)
    user = _user(session, "notion-run-main")
    other = _user(session, "notion-run-other")
    _target(session, user, token="secret", databases={
        "activities": "activity-db",
        "daily_steps": "steps-db",
        "personal_records": "records-db",
    })
    session.add_all([
        Activity(user_id=user.id, activity_id=1, start_time=datetime(2026, 7, 1, tzinfo=timezone.utc), raw_json={}),
        Activity(user_id=user.id, activity_id=2, start_time=datetime(2026, 7, 30, tzinfo=timezone.utc), raw_json={}),
        Activity(user_id=other.id, activity_id=3, start_time=datetime(2026, 7, 30, tzinfo=timezone.utc), raw_json={}),
        DailySummary(user_id=user.id, calendar_date=date(2026, 7, 1), raw_json={}),
        DailySummary(user_id=user.id, calendar_date=date(2026, 7, 30), raw_json={}),
        PersonalRecord(user_id=user.id, type_id=16, record_date=date(2026, 7, 1), value_text="12", raw_json={}),
        PersonalRecord(user_id=user.id, type_id=16, record_date=date(2026, 7, 30), value_text="3", raw_json={}),
    ])
    session.flush()

    results = run.run_user_sync(
        user.id,
        data_types=["personal_records", "activities", "daily_steps", "activities"],
        start_date=date(2026, 7, 29),
        end_date=date(2026, 7, 30),
    )

    assert list(results) == ["personal_records", "activities", "daily_steps"]
    assert [results[k]["rows"] for k in results] == [1, 1, 1]
    assert [results[k]["created"] for k in results] == [1, 1, 1]
    assert client.pages.create.call_count == 3
    assert client.pages.create.call_args_list[0].kwargs["properties"]["Value"]["rich_text"][0]["text"]["content"] == "12 days"
    assert options[0][0] == "secret"
    assert options[0][1].max_retries == 5


def test_dry_run_without_token_maps_rows_offline(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    monkeypatch.setattr(run, "Client", lambda **_kwargs: pytest.fail("Notion was contacted"))
    user = _user(session, "notion-run-offline")
    _target(session, user, token=None, databases={"activities": "activity-db"})
    session.add(Activity(
        user_id=user.id,
        activity_id=4,
        start_time=datetime(2026, 7, 30, tzinfo=timezone.utc),
        raw_json={},
    ))
    session.flush()

    result = run.run_user_sync(user.id, data_types=["activities", "daily_steps"], dry_run=True)

    assert result["activities"]["rows"] == 1
    assert result["activities"]["created"] == 0
    assert result["activities"]["updated"] == 0
    assert result["daily_steps"]["status"] == "skipped"
    with pytest.raises(ValueError, match="token"):
        run.run_user_sync(user.id, data_types=["activities"])


def test_dry_run_with_token_reads_notion_without_writes(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    client, _ = _notion_client(monkeypatch)
    user = _user(session, "notion-run-online-preview")
    _target(session, user, token="secret", databases={"activities": "activity-db"})
    session.add(Activity(
        user_id=user.id,
        activity_id=5,
        start_time=datetime(2026, 7, 30, tzinfo=timezone.utc),
        raw_json={},
    ))
    session.flush()

    result = run.run_user_sync(user.id, data_types=["activities"], dry_run=True)

    assert result["activities"]["rows"] == 1
    assert result["activities"]["created"] == 0
    client.databases.retrieve.assert_called_once()
    client.data_sources.query.assert_called_once()
    client.pages.create.assert_not_called()
    client.pages.update.assert_not_called()


def test_user_run_updates_existing_page(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    client, _ = _notion_client(monkeypatch)
    client.data_sources.query.return_value = {"results": [{"id": "page-1"}]}
    user = _user(session, "notion-run-update")
    _target(session, user, token="secret", databases={"activities": "activity-db"})
    session.add(Activity(
        user_id=user.id,
        activity_id=8,
        start_time=datetime(2026, 7, 30, tzinfo=timezone.utc),
        raw_json={},
    ))
    session.flush()

    result = run.run_user_sync(user.id, data_types=["activities"])["activities"]

    assert result["updated"] == 1
    assert result["created"] == 0
    assert client.pages.update.call_args.kwargs["page_id"] == "page-1"
    client.pages.create.assert_not_called()


def test_personal_record_snapshot_replays_latest_value(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    client, _ = _notion_client(monkeypatch)
    client.data_sources.query.side_effect = [
        {"results": []}, {"results": [{"id": "page-1"}]},
    ]
    user = _user(session, "notion-run-record-replay")
    _target(session, user, token="secret", databases={"personal_records": "records-db"})
    session.add_all([
        PersonalRecord(user_id=user.id, type_id=3, record_date=date(2026, 5, 1), value_text="1380", raw_json={}),
        PersonalRecord(user_id=user.id, type_id=3, record_date=date(2026, 6, 1), value_text="1334", raw_json={}),
    ])
    session.flush()

    result = run.run_user_sync(user.id, data_types=["personal_records"])["personal_records"]

    assert result["rows"] == 2
    assert result["created"] == 1
    assert result["updated"] == 1
    assert client.databases.retrieve.call_count == 1
    updated = client.pages.update.call_args.kwargs["properties"]
    assert updated["Value"]["rich_text"][0]["text"]["content"] == "22:14"


def test_user_run_continues_after_one_row_fails(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    client, _ = _notion_client(monkeypatch)
    user = _user(session, "notion-run-partial")
    _target(session, user, token="secret", databases={"activities": "activity-db"})
    session.add_all([
        Activity(user_id=user.id, activity_id=6, start_time=datetime(2026, 7, 29, tzinfo=timezone.utc), raw_json={}),
        Activity(user_id=user.id, activity_id=7, start_time=datetime(2026, 7, 30, tzinfo=timezone.utc), raw_json={}),
    ])
    session.flush()
    client.data_sources.query.side_effect = [RuntimeError("temporary failure"), {"results": []}]

    result = run.run_user_sync(user.id, data_types=["activities"])["activities"]

    assert result["status"] == "partial"
    assert result["rows"] == 2
    assert result["errors"] == 1
    assert result["created"] == 1


def test_run_rejects_inactive_or_unconfigured_user(session, monkeypatch):
    _use_test_connection(monkeypatch, session)
    inactive = _user(session, "notion-run-inactive", active=False)
    _target(session, inactive, token="secret", databases={"activities": "activity-db"})
    no_target = _user(session, "notion-run-no-target")
    no_databases = _user(session, "notion-run-no-databases")
    _target(session, no_databases, token="secret", databases={})

    with pytest.raises(ValueError, match="inactive"):
        run.run_user_sync(inactive.id)
    with pytest.raises(ValueError, match="sync target"):
        run.run_user_sync(no_target.id)
    with pytest.raises(ValueError, match="no Notion databases"):
        run.run_user_sync(no_databases.id)
    with pytest.raises(ValueError, match="not found"):
        run.run_user_sync(999999999)


def test_run_validates_data_types_before_database_access(monkeypatch):
    monkeypatch.setattr(run, "get_engine", lambda: pytest.fail("database was contacted"))
    with pytest.raises(ValueError, match="Unsupported Notion data type"):
        run.run_user_sync(1, data_types=["sleep"])
