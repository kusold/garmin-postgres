# Database Schema

## Design Philosophy

Tables are kept **thin** — identity columns, timestamps, and a `raw_json` JSONB column holding the complete Garmin API response. Typed columns are added incrementally via Alembic migrations only when a specific Grafana query or feature needs them.

This avoids premature schema design and lets the schema evolve naturally from actual usage.

## Naming Conventions

All tables use a consistent naming convention enforced via SQLAlchemy's `metadata.naming_convention`:

```python
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

## Shared Base Model

All table models inherit from a base that provides:

| Column | Type | Description |
|---|---|---|
| `id` | BIGINT (PK) | Auto-generated, BIGSERIAL |
| `created_at` | TIMESTAMPTZ | Row creation time (UTC) |
| `updated_at` | TIMESTAMPTZ | Last update time (UTC) |

All tables use BIGINT auto-incrementing primary keys. Smallest index footprint (8 bytes), fastest joins, human-readable IDs.

## Promoting Fields to Typed Columns

When a Grafana panel or query needs efficient filtering/sorting on a field, add it via migration:

```bash
alembic revision --autogenerate -m "add resting_heart_rate to daily_summaries"
```

The migration extracts the field from `raw_json`:

```python
# Example migration: promote a field
op.add_column('daily_summaries', sa.Column('resting_heart_rate', sa.Integer(), nullable=True))

# Backfill from raw_json
op.execute("""
    UPDATE daily_summaries
    SET resting_heart_rate = (raw_json->>'restingHeartRate')::integer
    WHERE raw_json->>'restingHeartRate' IS NOT NULL
""")
```

Rule of thumb: **don't promote until you have a concrete query that needs it.**

## Timezone Handling

- All TIMESTAMPTZ columns store UTC. No exceptions.
- `calendar_date` columns on daily-scoped tables reflect Garmin's server-side bucketing — it's the user's Garmin Connect profile timezone, not necessarily where they physically are.
- Intraday timestamps are parsed from Garmin's UTC epoch milliseconds.
- Activities include `timeZoneUnitDTO.unitKey` (IANA timezone) per activity — stored in `raw_json`.
- The `users.timezone` column stores the user's IANA timezone (inferred from their most recent activity) for Grafana display purposes. It is **not** authoritative — Garmin controls the date bucketing.

## Tables

### `users`

Garmin Connect user accounts. One row per authenticated Garmin user.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| garmin_display_name | TEXT (UNIQUE) | From Garmin profile |
| timezone | TEXT | IANA timezone (e.g. "America/Chicago"), inferred from activities |
| tokens_json | JSONB | Serialized garth OAuth tokens |
| is_active | BOOLEAN | Set to false to pause ingestion |
| is_active | BOOLEAN | Set to false to pause ingestion |
| last_ingest_at | TIMESTAMPTZ | Timestamp of last successful ingestion |
| raw_json | JSONB | Full user profile response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `devices`

Garmin devices associated with a user.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| garmin_device_id | TEXT | Device serial/ID from Garmin |
| raw_json | JSONB | Full device response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, garmin_device_id)`

### `daily_summaries`

One row per user per day. Aggregated daily health metrics from `get_stats()`.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | Calendar date |
| raw_json | JSONB | Full get_stats response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `heart_rate_intraday`

Per-minute or per-15-second heart rate readings.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Measurement timestamp (UTC) |
| raw_json | JSONB | Single HR data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `steps_intraday`

Per-15-minute step counts.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Interval start (UTC) |
| raw_json | JSONB | Single step interval data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `stress_intraday`

Intraday stress readings (~every 15 minutes).

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Measurement timestamp (UTC) |
| raw_json | JSONB | Single stress data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `body_battery_intraday`

Intraday body battery events.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Measurement timestamp (UTC) |
| raw_json | JSONB | Single body battery data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `sleep_summaries`

One row per sleep session.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | Date of the sleep session |
| raw_json | JSONB | Full sleep summary response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `sleep_intraday`

Granular sleep stage and measurement data.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| sleep_summary_id | BIGINT (FK → sleep_summaries) | |
| timestamp | TIMESTAMPTZ | |
| raw_json | JSONB | Single sleep intraday data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `hrv_summaries`

Heart Rate Variability data.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| raw_json | JSONB | Full HRV response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `respiration_intraday`

Intraday breathing rate.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | |
| raw_json | JSONB | Single respiration data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `spo2_intraday`

Intraday blood oxygen readings.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | |
| raw_json | JSONB | Single SpO2 data point |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `activities`

Individual recorded activities.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| garmin_activity_id | BIGINT (UNIQUE) | Garmin's activity ID |
| start_time | TIMESTAMPTZ | Activity start time |
| raw_json | JSONB | Full activity response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `activity_details`

