import re
from datetime import date, datetime, timezone

import pytest
from notion_client.errors import APIResponseError
from sqlalchemy import select
from typer.testing import CliRunner

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from notion_sync.formatters import (
    PERSONAL_RECORD_NAMES,
    format_duration,
    format_record_pace,
    format_record_value,
)
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page
from notion_sync.notion import NotionSink
from notion_sync.sync import (
    _apply_date_window,
    _apply_datetime_window,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Strip ANSI color/style codes so CLI assertions hold even when rich colorizes
    output (e.g. under GITHUB_ACTIONS)."""
    return _ANSI.sub("", text)


def _data_source_id_for(database_id):
    """Deterministic data source ID for a database ID, mirroring Notion's
    2025-09-03 API where a database container holds distinct data sources."""
    return f"ds-for-{database_id}"


class FakeDatabases:
    """Fake Notion ``databases`` endpoint (notion-client 3.x shape).

    ``retrieve`` returns a database container with a single data source whose
    ID differs from the database ID, like real Notion databases. Pass
    ``data_sources_info`` to override (e.g. empty or multiple data sources).
    """

    def __init__(self, data_sources_info=None):
        self._data_sources_info = data_sources_info
        self.retrieve_calls = []

    def retrieve(self, database_id, **kwargs):
        self.retrieve_calls.append(database_id)
        data_sources = self._data_sources_info
        if data_sources is None:
            data_sources = [{"id": _data_source_id_for(database_id), "name": "Main"}]
        return {"id": database_id, "data_sources": data_sources}


class FakeDataSources:
    """Fake Notion ``data_sources`` endpoint.

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
    def __init__(self, results=None, *, query_side_effect=None, data_sources_info=None):
        self.databases = FakeDatabases(data_sources_info)
        self.data_sources = FakeDataSources(results, query_side_effect=query_side_effect)
        self.pages = FakePages()


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


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
    assert properties["Duration"]["rich_text"][0]["text"]["content"] == "30:00"
    assert properties["PR"]["checkbox"] is True
    assert icon is not None


def test_activity_page_maps_training_effect_from_detail_payload_shape():
    """Detail payloads (/activity-service/activity/{id}) put the aerobic
    training effect number in summaryDTO.trainingEffect and never include
    aerobicTrainingEffect."""
    activity = Activity(
        user_id=2,
        activity_id=24633752656,
        activity_type="walking",
        start_time=datetime(2026, 10, 6, 23, 23, 1, tzinfo=timezone.utc),
        raw_json={
            "activityId": 24633752656,
            "activityName": "Evening Walk",
            "activityTypeDTO": {"typeId": 3, "typeKey": "walking"},
            "summaryDTO": {
                "distance": 2559.2,
                "duration": 2548.901,
                "trainingEffect": 2.8,
                "anaerobicTrainingEffect": 3.5,
                "trainingEffectLabel": "IMPROVING",
                "aerobicTrainingEffectMessage": "IMPROVING_2",
                "anaerobicTrainingEffectMessage": "IMPROVING_1",
            },
        },
    )

    properties, _, _ = activity_page(activity)

    assert properties["Aerobic"]["number"] == 2.8
    assert properties["Anaerobic"]["number"] == 3.5
    assert properties["Training Effect"]["select"]["name"] == "Improving"
    assert properties["Aerobic Effect"]["select"]["name"] == "Impacting"
    assert properties["Anaerobic Effect"]["select"]["name"] == "Impacting"


def test_activity_page_maps_training_effect_from_list_payload_shape():
    """List payloads (fallback when the detail fetch fails) expose the aerobic
    training effect as top-level aerobicTrainingEffect."""
    activity = Activity(
        user_id=2,
        activity_id=24633752656,
        activity_type="walking",
        start_time=datetime(2026, 10, 6, 23, 23, 1, tzinfo=timezone.utc),
        raw_json={
            "activityId": 24633752656,
            "activityName": "Evening Walk",
            "activityType": {"typeKey": "walking"},
            "aerobicTrainingEffect": 2.8,
            "anaerobicTrainingEffect": 3.5,
            "trainingEffectLabel": "IMPROVING",
            "aerobicTrainingEffectMessage": "IMPROVING_2",
            "anaerobicTrainingEffectMessage": "IMPROVING_1",
        },
    )

    properties, _, _ = activity_page(activity)

    assert properties["Aerobic"]["number"] == 2.8
    assert properties["Anaerobic"]["number"] == 3.5
    assert properties["Training Effect"]["select"]["name"] == "Improving"


def test_activity_page_maps_power_from_detail_payload_shape():
    """Detail payloads name average power averagePower (avgPower never appears)."""
    activity = Activity(
        user_id=2,
        activity_id=24607647919,
        activity_type="virtual_ride",
        start_time=datetime(2026, 10, 4, 20, 9, 21, tzinfo=timezone.utc),
        raw_json={
            "activityId": 24607647919,
            "activityName": "Zwift Ride",
            "activityTypeDTO": {"typeKey": "virtual_ride"},
            "summaryDTO": {
                "distance": 26500.0,
                "duration": 3900.0,
                "averagePower": 103.0,
                "maxPower": 316.0,
            },
        },
    )

    properties, _, _ = activity_page(activity)

    assert properties["Avg Power"]["number"] == 103.0
    assert properties["Max Power"]["number"] == 316.0


def test_activity_page_maps_summary_dto_payload_shape():
    activity = Activity(
        user_id=1,
        activity_id=23318629542,
        activity_type="walking",
        start_time=datetime(2026, 6, 20, 14, 26, 41, tzinfo=timezone.utc),
        raw_json={
            "activityId": 23318629542,
            "activityName": "Lakewood Walking",
            "activityTypeDTO": {"typeId": 3, "typeKey": "walking"},
            "summaryDTO": {
                "startTimeGMT": "2026-06-20T14:26:41.0",
                "distance": 2559.2,
                "duration": 2548.901,
                "calories": 146.0,
                "averageSpeed": 1.003999948,
            },
            "metadataDTO": {"favorite": True, "personalRecord": False},
        },
    )

    properties, filter_payload, icon = activity_page(activity)

    assert filter_payload == {
        "property": "Garmin Activity ID",
        "number": {"equals": 23318629542},
    }
    assert properties["Activity Name"]["title"][0]["text"]["content"] == "Lakewood Walking"
    assert properties["Activity Type"]["select"]["name"] == "Walking"
    assert properties["Distance (km)"]["number"] == 2.56
    assert properties["Duration (min)"]["number"] == 42.48
    assert properties["Duration"]["rich_text"][0]["text"]["content"] == "42:29"
    assert properties["Calories"]["number"] == 146
    assert properties["Avg Pace"]["rich_text"][0]["text"]["content"] == "16:36 min/km"
    assert properties["Fav"]["checkbox"] is True
    assert properties["PR"]["checkbox"] is False
    assert properties["Date"]["date"]["start"] == "2026-06-20T14:26:41+00:00"
    assert icon is not None


def test_personal_record_names_cover_all_garmin_type_ids():
    """5/6/11/16 are real Garmin PR types observed in archived data; without
    names they sync to Notion as 'Unnamed Activity'."""
    assert PERSONAL_RECORD_NAMES[5] == "Half Marathon"
    assert PERSONAL_RECORD_NAMES[6] == "Marathon"
    assert PERSONAL_RECORD_NAMES[11] == "Fastest 40 km"
    assert PERSONAL_RECORD_NAMES[16] == "Daily Streak"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (178, "2:58"),
        (3741, "1:02:21"),
        (3599.6, "1:00:00"),
        (62.5, "1:02"),
        (None, ""),
        (0, ""),
        (-1, ""),
        (float("nan"), ""),
        (float("inf"), ""),
        ("invalid", ""),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("type_id", "raw_value", "expected"),
    [
        (1, "178.0", "2:58"),
        (2, "292.0", "4:52"),
        (3, "1074.0", "17:54"),
        (4, "2236.0", "37:16"),
        (5, "5012.0", "1:23:32"),
        (6, "9876.0", "2:44:36"),
        (7, "32165.0", "32.16 km"),
        (8, "73540.0", "73.54 km"),
        (9, "1234.0", "1,234 m"),
        (10, "265.0", "265 W"),
        (11, "5421.0", "1:30:21"),
        (12, "27106", "27,106"),
        (13, "27106", "27,106"),
        (14, "27106", "27,106"),
        (15, "42.0", "42 days"),
        (16, "137.0", "137 days"),
        (99, "123.0", "123.0"),
        (3, "00:22:14", "00:22:14"),
        (3, "not-a-number", "not-a-number"),
        (3, "nan", "nan"),
        (7, "inf", "inf"),
        (3, None, ""),
    ],
)
def test_format_record_value(type_id, raw_value, expected):
    assert format_record_value(type_id, raw_value) == expected


