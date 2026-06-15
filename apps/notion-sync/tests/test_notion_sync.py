import logging
from datetime import date, datetime, timezone

import pytest
from notion_client.errors import APIResponseError, UnknownHTTPResponseError
from sqlalchemy import select
from typer.testing import CliRunner

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from notion_sync.config import NotionSettings, get_settings
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page
from notion_sync.notion import NotionSink
from notion_sync.sync import (
    _apply_date_window,
    _apply_datetime_window,
    run_sync,
)


class FakeDatabases:
    """Fake Notion ``databases`` endpoint.

    By default returns a fixed result list. Pass ``query_side_effect`` (a callable
    invoked with the query kwargs) to control per-call behavior (e.g. raise on
    the first call, return a result on the next).
    """

    def __init__(self, results=None, *, query_side_effect=None):
        self.results = results or []
        self.queries = []
        self._query_side_effect = query_side_effect

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self._query_side_effect is not None:
            return self._query_side_effect(**kwargs)
        return {"results": self.results}


class FakePages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, **kwargs):
        self.created.append(kwargs)

    def update(self, **kwargs):
        self.updated.append(kwargs)


class FakeNotionClient:
    def __init__(self, results=None, *, query_side_effect=None):
        self.databases = FakeDatabases(results, query_side_effect=query_side_effect)
        self.pages = FakePages()


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    """Fake SQLAlchemy session. ``rows`` is a list of row-lists popped in order."""

    def __init__(self, rows):
        self.rows = rows

    def scalars(self, stmt):
        return FakeScalarResult(self.rows.pop(0))


def _make_activity():
    return Activity(
        id=42,
        user_id=1,
        activity_id=123,
        activity_type="running",
        start_time=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
        raw_json={
            "activityName": "Morning Run",
            "activityType": {"typekey": "running"},
            "distance": 5000,
            "duration": 1800,
        },
    )


def test_activity_page_maps_postgres_activity_to_notion_properties():
    activity = Activity(
        user_id=1,
        activity_id=123,
        activity_type="running",
        start_time=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
        raw_json={
            "activityName": "Morning Run",
            "activityType": {"typeKey": "running"},
            "distance": 5000,
            "duration": 1800,
            "calories": 321,
            "averageSpeed": 2.77,
            "pr": True,
        },
    )

    properties, filter_payload, icon = activity_page(activity)

    assert filter_payload == {"property": "Garmin Activity ID", "number": {"equals": 123}}
    assert properties["Activity Name"]["title"][0]["text"]["content"] == "Morning Run"
    assert properties["Distance (km)"]["number"] == 5.0
    assert properties["Duration (min)"]["number"] == 30.0
    assert properties["PR"]["checkbox"] is True
    assert icon is not None


def test_daily_steps_page_maps_daily_summary_raw_json():
    summary = DailySummary(
        user_id=1,
        calendar_date=date(2026, 6, 1),
        raw_json={"totalSteps": 8432, "dailyStepGoal": 10000, "totalDistanceMeters": 6200},
    )

    properties, filter_payload, icon = daily_steps_page(summary)

    assert properties["Activity Type"]["title"][0]["text"]["content"] == "Walking"
    assert properties["Total Steps"]["number"] == 8432
    assert properties["Step Goal"]["number"] == 10000
    assert properties["Total Distance (km)"]["number"] == 6.2
    assert filter_payload["and"][0]["property"] == "Date"
    assert icon is None


def test_personal_record_page_maps_currently_ingested_personal_records():
    record = PersonalRecord(
        user_id=1,
        type_id=3,
        record_date=date(2026, 6, 1),
        activity_type="running",
        value_text="00:22:14",
        raw_json={"typeId": 3, "value": "00:22:14"},
    )

    properties, filter_payload, icon = personal_record_page(record)

    assert properties["Record"]["title"][0]["text"]["content"] == "5K"
    assert properties["Value"]["rich_text"][0]["text"]["content"] == "00:22:14"
    assert properties["PR"]["checkbox"] is True
    assert filter_payload["and"][0]["property"] == "Record"
    assert icon is None


def test_notion_sink_creates_when_no_existing_page():
    client = FakeNotionClient(results=[])
    sink = NotionSink(client)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "created"
    assert len(client.pages.created) == 1
    assert client.pages.updated == []


def test_notion_sink_updates_when_existing_page_exists():
    client = FakeNotionClient(results=[{"id": "page-1"}])
    sink = NotionSink(client)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "updated"
    assert client.pages.created == []
    assert client.pages.updated[0]["page_id"] == "page-1"


def test_notion_sink_dry_run_does_not_query_or_write():
    client = FakeNotionClient(results=[{"id": "page-1"}])
    sink = NotionSink(client, dry_run=True)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "dry_run"
    assert client.databases.queries == []
    assert client.pages.created == []
    assert client.pages.updated == []


