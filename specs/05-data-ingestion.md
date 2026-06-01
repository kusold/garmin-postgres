# Data Ingestion

## Overview

The ingestion pipeline fetches data from Garmin Connect and upserts it into PostgreSQL. It runs on a schedule (systemd timer, twice daily) and is also invocable manually via CLI.

## Scheduling

- **Mechanism**: Systemd user timer
- **Default schedule**: Twice daily (e.g., 06:00 and 18:00)
- **Token refresh**: Tokens are refreshed on each run by garth. Valid for ~1 year.
- **Backfill**: `garmin-postgres ingest backfill --days 365` fetches historical data.

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

## Upsert Strategy

All data uses `INSERT ... ON CONFLICT DO UPDATE`:

- **Unique keys** are defined per table (e.g., `(user_id, date)` for daily summaries, `(user_id, timestamp)` for intraday data).
- On conflict, update all non-key columns and set `updated_at = NOW()`.
- `raw_json` is always updated, even if parsed columns haven't changed (Garmin may add new fields we don't parse yet).

In SQLModel, this is achieved via:

```python
from sqlalchemy.dialects.postgresql import insert

stmt = insert(Model).values(**data)
stmt = stmt.on_conflict_do_update(
    index_elements=["user_id", "date"],
    set_={col: stmt.excluded[col] for col in data if col not in ("id", "user_id", "date")},
)
session.exec(stmt)
```

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

This log is written to stdout (for journald/systemd capture) and optionally to an `ingest_runs` table for dashboard monitoring.

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
- `--dry-run` — Fetch data but don't write to DB