def test_personal_record_page_computes_pace_for_running_duration_records():
    """PR payloads carry no pace key; pace must be derived from the duration
    value and the known race distance of the type."""
    record = PersonalRecord(
        user_id=1,
        type_id=3,
        record_date=date(2026, 6, 4),
        activity_type="running",
        value_text="1914.4",  # fastest 5K: 31:54
        raw_json={"typeId": 3, "value": 1914.4},
    )

    properties, _, _ = personal_record_page(record)

    assert properties["Record"]["title"][0]["text"]["content"] == "5K"
    assert properties["Value"]["rich_text"][0]["text"]["content"] == "31:54"
    assert properties["Pace"]["rich_text"][0]["text"]["content"] == "6:22 min/km"


def test_personal_record_page_pace_blank_for_non_duration_records():
    record = PersonalRecord(
        user_id=1,
        type_id=7,
        record_date=date(2026, 7, 6),
        activity_type="running",
        value_text="46054.1",  # longest run: a distance, not a duration
        raw_json={"typeId": 7, "value": 46054.1},
    )

    properties, _, _ = personal_record_page(record)

    assert properties["Pace"]["rich_text"][0]["text"]["content"] == ""
    assert properties["Value"]["rich_text"][0]["text"]["content"] == "46.05 km"


def test_format_record_pace_handles_missing_and_garbage_values():
    assert format_record_pace(3, None) == ""
    assert format_record_pace(3, "") == ""
    assert format_record_pace(3, "not-a-number") == ""
    assert format_record_pace(99, "1800.0") == ""  # unknown type
    assert format_record_pace(3, "0") == ""
    assert format_record_pace(3, "nan") == ""
    assert format_record_pace(3, "inf") == ""
    assert format_record_pace(11, "00:22:14") == ""
    assert format_record_pace(11, "1e-320") == ""


