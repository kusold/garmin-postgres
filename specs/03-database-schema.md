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
- The `users.timezone` column stores the user's IANA timezone for Grafana display purposes. It is **not** authoritative — Garmin controls the date bucketing.

## Tables

### `users`

Garmin Connect user accounts. One row per authenticated Garmin user.

| Column | Type | Notes |
|---|---|---|
| id | BIGINT (PK) | |
| garmin_display_name | TEXT (UNIQUE) | From Garmin profile |
| timezone | TEXT | IANA timezone (e.g. "America/Chicago") |
| tokens_json | JSONB | Serialized garth OAuth tokens |
| is_active | BOOLEAN | Set to false to pause ingestion |
| last_ingest_at | TIMESTAMPTZ | Timestamp of last successful ingestion |
| raw_json | JSONB | Full user profile response |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

## Indexes

Unique constraints already provide indexes for the identity columns. GIN indexes on `raw_json` columns should be added lazily when Grafana query patterns require JSON path filtering. Not created by default.

## Additional Tables

Additional tables will be designed as ingestion is built out for each data type. Each table will follow the thin schema pattern: identity columns + `raw_json` JSONB, with typed columns added only when needed.
