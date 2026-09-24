# Per-User Notion Sync

## Goal

Sync one Garmin user's archived PostgreSQL data to that user's Notion
databases, replacing the current global environment-variable configuration
with per-user sync targets stored in the database. Phase 1 covers Katie's
account (activities, daily steps, personal records). Sleep is a future phase
and is explicitly out of scope here, but its configuration is preserved.

## Background

The current notion-sync reads three Notion database IDs from global env vars
(`NOTION_ACTIVITIES_DB_ID` and friends) shared by every user. Two Garmin users
are archived (Katie, Mike), and the Prefect flow refuses to run unpinned when
more than one active user exists, so the scheduled deployment is unusable as
shipped. Per-user targets fix both problems and make adding a second Notion
user a data change, not a code change.

`users` is owned by the Garmin ingestion side, so Notion (or any future sync
destination) configuration must not grow columns there.

## Stored Payload Alignment (required bug fixes)

Inspecting stored rows shows the Notion activity mapper and the activity
ingest parser disagree with the actual `raw_json` payload shape from
python-garminconnect. All 3,917 archived activities (both users) are affected.

| Field | Parser/mapper expects | Actual payload |
| --- | --- | --- |
| Start time | top-level `startTimeGMT` | `summaryDTO.startTimeGMT` |
| Distance, duration, calories, speeds, power | top-level | `summaryDTO.*` |
| Activity type | `activityType.typeKey` | `activityTypeDTO.typeKey` |
| Training effect fields | top-level | `summaryDTO.*` |