def test_format_record_pace_uses_speed_for_cycling_record():
    assert format_record_pace(11, "5421.0") == "26.6 km/h"


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
    assert icon == {"type": "emoji", "emoji": "🚶‍♀️"}


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
    assert properties["Pace"]["rich_text"][0]["text"]["content"] == ""
    assert properties["PR"]["checkbox"] is True
    assert filter_payload == {"property": "typeId", "number": {"equals": 3}}
    assert icon == {"type": "emoji", "emoji": "🏃‍♀️"}


def test_notion_sink_creates_when_no_existing_page():
    client = FakeNotionClient(results=[])
    sink = NotionSink(client)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "created"
    assert len(client.pages.created) == 1
    assert client.pages.created[0]["parent"] == {"data_source_id": "ds-for-db"}
    assert client.pages.updated == []


def test_notion_sink_updates_when_existing_page_exists():
    client = FakeNotionClient(results=[{"id": "page-1"}])
    sink = NotionSink(client)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "updated"
    assert client.pages.created == []
    assert client.pages.updated[0]["page_id"] == "page-1"


def test_notion_sink_dry_run_queries_but_does_not_write():
    client = FakeNotionClient(results=[{"id": "page-1"}])
    sink = NotionSink(client, dry_run=True)

    action = sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert action == "dry_run"
    assert client.databases.retrieve_calls == ["db"]
    assert client.data_sources.queries == [
        {"data_source_id": "ds-for-db", "filter": {"property": "Name"}},
    ]
    assert client.pages.created == []
    assert client.pages.updated == []


def test_notion_sink_resolves_data_source_id_once_per_database():
    client = FakeNotionClient(results=[])
    sink = NotionSink(client, min_interval=0.0)

    for _ in range(2):
        sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})

    assert client.databases.retrieve_calls == ["db"]  # discovery cached
    assert len(client.data_sources.queries) == 2
    assert len(client.pages.created) == 2


def test_notion_sink_rejects_database_with_multiple_data_sources():
    client = FakeNotionClient(
        data_sources_info=[{"id": "ds-1", "name": "Main"}, {"id": "ds-2", "name": "Other"}]
    )
    sink = NotionSink(client)

    with pytest.raises(ValueError, match="multiple data sources"):
        sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})
    assert client.pages.created == []


