# Per-User Notion Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace global env-var Notion configuration with a per-user `sync_targets` table, fix the activity payload-shape bugs blocking a real sync, and pin the scheduled Prefect deployment to Katie.

**Architecture:** A new `sync_targets` table (one row per user per destination, JSONB config) is resolved per-user by notion-sync and the Prefect orchestrator. The activity parser and Notion mapper are corrected to read the payload shape Garmin actually returns (`summaryDTO`/`activityTypeDTO`), with a one-time migration backfilling `activities.start_time` and `activity_type` from stored `raw_json`. `NotionSettings` and all `NOTION_*` env vars are deleted; the token lives in `config_json`.

**Tech Stack:** Python 3.13, uv, SQLModel + Alembic, PostgreSQL 17 (testcontainers via rootless Podman), typer, pytest, Prefect 3.8.

**Spec:** `specs/10-per-user-notion-sync.md` — read it before starting.

**Key context for zero-context engineers:**

- Run everything from the repo root. Tests: `uv run pytest` (rootless Podman must be running for testcontainers tests; unit tests run without it).
- Only run single test files during TDD loops: `uv run pytest path/to/test.py -v`.
- The "Garmin display name" is a UUID string (e.g. `f65ac324-cbcf-4438-8abe-30e8fedeec4d`) — Garmin returns UUIDs as display names. Never "fix" this.
- Alembic migrations live in `packages/garmin-postgres-core/src/garmin_postgres/alembic/versions/`. Current head revision: `8b7c4e6f2a11`.
- Commit after every task. End commit messages with `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

### Task 1: Fix activity parser for the real Garmin payload shape

The stored payloads nest start time under `summaryDTO.startTimeGMT` and activity type under `activityTypeDTO.typeKey`. The parser currently reads top-level `startTimeGMT` and `activityType.typeKey`, so both typed columns end up NULL. Keep the legacy paths as fallbacks.

**Files:**
- Modify: `apps/garmin-sync/src/garmin_sync/ingest/parsers/activity.py`
- Test: `tests/test_parsers.py`

- [ ] **Step 1: Add failing tests for the DTO payload shape**

In `tests/test_parsers.py`, add this fixture near the existing `SAMPLE_ACTIVITY` fixture (it is a trimmed copy of a real stored payload):

```python
SAMPLE_ACTIVITY_SUMMARY_DTO = {
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
    "metadataDTO": {"favorite": False},
}
```

Add these tests to the same test class that holds the existing `parse_activity` tests:

```python
    def test_parses_start_time_from_summary_dto(self):
        result = parse_activity(SAMPLE_ACTIVITY_SUMMARY_DTO, user_id=1)

        assert result.start_time == datetime(2026, 6, 20, 14, 26, 41, tzinfo=timezone.utc)

    def test_parses_activity_type_from_activity_type_dto(self):
        result = parse_activity(SAMPLE_ACTIVITY_SUMMARY_DTO, user_id=1)

        assert result.activity_type == "walking"

    def test_legacy_top_level_keys_still_parse(self):
        result = parse_activity(SAMPLE_ACTIVITY, user_id=1)

        assert result.start_time is not None
        assert result.activity_type is not None
```

Check the file's existing imports — add `datetime`/`timezone` if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers.py -v -k "summary_dto or activity_type_dto or legacy_top_level"`
Expected: the first two FAIL (`result.start_time` is None, `result.activity_type` is None), the third PASSES.

- [ ] **Step 3: Implement the parser fix**

Replace the body of `parse_activity` in `apps/garmin-sync/src/garmin_sync/ingest/parsers/activity.py` (keep `_parse_garmin_utc` unchanged) with:

```python
def _payload_dict(raw: dict, key: str) -> dict:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def parse_activity(raw: dict, user_id: int) -> Activity:
    """Parse a Garmin activity response into an Activity model.

    Args:
        raw: Raw activity dict from get_activity() (detailed) or
             get_activities_by_date() (summary). Handles both the
             summaryDTO/activityTypeDTO nesting returned by list endpoints
             and the legacy top-level startTimeGMT/activityType.typeKey
             shape returned by detail endpoints.
        user_id: The database user_id to associate with this activity.

    Returns:
        An Activity instance ready for database persistence.

    Raises:
        KeyError: If 'activityId' is missing.
    """
    activity_id = raw["activityId"]

    summary = _payload_dict(raw, "summaryDTO")
    type_dto = _payload_dict(raw, "activityTypeDTO")
    legacy_type = _payload_dict(raw, "activityType")
    activity_type = type_dto.get("typeKey") or legacy_type.get("typeKey")

    start_raw = summary.get("startTimeGMT") or raw.get("startTimeGMT")
    start_time = _parse_garmin_utc(start_raw) if start_raw else None

    return Activity(
        user_id=user_id,
        activity_id=activity_id,
        activity_type=activity_type,
        start_time=start_time,
        raw_json=raw,
    )
```

- [ ] **Step 4: Run the full parser test file**

Run: `uv run pytest tests/test_parsers.py -v`
Expected: all PASS (existing tests keep passing via the fallbacks).

- [ ] **Step 5: Commit**

