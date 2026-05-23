# Data Ingestion

## Overview

The ingestion pipeline fetches data from Garmin Connect and upserts it into PostgreSQL. It runs on a schedule (systemd timer, twice daily) and is also invocable manually via CLI.

## Scheduling

- **Mechanism**: Systemd user timer
- **Default schedule**: Twice daily (e.g., 06:00 and 18:00)
- **Token refresh**: Tokens are refreshed on each run by garth. Valid for ~1 year.
- **Backfill**: `garmin-postgres ingest backfill --days 365` fetches historical data.

## Ingestion Order

Data is fetched in dependency order to satisfy foreign keys:

1. **User profile** — `get_user_profile()`, `get_userprofile_settings()`
2. **Devices** — `get_devices()`, `get_device_settings()`
3. **Daily summaries** — `get_stats(date)` for each day in range
4. **Hydration** — `get_hydration_data(date)`
5. **Heart rate intraday** — `get_heart_rates(date)`
6. **Steps intraday** — `get_steps_data(date)`
7. **Stress intraday** — `get_all_day_stress(date)`
8. **Body battery intraday** — `get_body_battery_events(date)`
9. **Sleep data** — `get_sleep_data(date)`
10. **HRV data** — `get_hrv_data(date)`
11. **Respiration** — `get_respiration_data(date)`
12. **SpO2** — `get_spo2_data(date)`
13. **Body composition** — `get_body_composition(start, end)`
14. **Blood pressure** — `get_blood_pressure(start, end)`
15. **Activities** — `get_activities_by_date(start, end)`
    - For each new/updated activity:
      - Activity details: `get_activity(id)`
      - Activity sub-data (stored in `activity_details` row):
        - Splits: `get_activity_splits(id)`, `get_activity_typed_splits(id)`, `get_activity_split_summaries(id)`
        - HR zones: `get_activity_hr_in_timezones(id)`
        - Exercise sets: `get_activity_exercise_sets(id)`
        - Weather: `get_activity_weather(id)`
        - Gear: `get_activity_gear(id)`
      - Activity tracks: `get_activity_details(id)` (GPS track points → `activity_tracks`)
      - Activity file download: `download_activity(id, ORIGINAL)` → `activity_files`
16. **Training status** — `get_training_status(date)` → `training_statuses`
17. **Training readiness** — `get_training_readiness(date)` → `training_readiness`
18. **Race predictions** — `get_race_predictions(start, end)` → `race_predictions`
19. **Fitness age** — `get_fitnessage_data(date)` → `fitness_ages`
20. **Daily metrics** (consolidated into `daily_metrics` table):
    - VO2 max: `get_max_metrics(date)` with `metric_type = "vo2_max"`
    - Hill score: `get_hill_score(start, end)` with `metric_type = "hill_score"`
    - Endurance score: `get_endurance_score(start, end)` with `metric_type = "endurance_score"`
    - Lactate threshold: `get_lactate_threshold(start, end)` with `metric_type = "lactate_threshold"`
    - Intensity minutes: `get_intensity_minutes_data(date)` with `metric_type = "intensity_minutes"`
21. **Gear** — `get_gear()`
25. **Weight-ins** — `get_weigh_ins(start, end)`

## Date Range Logic

### Incremental run (default)

```
date_range = [today - INGEST_DAYS_BACK, today]
```

`INGEST_DAYS_BACK` defaults to 1. Configurable via env var. For intraday data that changes throughout the day (heart rate, steps, stress), always re-fetch today's data.

### Backfill run

```
date_range = [today - N days, today]
```

Where N is specified by `--days` CLI argument.

### Activity detection

Activities are fetched by date range. The pipeline tracks the last-seen activity timestamp per user. If an activity's Garmin ID is already in the database and hasn't changed (same `updated_at` from Garmin), skip the detail fetches.

## Upsert Strategy

All data uses `INSERT ... ON CONFLICT DO UPDATE`:

- **Unique keys** are defined per table (e.g., `(user_id, date)` for daily summaries, `(user_id, timestamp)` for intraday data, `garmin_activity_id` for activities).
- On conflict, update all non-key columns and set `updated_at = NOW()`.
- `raw_json` is always updated, even if parsed columns haven't changed (Garmin may add new fields we don't parse yet).

In SQLModel, this is achieved via:

```python
from sqlalchemy.dialects.postgresql import insert

stmt = insert(DailySummary).values(**data)
stmt = stmt.on_conflict_do_update(
    index_elements=["user_id", "date"],
    set_={col: stmt.excluded[col] for col in data if col not in ("id", "user_id", "date")},
)
session.exec(stmt)
```

## Activity File Handling

For each activity, download the original FIT file and store it:

1. Call `client.download_activity(activity_id, ActivityDownloadFormat.ORIGINAL)`
2. Store in `activity_files` table as `bytea`
3. Track format ("fit") and file size
4. Only download if not already stored (check by `activity_id + file_format`)

## Pipeline Error Handling

Each data type fetch is wrapped in try/except:

- **Success**: Upsert and continue
- **GarminConnectTooManyRequestsError**: Wait and retry (up to 3 times)
- **GarminConnectAuthenticationError**: Stop pipeline for this user, log critical error
- **Other exception**: Log error, skip this data type, continue with next

The pipeline does **not** abort on individual data type failures. It logs them and continues. This maximizes data capture even if one API endpoint is having issues.

## Ingestion Logging

Each run produces a structured log:

```json
{
  "run_id": "uuid",
  "user_id": "uuid",
  "started_at": "2026-05-23T06:00:00Z",
  "finished_at": "2026-05-23T06:02:34Z",
  "data_types": {
    "daily_summary": {"status": "success", "rows": 2},
    "heart_rate": {"status": "success", "rows": 2880},
    "sleep": {"status": "success", "rows": 1},
    "activities": {"status": "success", "rows": 0},
    "hrv": {"status": "no_data", "rows": 0},
    "body_composition": {"status": "error", "error": "GarminConnectConnectionError: 500"}
  }
}
```

This log is written to stdout (for journald/systemd capture) and optionally to a `ingest_runs` table for dashboard monitoring.

## Rate Limiting Across Users

When ingesting for multiple users, the pipeline processes users sequentially (not in parallel) to avoid hitting Garmin's per-account rate limits. Between users, a configurable delay (default: 5 seconds) is applied.

## CLI Commands

### `garmin-postgres ingest run`

Run an incremental ingestion for all active users.

Options:
- `--user DISPLAY_NAME` — Only ingest for a specific user
- `--days-back N` — Override INGEST_DAYS_BACK
- `--dry-run` — Fetch data but don't write to DB (for testing)

### `garmin-postgres ingest backfill`

Run a historical backfill.

Options:
- `--user DISPLAY_NAME` — Only backfill for a specific user
- `--days N` — Number of days to backfill (default: 365)
- `--start-date YYYY-MM-DD` — Explicit start date
- `--end-date YYYY-MM-DD` — Explicit end date (default: today)
- `--skip-activities` — Skip activity file downloads (faster)
- `--dry-run` — Fetch data but don't write to DB