def test_notion_sink_rejects_database_with_no_data_sources():
    client = FakeNotionClient(data_sources_info=[])
    sink = NotionSink(client)

    with pytest.raises(ValueError, match="no data sources"):
        sink.upsert_page("db", filter_payload={"property": "Name"}, properties={"Name": {}})
    assert client.pages.created == []


def test_notion_sync_run_requires_user():
    from notion_sync.cli import app

    result = CliRunner().invoke(app, ["run", "--dry-run"])

    assert result.exit_code != 0
    assert "Missing option" in _strip_ansi(result.output)
    assert "--user" in _strip_ansi(result.output)


class _FakeDbUser:
    id = 7


def _make_cli_session(scalar_rows):
    """Return a fake ``sqlmodel.Session`` class for CLI tests.

    The CLI constructs ``Session(engine)`` and enters it as a context manager;
    ``scalars(...)`` always returns ``FakeScalarResult(scalar_rows)``.
    """

    class _CliFakeSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def scalars(self, stmt):
            return FakeScalarResult(scalar_rows)

    return _CliFakeSession


def test_cli_run_errors_for_unknown_user(monkeypatch):
    from notion_sync import cli

    monkeypatch.setattr(cli, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "Session", _make_cli_session([]))

    from notion_sync.cli import app

    result = CliRunner().invoke(
        app, ["run", "--user", "nobody", "--days-back", "1", "--dry-run"]
    )

    assert result.exit_code != 0
    assert "No Garmin user matches" in _strip_ansi(result.output)


def test_cli_run_resolves_user_and_calls_shared_run(monkeypatch):
    from notion_sync import cli

    captured = {}

    def fake_run_user_sync(user_id, **kwargs):
        captured["user_id"] = user_id
        captured.update(kwargs)
        return {"activities": {"status": "ok"}}

    monkeypatch.setattr(cli, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "Session", _make_cli_session([_FakeDbUser()]))
    monkeypatch.setattr(cli, "run_user_sync", fake_run_user_sync)

    from notion_sync.cli import app

    result = CliRunner().invoke(
        app, ["run", "--user", "somebody", "--days-back", "1"]
    )

    assert result.exit_code == 0
    assert captured["user_id"] == 7
    assert captured["data_types"] is None
    assert captured["start_date"] is not None
    assert captured["end_date"] is not None
    assert captured["dry_run"] is False
    assert "activities" in _strip_ansi(result.output)


def test_cli_run_reports_inactive_user(monkeypatch):
    from notion_sync import cli

    def inactive_run(_user_id, **_kwargs):
        raise ValueError("Garmin user id=7 is inactive")

    monkeypatch.setattr(cli, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "Session", _make_cli_session([_FakeDbUser()]))
    monkeypatch.setattr(cli, "run_user_sync", inactive_run)

    result = CliRunner().invoke(cli.app, ["run", "--user", "somebody"])

    assert result.exit_code != 0
    assert "inactive" in _strip_ansi(result.output)


# --------------------------------------------------------------------------- #
# NotionSink pacing; the Notion client owns retries
# --------------------------------------------------------------------------- #

class _FakeNotion404(APIResponseError):
    """An API error from the client, forwarded by the sink."""

    def __init__(self, headers=None):
        self.status = 404
        self.headers = headers or {}


def test_notion_sink_forwards_client_errors_without_its_own_retry():
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
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(APIResponseError):
        sink.upsert_page("db", filter_payload={"property": "X"}, properties={"X": {}})

    assert query["n"] == 1
    assert sleeps == []
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

    client = FakeNotionClient(results=[])  # empty -> create path (three calls:
    # data source discovery, query, create)
    sink = NotionSink(
        client,
        dry_run=False,
        min_interval=0.1,
        sleep=sleeps.append,
        monotonic=monotonic,
    )

    action = sink.upsert_page("db", filter_payload={"property": "X"}, properties={"X": {}})

    assert action == "created"
    # First call: no pacing (no prior call). Second and third: pace the remainder.
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.06, abs=0.001)
    assert sleeps[1] == pytest.approx(0.06, abs=0.001)


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
    assert "--start-date must be on or before --end-date" in _strip_ansi(result.output)
