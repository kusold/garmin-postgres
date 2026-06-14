from datetime import date, datetime, timezone

from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from notion_sync.config import NotionSettings, get_settings
from notion_sync.mappers import activity_page, daily_steps_page, personal_record_page
from notion_sync.notion import NotionSink
from notion_sync.sync import run_sync
from typer.testing import CliRunner


class FakeDatabases:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
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
    def __init__(self, results=None):
        self.databases = FakeDatabases(results)
        self.pages = FakePages()


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, stmt):
        return FakeScalarResult(self.rows.pop(0))


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

    properties, filter_payload = daily_steps_page(summary)

    assert properties["Activity Type"]["title"][0]["text"]["content"] == "Walking"
    assert properties["Total Steps"]["number"] == 8432
    assert properties["Step Goal"]["number"] == 10000
    assert properties["Total Distance (km)"]["number"] == 6.2
    assert filter_payload["and"][0]["property"] == "Date"


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

    assert action == "created"
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
        timezone="UTC",
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
                "NOTION_TIMEZONE=America/Denver",
            ]
        )
    )

    settings = get_settings()

    assert settings.token == "from-file"
    assert settings.activities_database_id == "activities-db"
    assert settings.daily_steps_database_id == "steps-db"
    assert settings.personal_records_database_id == "records-db"
    assert settings.timezone == "America/Denver"


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