```bash
git add apps/garmin-sync/src/garmin_sync/ingest/parsers/activity.py tests/test_parsers.py
git commit -m "fix(garmin): parse summaryDTO payload shape for activity start time and type

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Add the SyncTarget model

**Files:**
- Create: `packages/garmin-postgres-core/src/garmin_postgres/models/sync_target.py`
- Modify: `packages/garmin-postgres-core/src/garmin_postgres/models/__init__.py`

- [ ] **Step 1: Create the model**

Create `packages/garmin-postgres-core/src/garmin_postgres/models/sync_target.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class SyncTarget(BaseModel, table=True):
    """Per-user configuration for one sync destination (e.g. Notion).

    config_json is destination-specific. For target='notion' it holds
    {"token": "...", "databases": {"activities": "<id>", ...}}.
    """

    __tablename__ = "sync_targets"
    __table_args__ = (
        UniqueConstraint("user_id", "target", name="uq_sync_targets_user_id_target"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id"), nullable=False),
    )
    target: str = Field(sa_column=Column(String, nullable=False))
    config_json: dict = Field(sa_column=Column(JSONB, nullable=False))
```

- [ ] **Step 2: Register it in the models package**

In `packages/garmin-postgres-core/src/garmin_postgres/models/__init__.py`, add the import and `__all__` entry (alphabetical, after PersonalRecord):

```python
from garmin_postgres.models.sync_target import SyncTarget
```

and append `"SyncTarget",` to `__all__` after `"PersonalRecord",`.

- [ ] **Step 3: Verify it imports and maps**

Run: `uv run python -c "from garmin_postgres.models import SyncTarget; print(SyncTarget.__tablename__)"`
Expected: prints `sync_targets` with no import errors.

- [ ] **Step 4: Commit**

```bash
git add packages/garmin-postgres-core/src/garmin_postgres/models/sync_target.py packages/garmin-postgres-core/src/garmin_postgres/models/__init__.py
git commit -m "feat(core): add SyncTarget model for per-user sync destinations

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Migration — create `sync_targets`, backfill activity typed columns

**Files:**
- Create: `packages/garmin-postgres-core/src/garmin_postgres/alembic/versions/c2f7a91d4e30_add_sync_targets_backfill_activity_columns.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_migrations.py`:

```python
import json
from datetime import datetime, timezone


def test_migration_backfills_activity_columns_from_raw_json(postgres_container, engine):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", postgres_container.get_connection_url()
    )
    command.downgrade(alembic_cfg, "8b7c4e6f2a11")

    payload = {
        "activityTypeDTO": {"typeKey": "walking"},
        "summaryDTO": {"startTimeGMT": "2026-06-20T14:26:41.0"},
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (garmin_display_name) "
                "VALUES ('migration-test-user') "
                "ON CONFLICT (garmin_display_name) DO NOTHING"
            )
        )
        user_id = conn.execute(
            text("SELECT id FROM users WHERE garmin_display_name = 'migration-test-user'")
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO activities (user_id, activity_id, raw_json) "
                "VALUES (:user_id, 999999, CAST(:payload AS jsonb))"
            ),
            {"user_id": user_id, "payload": json.dumps(payload)},
        )

    command.upgrade(alembic_cfg, "head")

    with engine.begin() as conn:
        start_time, activity_type = conn.execute(
            text("SELECT start_time, activity_type FROM activities WHERE activity_id = 999999")
        ).one()
        table_exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE tablename = 'sync_targets')"
            )
        ).scalar()

    assert start_time == datetime(2026, 6, 20, 14, 26, 41, tzinfo=timezone.utc)
    assert activity_type == "walking"
    assert table_exists is True
```

If `tests/test_migrations.py` already imports `datetime`/`timezone`/`json`, don't duplicate the imports.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_migrations.py::test_migration_backfills_activity_columns_from_raw_json -v`
Expected: FAIL — either `sync_targets` doesn't exist yet on upgrade to a newer head (no — the migration doesn't exist, so "head" is still `8b7c4e6f2a11` and `command.downgrade` to it is a no-op), or the final `pg_tables` assertion fails because the table was never created. The failure mode will be the `table_exists is True` assertion or `start_time` being None.

- [ ] **Step 3: Create the migration**

Create `packages/garmin-postgres-core/src/garmin_postgres/alembic/versions/c2f7a91d4e30_add_sync_targets_backfill_activity_columns.py`:

```python
"""add sync_targets, backfill activity columns

Revision ID: c2f7a91d4e30
Revises: 8b7c4e6f2a11
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'c2f7a91d4e30'
down_revision: Union[str, None] = '8b7c4e6f2a11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sync_targets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('target', sa.String(), nullable=False),
        sa.Column('config_json', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_targets')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sync_targets_user_id_users')),
        sa.UniqueConstraint('user_id', 'target', name=op.f('uq_sync_targets_user_id_target')),
    )
    # Garmin list endpoints nest these under summaryDTO/activityTypeDTO.
    # startTimeGMT is GMT, so interpret the naive value as UTC explicitly.
    op.execute(
        """
        UPDATE activities
        SET start_time = (raw_json->'summaryDTO'->>'startTimeGMT')::timestamp AT TIME ZONE 'UTC'
        WHERE start_time IS NULL
          AND raw_json->'summaryDTO'->>'startTimeGMT' IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE activities
        SET activity_type = raw_json->'activityTypeDTO'->>'typeKey'
        WHERE activity_type IS NULL
          AND raw_json->'activityTypeDTO'->>'typeKey' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table('sync_targets')
```