def test_run_sync_skips_unconfigured_databases():
    session = FakeSession(rows=[])
    sink = NotionSink(FakeNotionClient(), dry_run=True)
    settings = NotionSettings(
        token=None,
        activities_database_id=None,
        daily_steps_database_id=None,
        personal_records_database_id=None,
    )

    result = run_sync(session, sink, settings)

    assert result["activities"]["status"] == "skipped"
    assert result["daily_steps"]["status"] == "skipped"
    assert result["personal_records"]["status"] == "skipped"


def test_notion_settings_load_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_ACTIVITIES_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_DAILY_STEPS_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_PERSONAL_RECORDS_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_TIMEZONE", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "NOTION_TOKEN=from-file",
                "NOTION_ACTIVITIES_DB_ID=activities-db",
                "NOTION_DAILY_STEPS_DB_ID=steps-db",
                "NOTION_PERSONAL_RECORDS_DB_ID=records-db",
            ]
        )
    )

    settings = get_settings()

    assert settings.token == "from-file"
    assert settings.activities_database_id == "activities-db"
    assert settings.daily_steps_database_id == "steps-db"
    assert settings.personal_records_database_id == "records-db"


def test_notion_settings_process_env_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("NOTION_TOKEN=from-file\n")
    monkeypatch.setenv("NOTION_TOKEN", "from-env")

    settings = get_settings()

    assert settings.token == "from-env"


def test_notion_sync_run_requires_user():
    from notion_sync.cli import app

    result = CliRunner().invoke(app, ["run", "--dry-run"])

    assert result.exit_code != 0
    assert "Missing option" in result.output
    assert "--user" in result.output


# --------------------------------------------------------------------------- #
# run_sync create / update / error paths
# --------------------------------------------------------------------------- #

def _settings_with_activities():
    """Build a NotionSettings with activities configured (env file ignored for test isolation)."""
    return NotionSettings(_env_file=None, token="tok", activities_database_id="activities-db")


def test_run_sync_creates_page_when_no_existing_page():
    session = FakeSession(rows=[[_make_activity()]])
    client = FakeNotionClient(results=[])
    sink = NotionSink(client, dry_run=False, min_interval=0.0, sleep=lambda _s: None)
    settings = _settings_with_activities()

    result = run_sync(session, sink, settings, data_types=["activities"])

    info = result["activities"]
    assert info["status"] == "success"
    assert info["rows"] == 1
    assert info["created"] == 1
    assert info["updated"] == 0
    assert info["errors"] == 0
    assert len(client.pages.created) == 1
    assert client.pages.updated == []
    assert client.pages.created[0]["parent"] == {"database_id": "activities-db"}


def test_run_sync_updates_page_when_existing_page_exists():
    session = FakeSession(rows=[[_make_activity()]])
    client = FakeNotionClient(results=[{"id": "page-1"}])
    sink = NotionSink(client, dry_run=False, min_interval=0.0, sleep=lambda _s: None)
    settings = _settings_with_activities()

    result = run_sync(session, sink, settings, data_types=["activities"])

    info = result["activities"]
    assert info["status"] == "success"
    assert info["rows"] == 1
    assert info["updated"] == 1
    assert info["created"] == 0
    assert info["errors"] == 0
    assert client.pages.created == []
    assert client.pages.updated[0]["page_id"] == "page-1"


def test_run_sync_logs_error_and_marks_partial_when_a_row_fails(caplog):
    # Two activities; the client's query raises on the first row then succeeds
    # on the second, so we get one error + one success -> status "partial".
    activity_a = _make_activity()
    activity_b = Activity(
        id=43,
        user_id=1,
        activity_id=124,
        activity_type="running",
        start_time=datetime(2026, 6, 2, 12, 30, tzinfo=timezone.utc),
        raw_json={"activityName": "Evening Run", "activityType": {"typekey": "running"}},
    )
    session = FakeSession(rows=[[activity_a, activity_b]])

    call = {"n": 0}

    def query_side_effect(**kwargs):
        call["n"] += 1
        if call["n"] == 1:
            raise RuntimeError("boom from query")
        return {"results": []}

    client = FakeNotionClient(query_side_effect=query_side_effect)
    sink = NotionSink(client, dry_run=False, min_interval=0.0, sleep=lambda _s: None)
    settings = _settings_with_activities()

    caplog.set_level(logging.DEBUG, logger="notion_sync.sync")
    result = run_sync(session, sink, settings, data_types=["activities"])

    info = result["activities"]
    assert info["status"] == "partial"
    assert info["rows"] == 2
    assert info["errors"] == 1
    assert info["created"] == 1

    failure_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure_records, "expected an ERROR log from logger.exception"
    msg = failure_records[0].getMessage()
    assert "Activity" in msg
    assert "id=42" in msg  # the failing row's id