Detailed activity data (splits, laps, HR zones, exercise sets, weather, gear). One row per activity with each sub-type's data in its own JSONB column.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| activity_id | BIGINT (FK → activities, UNIQUE) | |
| splits_json | JSONB | Response from get_activity_splits |
| typed_splits_json | JSONB | Response from get_activity_typed_splits |
| split_summaries_json | JSONB | Response from get_activity_split_summaries |
| hr_timezones_json | JSONB | Response from get_activity_hr_in_timezones |
| exercise_sets_json | JSONB | Response from get_activity_exercise_sets |
| weather_json | JSONB | Response from get_activity_weather |
| gear_json | JSONB | Response from get_activity_gear |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `activity_tracks`

GPS track points and per-second metrics during activities.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| activity_id | BIGINT (FK → activities) | |
| timestamp | TIMESTAMPTZ | |
| raw_json | JSONB | Single track point data |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(activity_id, timestamp)`

### `activity_files`

Original downloaded activity files stored in Postgres.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| activity_id | BIGINT (FK → activities) | |
| file_format | TEXT | "fit", "tcx", "gpx", "kml", "csv" |
| file_data | BYTEA | Binary content |
| created_at | TIMESTAMPTZ | |

Unique constraint: `(activity_id, file_format)`

### `body_compositions`

Weight and body composition measurements.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Measurement time |
| raw_json | JSONB | Full body composition response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `blood_pressures`

Blood pressure readings.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| timestamp | TIMESTAMPTZ | Measurement time |
| raw_json | JSONB | Full blood pressure response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, timestamp)`

### `training_statuses`

Daily training status assessment.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| raw_json | JSONB | Full training status response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `training_readiness`

Training readiness scores.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| raw_json | JSONB | Full training readiness response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `race_predictions`

Daily race time predictions.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| raw_json | JSONB | Full race predictions response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `fitness_ages`

Daily fitness age estimates.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| raw_json | JSONB | Full fitness age response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date)`

### `daily_metrics`

Catch-all for daily-scoped metrics that don't warrant their own table: VO2 max, hill scores, endurance scores, lactate thresholds, intensity minutes.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| calendar_date | DATE | |
| metric_type | TEXT | "vo2_max", "hill_score", "endurance_score", "lactate_threshold", "intensity_minutes" |
| raw_json | JSONB | Full API response for this metric type |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, calendar_date, metric_type)`

### `gear`

User's gear/equipment.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| user_id | BIGINT (FK → users) | |
| garmin_gear_uuid | TEXT | Garmin's gear ID |
| raw_json | JSONB | Full gear response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(user_id, garmin_gear_uuid)`

## Consolidation Notes

Several previously-separate tables are merged into `daily_metrics` since they share the same structure (user + date + JSON) and low query frequency:

- ~~`vo2_max`~~ → `daily_metrics` with `metric_type = "vo2_max"`
- ~~`hill_scores`~~ → `daily_metrics` with `metric_type = "hill_score"`
- ~~`endurance_scores`~~ → `daily_metrics` with `metric_type = "endurance_score"`
- ~~`lactate_thresholds`~~ → `daily_metrics` with `metric_type = "lactate_threshold"`
- ~~`intensity_minutes`~~ → `daily_metrics` with `metric_type = "intensity_minutes"`

Activity sub-data (splits, laps, HR zones, exercise sets, weather, gear) is consolidated into `activity_details` with separate JSONB columns per sub-type. This avoids 6 extra tables for data that's always fetched together and rarely queried independently.

## Indexes

Unique constraints already provide indexes for the identity columns. Additional indexes:

- `ix_daily_summaries_user_id_calendar_date` on `(user_id, calendar_date)`
- `ix_activities_user_id_start_time` on `(user_id, start_time)`
- `ix_activity_tracks_activity_id_timestamp` on `(activity_id, timestamp)`
- `ix_daily_metrics_user_id_calendar_date_metric_type` on `(user_id, calendar_date, metric_type)`

GIN indexes on `raw_json` columns should be added lazily when Grafana query patterns require JSON path filtering. Not created by default.

## Table Count Summary

| # | Table | Rows/year (est. single user) |
|---|---|---|
| 1 | users | 1 |
| 2 | devices | 1–5 |
| 3 | daily_summaries | 365 |
| 4 | heart_rate_intraday | ~525K (per-minute) |
| 5 | steps_intraday | ~35K (per-15-min) |
| 6 | stress_intraday | ~35K (per-15-min) |
| 7 | body_battery_intraday | ~35K (per-15-min) |
| 8 | sleep_summaries | 365 |
| 9 | sleep_intraday | ~2K/night |
| 10 | hrv_summaries | 365 |
| 11 | respiration_intraday | ~35K (per-15-min) |
| 12 | spo2_intraday | ~35K (per-15-min) |
| 13 | activities | 100–500 |
| 14 | activity_details | 100–500 |
| 15 | activity_tracks | 100K–500K |
| 16 | activity_files | 100–500 |
| 17 | body_compositions | 12–365 |
| 18 | blood_pressures | 0–365 |
| 19 | training_statuses | 365 |
| 20 | training_readiness | 365 |
| 21 | race_predictions | 365 |
| 22 | fitness_ages | 365 |
| 23 | daily_metrics | ~1,825 (5 types × 365) |
| 24 | gear | 1–10 |
| **Total** | **24 tables** | **~1.3M rows/year** |