- [ ] **Step 4: Run the migration tests**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: all PASS, including the existing upgrade/downgrade round-trip tests (downgrade drops `sync_targets`).

- [ ] **Step 5: Commit**

```bash
git add packages/garmin-postgres-core/src/garmin_postgres/alembic/versions/c2f7a91d4e30_add_sync_targets_backfill_activity_columns.py tests/test_migrations.py
git commit -m "feat(core): add sync_targets table and backfill activity columns

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Notion target resolver

**Files:**
- Create: `apps/notion-sync/src/notion_sync/targets.py`
- Test: `apps/notion-sync/tests/test_targets.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/notion-sync/tests/test_targets.py`:

```python
import logging

from garmin_postgres.models.sync_target import SyncTarget
from notion_sync.targets import find_user, notion_sync_config, sync_target


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    """Fake session; ``rows`` is a list of row-lists popped in call order."""

    def __init__(self, rows):
        self.rows = rows

    def scalars(self, stmt):
        return FakeScalarResult(self.rows.pop(0))


def test_sync_target_returns_config_json_for_matching_row():
    row = SyncTarget(id=1, user_id=7, target="notion", config_json={"token": "t"})
    session = FakeSession(rows=[[row]])

    assert sync_target(session, user_id=7) == {"token": "t"}


def test_sync_target_returns_none_when_no_row():
    session = FakeSession(rows=[[]])

    assert sync_target(session, user_id=7) is None


def test_notion_sync_config_extracts_token_and_known_databases():
    config = {
        "token": "secret_abc",
        "databases": {
            "activities": "db-act",
            "daily_steps": "db-steps",
            "personal_records": "db-pr",
            "sleep": "db-sleep",
        },
    }
    session = FakeSession(rows=[[SyncTarget(config_json=config)]])

    token, databases = notion_sync_config(session, user_id=1)

    assert token == "secret_abc"
    assert databases == {
        "activities": "db-act",
        "daily_steps": "db-steps",
        "personal_records": "db-pr",
    }


def test_notion_sync_config_warns_and_drops_unknown_data_types(caplog):
    session = FakeSession(
        rows=[[SyncTarget(config_json={"databases": {"sleep": "db-sleep"}})]]
    )

    with caplog.at_level(logging.WARNING):
        token, databases = notion_sync_config(session, user_id=1)

    assert token is None
    assert databases == {}
    assert any("sleep" in record.message for record in caplog.records)


def test_notion_sync_config_handles_missing_row():
    session = FakeSession(rows=[[]])

    token, databases = notion_sync_config(session, user_id=1)

    assert token is None
    assert databases == {}


def test_find_user_matches_by_display_name():
    from garmin_postgres.models.user import User

    user = User(id=3, garmin_display_name="f65ac324-0000")
    session = FakeSession(rows=[[user]])

    assert find_user(session, "f65ac324-0000") is user

    missing = FakeSession(rows=[[]])
    assert find_user(missing, "nobody") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notion-sync/tests/test_targets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notion_sync.targets'`.

- [ ] **Step 3: Implement targets.py**

Create `apps/notion-sync/src/notion_sync/targets.py`:

```python
import logging

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.sync_target import SyncTarget
from garmin_postgres.models.user import User
from notion_sync.sync import DATA_TYPES

logger = logging.getLogger(__name__)


def find_user(session: Session, display_name: str) -> User | None:
    stmt = select(User).where(User.garmin_display_name == display_name)
    return session.scalars(stmt).first()


def sync_target(session: Session, user_id: int, target: str = "notion") -> dict | None:
    """Return the destination-specific config_json for a user, or None."""
    stmt = select(SyncTarget).where(
        SyncTarget.user_id == user_id,
        SyncTarget.target == target,
    )
    row = session.scalars(stmt).first()
    return row.config_json if row is not None else None


def notion_sync_config(session: Session, user_id: int) -> tuple[str | None, dict[str, str]]:
    """Extract the Notion token and the {data_type: database_id} mapping.

    Database keys that are not syncable data types yet (e.g. 'sleep') are
    dropped with a warning so dormant configuration is harmless.
    """
    config = sync_target(session, user_id) or {}
    databases = {}
    for data_type, database_id in (config.get("databases") or {}).items():
        if data_type not in DATA_TYPES:
            logger.warning(
                "Skipping data type %r in sync_targets 'notion' config "
                "(not a syncable data type)",
                data_type,
            )
            continue
        if database_id:
            databases[data_type] = database_id
    return config.get("token"), databases
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notion-sync/tests/test_targets.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/notion-sync/src/notion_sync/targets.py apps/notion-sync/tests/test_targets.py
git commit -m "feat(notion-sync): resolve per-user Notion sync targets

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: run_sync takes a targets mapping instead of NotionSettings

**Files:**
- Modify: `apps/notion-sync/src/notion_sync/sync.py`
- Test: `apps/notion-sync/tests/test_notion_sync.py`

- [ ] **Step 1: Update the tests first**

In `apps/notion-sync/tests/test_notion_sync.py`:

1. Delete the `NotionSettings` import (line 13: `from notion_sync.config import NotionSettings, get_settings`).
2. Replace the body of `test_run_sync_skips_unconfigured_databases` (lines 202-216) with:

```python
def test_run_sync_skips_unconfigured_databases():
    session = FakeSession(rows=[])
    sink = NotionSink(FakeNotionClient(), dry_run=True)

    result = run_sync(session, sink, {})

    assert result["activities"]["status"] == "skipped"
    assert result["daily_steps"]["status"] == "skipped"
    assert result["personal_records"]["status"] == "skipped"