Consequences today: `activities.start_time` is NULL for every row (so the
sync's date window and ordering see nothing), and the activity mapper would
write pages titled "Unnamed Activity" with zeroed metrics. Phase 1 includes:

1. **Parser fix** (`garmin_sync/ingest/parsers/activity.py`): read
   `summaryDTO.startTimeGMT`, keeping the top-level key as a fallback for
   older payload shapes.
2. **Data migration**: backfill `activities.start_time` from
   `raw_json.summaryDTO.startTimeGMT` (GMT, tz-aware UTC). All 3,917 stored
   activities have this key, so coverage is complete. Idempotent update;
   rows with a value are left alone. (`personal_records.activity_type` is
   also mostly NULL, but Garmin omits `activityType` from 105 of 118 stored
   PR payloads — nothing to backfill from. Those records simply render as
   "Unknown" activity type in Notion, matching the upstream data.)
3. **Mapper fix** (`notion_sync/mappers.py`): read metrics from
   `summaryDTO` and type from `activityTypeDTO`, with fallbacks to top-level
   keys. `activityName` and `activityId` remain top-level in the real payload
   and keep working.

Daily summary and personal record payloads already match their mappers
(`totalSteps`, `dailyStepGoal`, `totalDistanceMeters`; `typeId`, `value`).

## Schema — `sync_targets`

One row per user per destination.

```
sync_targets
  id           BIGINT PK, auto-increment
  user_id      BIGINT NOT NULL REFERENCES users(id)
  target       TEXT NOT NULL        -- 'notion' today
  config_json  JSONB NOT NULL       -- destination-specific payload
  created_at   TIMESTAMPTZ
  updated_at   TIMESTAMPTZ
  UNIQUE (user_id, target)
```

Model: `SyncTarget` in `garmin_postgres/models/`, registered for Alembic.
Follows the thin-schema/JSONB-first convention; the Garmin side never reads
or writes this table.

### Notion config shape

```json
{
  "token": "secret_...",
  "databases": {
    "activities": "<activities-database-id>",
    "daily_steps": "<daily-steps-database-id>",
    "personal_records": "<personal-records-database-id>",
    "sleep": "<sleep-database-id>"
  }
}
```

The token lives here rather than in the environment: same posture as the
existing Garmin `tokens_json` precedent, and it allows different users to
sync to different Notion workspaces later. `sleep` is seeded now and stays
dormant (no sync code) until the sleep phase.

## Config Resolution

`notion_sync/targets.py`:

- `sync_target(session, user_id, target="notion") -> dict | None` returns
  `config_json` for the row, or None.
- A Notion accessor extracts the token and builds `databases` restricted to
  `DATA_TYPES`; unknown keys (`sleep`) are dropped with a warning.
- A missing `databases` entry makes that data type report `skipped`, exactly
  like a missing env var today.

## Notion-Sync Changes

- `run_sync(session, sink, targets: dict[str, str], *, data_types, ...)` —
  takes the per-user `{data_type: database_id}` mapping instead of
  `NotionSettings`.
- `notion_sync/config.py` (`NotionSettings`) and all `NOTION_*` env vars are
  removed. Dry runs need no token, as today.
- CLI `run --user <garmin_display_name>` resolves the user, loads the sync
  target, and errors clearly when the user has no `notion` row. The
  `config` subcommand lists sync targets per user instead of env-based
  status.

## Orchestrator Changes

- `sync_notion_user_task` loads the sync target in-session and passes the
  databases mapping (and token) through.
- Flow guard: an explicit `user` must exist and have a `notion` row, else
  ValueError. Unpinned runs sync every active user that has a `notion` row,
  sequentially — the "exactly one active user" restriction is removed.
- Deployment `notion-sync` pins
  `user: "f65ac324-cbcf-4438-8abe-30e8fedeec4d"` (Katie) so the daily 7am
  run is deterministic.

## Rollout

1. Merge and deploy the migration (creates `sync_targets`, backfills typed
   columns).
2. Seed Katie's row (idempotent):

   ```sql
   INSERT INTO sync_targets (user_id, target, config_json) VALUES
     (1, 'notion', '{
        "token": "secret_...",
        "databases": {
          "activities": "<activities-database-id>",
          "daily_steps": "<daily-steps-database-id>",
          "personal_records": "<personal-records-database-id>",
          "sleep": "<sleep-database-id>"
        }
     }')
   ON CONFLICT (user_id, target) DO UPDATE SET config_json = excluded.config_json;
   ```

3. Smoke test without touching Notion:
   `uv run notion-sync run --user f65ac324-... --days-back 3 --dry-run`.
4. One-day real sync, then spot-check pages in Notion.
5. Backfill from Katie's earliest data (daily summaries start 2018-01-01):
   `uv run notion-sync run --user f65ac324-... --start-date 2018-01-01`.
   ~6,400 rows ≈ 2 API calls each ≈ ~75 minutes at Notion pacing. Re-running
   with the same window resumes safely (upserts).
6. Pin the deployment user and let the daily schedule continue.

## Notion-Side Prerequisites

Each database must be shared with the Notion integration (••• → Connections)
and must already contain the properties below — the API rejects unknown
property names, and this design intentionally does not auto-create them.

- **Activities**: Garmin Activity ID (number), Date (date), Activity Type
  (select), Subactivity Type (select), Activity Name (title), Distance (km),
  Duration (min), Calories, Avg Power, Max Power, Aerobic, Anaerobic
  (numbers), Avg Pace (rich text), Training Effect, Aerobic Effect,
  Anaerobic Effect (selects), PR, Fav (checkboxes)
- **Daily Steps**: Activity Type (title), Date (date), Total Steps, Step
  Goal, Total Distance (km) (numbers)
- **Personal Records**: Date (date), Activity Type (select), Record (title),
  typeId (number), Value (rich text), Pace (rich text), PR (checkbox)

## Testing

- Unit: target resolution (known/unknown data types, missing row), new
  `run_sync` signature (skips, upsert paths), activity mapper against the
  real payload shape captured from stored rows, flow guard (user without a
  row; unpinned multi-user).
- Integration (testcontainers): migration creates `sync_targets` and
  backfills `start_time` from fixture payloads.
- Existing notion-sync and orchestrator tests updated for the new
  signatures.

## Future Work

- **Sleep phase**: sleep ingestion, table, historical backfill, and a Notion
  mapper. The `sleep` database ID is already seeded, so no config work.
- **Adding Mike**: one `sync_targets` INSERT plus a pinned deployment — no
  code changes.