# --------------------------------------------------------------------------- #
# NotionSink retry / backoff / pacing
# --------------------------------------------------------------------------- #

class _FakeNotion429(APIResponseError):
    """A retryable 429 error with an injectable Retry-After header.

    APIResponseError.__init__ (via HTTPResponseError) requires several args, so
    we bypass it and just set the attributes the sink reads via getattr:
    ``status`` and ``headers``. Inheritance ensures it's still caught by the
    sink's ``except (APIResponseError, UnknownHTTPResponseError)``.
    """

    def __init__(self, headers=None):
        self.status = 429
        self.headers = headers or {}


class _FakeNotion404(APIResponseError):
    """A non-retryable 404 error, constructed the same way as the 429 fake."""

    def __init__(self, headers=None):
        self.status = 404
        self.headers = headers or {}


def test_notion_sink_retries_on_429_then_succeeds():
    sleeps = []
    query = {"n": 0}

    def query_side_effect(**kwargs):
        query["n"] += 1
        if query["n"] == 1:
            raise _FakeNotion429(headers={"Retry-After": "1.5"})
        return {"results": []}

    client = FakeNotionClient(query_side_effect=query_side_effect)
    sink = NotionSink(
        client,
        dry_run=False,
        min_interval=0.0,
        max_retries=3,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    action = sink.upsert_page("db", filter_payload={"property": "X"}, properties={"X": {}})

    assert action == "created"
    assert query["n"] == 2  # one failure, one success
    assert sleeps == [1.5]  # honored the Retry-After header before retrying
    assert len(client.pages.created) == 1


def test_notion_sink_does_not_retry_non_retryable_4xx():
    sleeps = []
    query = {"n": 0}

    def query_side_effect(**kwargs):
        query["n"] += 1
        raise _FakeNotion404()

    client = FakeNotionClient(query_side_effect=query_side_effect)
    sink = NotionSink(
        client,
        dry_run=False,
        min_interval=0.0,
        max_retries=3,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(APIResponseError):
        sink.upsert_page("db", filter_payload={"property": "X"}, properties={"X": {}})

    assert query["n"] == 1  # no retries
    assert sleeps == []  # no backoff sleep
    assert client.pages.created == []


def test_notion_sink_paces_calls_using_min_interval():
    # min_interval=0.1; monotonic advances 0.04 between calls, so the sink must
    # sleep the remaining ~0.06 before the second call.
    sleeps = []
    clock = {"t": 0.0}

    def monotonic():
        # Advance 0.04s on each read (simulate real time passing during work).
        clock["t"] += 0.04
        return clock["t"]

    client = FakeNotionClient(results=[])  # empty -> create path (two calls)
    sink = NotionSink(
        client,
        dry_run=False,
        min_interval=0.1,
        max_retries=0,
        sleep=sleeps.append,
        monotonic=monotonic,
    )

    action = sink.upsert_page("db", filter_payload={"property": "X"}, properties={"X": {}})

    assert action == "created"
    # First call: no pacing (no prior call). Second call: pace the remainder.
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.06, abs=0.001)


# --------------------------------------------------------------------------- #
# Pure date-window helpers (no DB needed)
# --------------------------------------------------------------------------- #

def _compile(stmt):
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_apply_datetime_window_is_half_open_on_datetime_column():
    stmt = select(Activity.start_time)
    stmt = _apply_datetime_window(stmt, Activity.start_time, date(2026, 6, 10), date(2026, 6, 12))
    sql = _compile(stmt)
    assert "start_time >= '2026-06-10 00:00:00+00:00'" in sql
    # end_date is exclusive -> end+1day at 00:00
    assert "start_time < '2026-06-13 00:00:00+00:00'" in sql


def test_apply_date_window_is_inclusive_on_date_column():
    stmt = select(DailySummary.calendar_date)
    stmt = _apply_date_window(stmt, DailySummary.calendar_date, date(2026, 6, 10), date(2026, 6, 12))
    sql = _compile(stmt)
    assert "calendar_date >= '2026-06-10'" in sql
    assert "calendar_date <= '2026-06-12'" in sql


# --------------------------------------------------------------------------- #
# CLI date validation
# --------------------------------------------------------------------------- #

def test_cli_run_rejects_start_date_after_end_date():
    from notion_sync.cli import app

    result = CliRunner().invoke(
        app,
        ["run", "--dry-run", "--user", "x", "--start-date", "2026-06-10", "--end-date", "2026-06-01"],
    )

    assert result.exit_code != 0
    assert "--start-date must be on or before --end-date" in result.output