```

3. Find the `_settings_with_activities` helper (around line 274) — it builds a `NotionSettings`. Replace it with:

```python
def _targets_with_activities():
    """Build a targets mapping with activities configured."""
    return {"activities": "activities-db"}
```

and change its call sites from `run_sync(session, sink, _settings_with_activities(), ...)` to `run_sync(session, sink, _targets_with_activities(), ...)`. Grep for `_settings_with_activities(` to find every call site.

4. Also delete the three `NotionSettings` tests that no longer apply (they test env-file loading): `test_notion_settings_load_from_dotenv`, `test_notion_settings_process_env_overrides_dotenv`, `test_notion_settings_loads_docker_mounted_env_file`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notion-sync/tests/test_notion_sync.py -v`
Expected: FAIL — `run_sync` still expects `settings: NotionSettings` and the import of `notion_sync.config` fails once deleted… note the import is deleted from the test but `sync.py` still imports it. Failures will be TypeError/signature mismatches or ImportError propagating from sync.py.

- [ ] **Step 3: Update sync.py**

In `apps/notion-sync/src/notion_sync/sync.py`:

1. Delete line 13 (`from notion_sync.config import NotionSettings`).
2. Change `_sync_table`'s signature parameter `database_id: str | None` and its `not_configured_error: str` — replace the `not_configured_error` keyword with `label: str` and build the message internally. The new check at the top of `_sync_table`:

```python
    if not database_id:
        return SyncResult(
            status="skipped",
            skipped=1,
            error=f"No Notion database configured for {label} in sync_targets",
        )
```

3. Update the three call sites inside `run_sync` to pass `targets.get("activities")` / `targets.get("daily_steps")` / `targets.get("personal_records")` and `label="activities"` / `label="daily_steps"` / `label="personal_records"` (remove the old `not_configured_error=` arguments).
4. Change `run_sync`'s signature from `settings: NotionSettings` to `targets: dict[str, str]`.

- [ ] **Step 4: Run the notion-sync tests**

Run: `uv run pytest apps/notion-sync/tests/test_notion_sync.py apps/notion-sync/tests/test_targets.py -v`
Expected: all PASS. (`sync_activities` / `sync_daily_steps` / `sync_personal_records` keep their existing signatures minus the settings indirection — only `run_sync` and `_sync_table` change.)

- [ ] **Step 5: Commit**

```bash
git add apps/notion-sync/src/notion_sync/sync.py apps/notion-sync/tests/test_notion_sync.py
git commit -m "refactor(notion-sync): run_sync takes per-user targets mapping

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Fix the activity mapper for the real payload shape

The mapper reads top-level keys (`distance`, `duration`, `activityType.typeKey`, `favorite`, …) but stored payloads nest metrics in `summaryDTO`, type in `activityTypeDTO`, and flags in `metadataDTO`.

**Files:**
- Modify: `apps/notion-sync/src/notion_sync/mappers.py`
- Test: `apps/notion-sync/tests/test_notion_sync.py`

- [ ] **Step 1: Add the failing test**

In `apps/notion-sync/tests/test_notion_sync.py`, add after `test_activity_page_maps_postgres_activity_to_notion_properties`:

```python
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
    assert properties["Calories"]["number"] == 146
    assert properties["Avg Pace"]["rich_text"][0]["text"]["content"] == "16:36 min/km"
    assert properties["Fav"]["checkbox"] is True
    assert properties["PR"]["checkbox"] is False
    assert properties["Date"]["date"]["start"] == "2026-06-20T14:26:41+00:00"
    assert icon is not None
```

(The pace assertion is exact: `1000 / (1.003999948 * 60)` ≈ 16.6004 min/km, so `format_pace` renders `16:36 min/km`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest apps/notion-sync/tests/test_notion_sync.py::test_activity_page_maps_summary_dto_payload_shape -v`
Expected: FAIL — "Activity Type" select is "Unknown" (or the mapper crashes on `raw.get("activityType", {})`), distance is 0.0.

- [ ] **Step 3: Implement the mapper fix**

In `apps/notion-sync/src/notion_sync/mappers.py`, add helpers above `activity_filter`:

```python
def _metric(raw: dict, key: str):
    """Read a metric from summaryDTO, falling back to the top-level key."""
    summary = raw.get("summaryDTO")
    if not isinstance(summary, dict):
        summary = {}
    return summary.get(key, raw.get(key))


def _metadata(raw: dict) -> dict:
    value = raw.get("metadataDTO")
    return value if isinstance(value, dict) else {}
```

Replace `activity_page` with:

```python
def activity_page(activity: Activity) -> tuple[dict, dict, dict | None]:
    raw = activity.raw_json or {}
    activity_name = format_entertainment(raw.get("activityName", "Unnamed Activity"))
    type_dto = raw.get("activityTypeDTO")
    legacy_type = raw.get("activityType")
    type_key = None
    for type_payload in (type_dto, legacy_type):
        if isinstance(type_payload, dict) and type_payload.get("typeKey"):
            type_key = type_payload["typeKey"]
            break
    if not type_key:
        type_key = activity.activity_type
    activity_type, activity_subtype = format_activity_type(type_key, activity_name)

    metadata = _metadata(raw)
    properties = {
        "Garmin Activity ID": {"number": activity.activity_id},
        "Date": {"date": {"start": notion_date(activity.start_time or _metric(raw, "startTimeGMT"))}},
        "Activity Type": {"select": {"name": activity_type}},
        "Subactivity Type": {"select": {"name": activity_subtype}},
        "Activity Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance (km)": {"number": round(number(_metric(raw, "distance")) / 1000, 2)},
        "Duration (min)": {"number": round(number(_metric(raw, "duration")) / 60, 2)},
        "Calories": {"number": round(number(_metric(raw, "calories")))},
        "Avg Pace": {"rich_text": [{"text": {"content": format_pace(_metric(raw, "averageSpeed"))}}]},
        "Avg Power": {"number": round(number(_metric(raw, "avgPower")), 1)},
        "Max Power": {"number": round(number(_metric(raw, "maxPower")), 1)},
        "Training Effect": {"select": {"name": format_training_effect(_metric(raw, "trainingEffectLabel"))}},
        "Aerobic": {"number": round(number(_metric(raw, "aerobicTrainingEffect")), 1)},
        "Aerobic Effect": {"select": {"name": format_training_message(_metric(raw, "aerobicTrainingEffectMessage"))}},
        "Anaerobic": {"number": round(number(_metric(raw, "anaerobicTrainingEffect")), 1)},
        "Anaerobic Effect": {"select": {"name": format_training_message(_metric(raw, "anaerobicTrainingEffectMessage"))}},
        "PR": {"checkbox": bool(raw.get("pr") or metadata.get("personalRecord", False))},
        "Fav": {"checkbox": bool(raw.get("favorite") or metadata.get("favorite", False))},
    }

    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)
    icon = {"type": "external", "external": {"url": icon_url}} if icon_url else None
    return properties, activity_filter(activity, activity_name, activity_type), icon
```

`activity_filter` and the daily-steps / personal-record mappers stay unchanged (their payload shapes already match).

- [ ] **Step 4: Run the mapper tests**

Run: `uv run pytest apps/notion-sync/tests/test_notion_sync.py -v -k "activity_page"`
Expected: both the legacy-shape test and the new DTO-shape test PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/notion-sync/src/notion_sync/mappers.py apps/notion-sync/tests/test_notion_sync.py
git commit -m "fix(notion-sync): map summaryDTO payload shape for activities

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: CLI reads sync targets; delete NotionSettings

**Files:**
- Modify: `apps/notion-sync/src/notion_sync/cli.py`
- Delete: `apps/notion-sync/src/notion_sync/config.py`
- Test: `apps/notion-sync/tests/test_notion_sync.py`

- [ ] **Step 1: Update the CLI tests first**

In `apps/notion-sync/tests/test_notion_sync.py`, replace `test_notion_sync_run_requires_user` and add new CLI tests:

```python
def test_notion_sync_run_requires_user():
    from notion_sync.cli import app

    result = CliRunner().invoke(app, ["run", "--dry-run"])

    assert result.exit_code != 0
    assert "Missing option" in _strip_ansi(result.output)
    assert "--user" in _strip_ansi(result.output)


class _FakeDbUser:
    id = 7


def test_cli_run_errors_for_unknown_user(monkeypatch):
    from notion_sync import cli

    class FakeSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def scalars(self, stmt):
            return FakeScalarResult([])

    monkeypatch.setattr(cli, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "Session", FakeSession)

    from notion_sync.cli import app

    result = CliRunner().invoke(
        app, ["run", "--user", "nobody", "--days-back", "1", "--dry-run"]
    )

    assert result.exit_code != 0
    assert "No Garmin user matches" in _strip_ansi(result.output)


def test_cli_run_requires_token_for_non_dry_run(monkeypatch):
    from notion_sync import cli

    class FakeSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def scalars(self, stmt):
            return FakeScalarResult([_FakeDbUser()])

    monkeypatch.setattr(cli, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "Session", FakeSession)
    monkeypatch.setattr(
        cli, "notion_sync_config", lambda session, user_id: (None, {"activities": "db"})
    )

    from notion_sync.cli import app

    result = CliRunner().invoke(
        app, ["run", "--user", "somebody", "--days-back", "1"]
    )

    assert result.exit_code != 0
    assert "token" in _strip_ansi(result.output)
```

Note: `notion_sync_config` must be imported into `cli.py`'s namespace (`from notion_sync.targets import find_user, notion_sync_config`) so `monkeypatch.setattr(cli, ...)` works.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest apps/notion-sync/tests/test_notion_sync.py -v -k "cli_run or requires_user"`
Expected: the two new tests FAIL (old CLI doesn't resolve users from the DB).

- [ ] **Step 3: Rewrite cli.py**

Replace the full contents of `apps/notion-sync/src/notion_sync/cli.py` with:

```python
from datetime import date, timedelta

import typer
from notion_client import Client
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.config import get_settings as get_db_settings
from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User
from notion_sync.notion import NotionSink
from notion_sync.sync import DATA_TYPES, run_sync
from notion_sync.targets import find_user, notion_sync_config

app = typer.Typer(name="notion-sync", help="Sync archived Garmin data from PostgreSQL to Notion.")


def _date_range(days_back: int | None, start_date: str | None, end_date: str | None) -> tuple[date | None, date | None]:
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_start and parsed_end and parsed_start > parsed_end:
        typer.echo("--start-date must be on or before --end-date", err=True)
        raise typer.Exit(1)
    if parsed_start and days_back is not None:
        typer.echo("--days-back is ignored when --start-date is provided", err=True)
    if parsed_start:
        return parsed_start, parsed_end
    if days_back is None:
        return None, parsed_end
    if days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)
    end = parsed_end or date.today()
    return end - timedelta(days=days_back - 1), end


@app.callback()
def main() -> None:
    """Sync archived Garmin data from PostgreSQL to Notion."""


@app.command()
def run(
    user: str = typer.Option(..., "--user", "-u", help="Garmin display name to sync"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    data_type: list[str] = typer.Option(None, "--data-type", "-t", help="Data types to sync (activities, daily_steps, personal_records)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Query data but don't read or write Notion pages"),
) -> None:
    """Sync archived data to Notion for one user's configured databases."""
    selected = data_type if data_type else None
    invalid = sorted(set(selected or []) - set(DATA_TYPES))
    if invalid:
        typer.echo(f"Unsupported data type(s): {', '.join(invalid)}", err=True)
        raise typer.Exit(1)

    parsed_start, parsed_end = _date_range(days_back, start_date, end_date)

    engine = get_engine()
    with Session(engine) as session:
        db_user = find_user(session, user)
        if db_user is None:
            typer.echo(f"No Garmin user matches --user {user!r}", err=True)
            raise typer.Exit(1)
        token, targets = notion_sync_config(session, db_user.id)

    if not token and not dry_run:
        typer.echo(
            f"sync_targets 'notion' config for user {user!r} has no token "
            "(required unless --dry-run is used)",
            err=True,
        )
        raise typer.Exit(1)

    client = Client(auth=token or "dry-run")
    sink = NotionSink(client, dry_run=dry_run)
    with Session(engine) as session:
        results = run_sync(
            session,
            sink,
            targets,
            data_types=selected,
            start_date=parsed_start,
            end_date=parsed_end,
            user_filter=user,
        )

    for dtype, info in results.items():
        typer.echo(f"  {dtype}: {info}")


@app.command()
def config() -> None:
    """Show the database URL and each user's configured Notion targets."""
    db_settings = get_db_settings()
    typer.echo(f"database_url: {db_settings.database_url}")
    engine = get_engine()
    with Session(engine) as session:
        for db_user in session.scalars(select(User).order_by(User.id)).all():
            token, targets = notion_sync_config(session, db_user.id)
            configured = ", ".join(f"{k}={v}" for k, v in sorted(targets.items())) or "none"
            token_state = "token: yes" if token else "token: no"
            typer.echo(f"{db_user.garmin_display_name}: {configured} ({token_state})")


if __name__ == "__main__":
    app()
```

Then delete the settings module:

```bash
git rm apps/notion-sync/src/notion_sync/config.py
```

- [ ] **Step 4: Check for stragglers and run the suite**

Run: `grep -rn "notion_sync.config\|NotionSettings\|NOTION_" --include="*.py" apps/ tests/ | grep -v pycache`
Expected: only hits inside `apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py` (fixed in Task 8) — no others. Fix any others found.

Run: `uv run pytest apps/notion-sync/tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/notion-sync/
git commit -m "feat(notion-sync): resolve databases and token per user in CLI

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Orchestrator task reads sync targets

**Files:**
- Modify: `apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py`
- Test: `apps/garmin-orchestrator/tests/test_notion_orchestrator.py`

- [ ] **Step 1: Update the task tests first**

In `apps/garmin-orchestrator/tests/test_notion_orchestrator.py`, replace `test_notion_user_task_bounds_dated_rows_but_replays_all_personal_records` and `test_notion_user_task_requires_token_for_writes` with:

```python
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

    def fake_run_sync(current_session, sink, targets, **kwargs):
        assert current_session is session
        assert sink == "sink"
        assert targets == {"activities": "db-act", "personal_records": "db-pr"}
        calls.append(kwargs)
        return {data_type: _sync_result() for data_type in kwargs["data_types"]}

    monkeypatch.setattr(
        notion_tasks,
        "find_user",
        lambda session, display_name: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(
        notion_tasks,
        "notion_sync_config",
        lambda session, user_id: (
            "secret",
            {"activities": "db-act", "personal_records": "db-pr"},
        ),
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
    class FakeSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(
        notion_tasks, "find_user", lambda session, display_name: SimpleNamespace(id=7)
    )
    monkeypatch.setattr(
        notion_tasks, "notion_sync_config", lambda session, user_id: (None, {})
    )
    monkeypatch.setattr(notion_tasks, "get_engine", lambda: "engine")
    monkeypatch.setattr(notion_tasks, "Session", FakeSession)

    with pytest.raises(ValueError, match="token"):
        notion_tasks.sync_notion_user_task.fn(
            user="mike",
            data_types=["activities"],
            start_date=date(2026, 7, 29),
            end_date=date(2026, 7, 30),
        )


def test_notion_user_task_requires_databases(monkeypatch):
    class FakeSession:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(
        notion_tasks, "find_user", lambda session, display_name: SimpleNamespace(id=7)
    )
    monkeypatch.setattr(
        notion_tasks, "notion_sync_config", lambda session, user_id: ("secret", {})
    )
    monkeypatch.setattr(notion_tasks, "get_engine", lambda: "engine")
    monkeypatch.setattr(notion_tasks, "Session", FakeSession)

    with pytest.raises(ValueError, match="no databases"):
        notion_tasks.sync_notion_user_task.fn(
            user="mike",
            data_types=["activities"],
            start_date=date(2026, 7, 29),
            end_date=date(2026, 7, 30),
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest apps/garmin-orchestrator/tests/test_notion_orchestrator.py -v -k "notion_user_task"`
Expected: FAIL — `notion_tasks` has no `find_user` / `notion_sync_config` attributes to patch.

- [ ] **Step 3: Implement the task changes**

In `apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py`:

1. Replace the `get_notion_settings` import with the resolver imports:

```python
from notion_sync.targets import find_user, notion_sync_config
```

2. Replace the body of `sync_notion_user_task` (keep the `@task(...)` decorator and docstring) with:

```python
    run_logger = _get_logger()
    engine = get_engine()
    with Session(engine) as session:
        db_user = find_user(session, user)
        if db_user is None:
            raise ValueError(f"No Garmin user matches user={user!r}")
        token, targets = notion_sync_config(session, db_user.id)

    if not token and not dry_run:
        raise ValueError(
            f"sync_targets 'notion' config for user={user!r} has no token "
            "(required unless dry_run is enabled)"
        )
    if not targets:
        raise ValueError(
            f"sync_targets 'notion' config for user={user!r} has no databases"
        )

    run_logger.info(
        "Starting Notion sync: user=%s window=%s..%s data_types=%s dry_run=%s",
        user,
        start_date,
        end_date,
        data_types,
        dry_run,
    )
    client = Client(auth=token or "dry-run")
    sink = NotionSink(client, dry_run=dry_run)
    dated_data_types = [
        data_type for data_type in data_types if data_type != PERSONAL_RECORDS
    ]

    with Session(engine) as session:
        results: dict[str, dict[str, Any]] = {}
        if dated_data_types:
            results.update(
                run_sync(
                    session,
                    sink,
                    targets,
                    data_types=dated_data_types,
                    start_date=start_date,
                    end_date=end_date,
                    user_filter=user,
                )
            )
        if PERSONAL_RECORDS in data_types:
            results.update(
                run_sync(
                    session,
                    sink,
                    targets,
                    data_types=[PERSONAL_RECORDS],
                    user_filter=user,
                )
            )

    ordered_results = {
        data_type: results[data_type]
        for data_type in data_types
        if data_type in results
    }
    run_logger.info("Notion sync finished: user=%s results=%s", user, ordered_results)
    return ordered_results
```

- [ ] **Step 4: Run the task tests**

Run: `uv run pytest apps/garmin-orchestrator/tests/test_notion_orchestrator.py -v -k "notion_user_task"`
Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py apps/garmin-orchestrator/tests/test_notion_orchestrator.py
git commit -m "feat(orchestrator): sync task resolves per-user Notion targets

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Flow syncs all configured users; pin deployment to Katie

**Files:**
- Modify: `apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py` (new task)
- Modify: `apps/garmin-orchestrator/src/garmin_orchestrator/notion_flows.py`
- Modify: `prefect.yaml:97`
- Test: `apps/garmin-orchestrator/tests/test_notion_orchestrator.py`

- [ ] **Step 1: Update the flow tests first**

In `apps/garmin-orchestrator/tests/test_notion_orchestrator.py`:

1. In `test_notion_flow_infers_single_active_user_and_returns_summary` (and in `test_notion_flow_failure_policy_raises_for_partial_when_requested`), add this patch next to the `resolve_active_users_task` patch:

```python
    monkeypatch.setattr(
        notion_flows,
        "resolve_notion_configured_users_task",
        lambda candidates: candidates,
    )
```

2. Replace `test_notion_flow_requires_user_when_multiple_are_active` with two tests:

```python
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
        lambda candidates: [candidates[1]],
    )
    monkeypatch.setattr(
        notion_flows,
        "sync_notion_user_task",
        lambda **kwargs: calls.append(kwargs)
        or {
            "activities": _sync_result(),
            "daily_steps": _sync_result(),
            "personal_records": _sync_result(),
        },
    )
    monkeypatch.setattr(
        notion_flows, "_publish_summary_artifact", lambda summary: None
    )

    result = notion_sync_flow.fn()

    assert [call["user"] for call in calls] == ["katie"]
    assert result["results"][0]["user"] == "katie"


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest apps/garmin-orchestrator/tests/test_notion_orchestrator.py -v -k "notion_flow"`
Expected: FAIL — `notion_flows` has no attribute `resolve_notion_configured_users_task`.

- [ ] **Step 3: Add the resolver task to notion_tasks.py**

In `apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py`, add imports and the task:

```python
from sqlalchemy import select
from garmin_postgres.models.sync_target import SyncTarget
```

```python
@task(name="resolve-notion-configured-users")
def resolve_notion_configured_users_task(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter candidate users down to those with a 'notion' sync target row."""
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(SyncTarget.user_id).where(SyncTarget.target == "notion")
        configured_ids = set(session.scalars(stmt).all())
    return [u for u in candidates if u["id"] in configured_ids]
```

- [ ] **Step 4: Rework the flow**

In `apps/garmin-orchestrator/src/garmin_orchestrator/notion_flows.py`:

1. Change the import from `garmin_orchestrator.notion_tasks import sync_notion_user_task` to:

```python
from garmin_orchestrator.notion_tasks import (
    resolve_notion_configured_users_task,
    sync_notion_user_task,
)
```

2. Replace the user-resolution and sync block inside `notion_sync_flow` (from `users = resolve_active_users_task(user_filter=user)` through the `results = [...]` line) with:

```python
    candidates = resolve_active_users_task(user_filter=user)
    if user is not None and not candidates:
        raise ValueError(f"No active Garmin user matched user={user!r}")
    users = resolve_notion_configured_users_task(candidates)
    if not users:
        raise ValueError("No active Garmin user has a 'notion' sync target configured")

    run_logger.info(
        "Starting PostgreSQL to Notion flow: window=%s..%s users=%s "
        "data_types=%s dry_run=%s fail_on_partial=%s",
        window["start_date"],
        window["end_date"],
        [u["display_name"] for u in users],
        selected_data_types,
        dry_run,
        fail_on_partial,
    )
    results = []
    for sync_user in users:
        notion_results = sync_notion_user_task(
            user=sync_user["display_name"],
            data_types=selected_data_types,
            start_date=window["start_date"],
            end_date=window["end_date"],
            dry_run=dry_run,
        )
        results.append({"user": sync_user["display_name"], **notion_results})
```

3. In the final `run_logger.info(...)` call, the format string references `users[0]["display_name"]` — change that argument to `[u["display_name"] for u in users]` (or simply drop it; keep the log line compiling).

- [ ] **Step 5: Pin the deployment**

In `prefect.yaml`, in the `notion-sync` deployment's `parameters:` block, change `user: null` to:

```yaml
      user: "f65ac324-cbcf-4438-8abe-30e8fedeec4d"
```

- [ ] **Step 6: Run the orchestrator tests**

Run: `uv run pytest apps/garmin-orchestrator/tests/ -v`
Expected: all PASS (including `test_notion_sync_cli_calls_flow_with_parsed_options`, which is unchanged).

- [ ] **Step 7: Commit**

```bash
git add apps/garmin-orchestrator/src/garmin_orchestrator/notion_tasks.py apps/garmin-orchestrator/src/garmin_orchestrator/notion_flows.py prefect.yaml apps/garmin-orchestrator/tests/test_notion_orchestrator.py
git commit -m "feat(orchestrator): sync every user with a Notion target, pin Katie

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: Docs and full verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Remove NOTION_* from .env.example**

In `.env.example`, delete the four lines `NOTION_TOKEN=`, `NOTION_ACTIVITIES_DB_ID=`, `NOTION_DAILY_STEPS_DB_ID=`, `NOTION_PERSONAL_RECORDS_DB_ID=`.

- [ ] **Step 2: Update README**

In `README.md`, find the block that exports `NOTION_TOKEN` / `NOTION_*_DB_ID` (around line 63) and replace it with a short section explaining per-user configuration, including the seed SQL from `specs/10-per-user-notion-sync.md` (with the token placeholder kept as `secret_...`) and a pointer to the spec's "Notion-Side Prerequisites" property checklist. Keep it brief — the spec is the source of truth.

- [ ] **Step 3: Run the entire test suite**

Run: `uv run pytest`
Expected: all PASS. If testcontainers tests fail with connection errors, confirm rootless Podman is running (`podman ps`) — that is an environment issue, not a code issue; re-run once Podman is up.

- [ ] **Step 4: Verify the migration against the dev database**

```bash
uv run alembic upgrade head
```

Then verify the backfill landed (needs the dev DB running):

```bash
uv run python -c "
from garmin_postgres.db import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    nulls = conn.execute(text('SELECT count(*) FROM activities WHERE start_time IS NULL')).scalar()
    typed = conn.execute(text('SELECT count(*) FROM activities WHERE activity_type IS NULL')).scalar()
    print('null start_time:', nulls, '| null activity_type:', typed)
"
```

Expected: `null start_time: 0 | null activity_type: 0`.

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md
git commit -m "docs: per-user Notion configuration replaces env vars

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Post-implementation rollout (operational, outside this plan's code)

For whoever operates this after merge, from `specs/10-per-user-notion-sync.md`:

1. Seed Katie's `sync_targets` row (token + the four database IDs).
2. `uv run notion-sync run --user f65ac324-cbcf-4438-8abe-30e8fedeec4d --days-back 3 --dry-run`
3. One-day real sync; spot-check pages in Notion.
4. Backfill: `--start-date 2018-01-01` (~75 min, resumable).
5. Redeploy Prefect so the pinned `notion-sync` deployment picks up `user: f65ac324-…`.
