# garmin-postgres

Archives available Garmin Connect health and fitness data into PostgreSQL and syncs archived data to Notion.

This repo is a uv workspace with two CLI apps:

- `garmin-sync`: Garmin Connect to PostgreSQL ingestion.
- `notion-sync`: PostgreSQL to Notion export for data already ingested locally.

## Setup

```bash
# Install dependencies (requires uv)
uv sync --all-packages --group dev

# Copy and edit environment config
cp .env.sample .env

# Start the database
podman compose up -d

# Run migrations
uv run alembic upgrade head
```

## Authentication

Login interactively with your Garmin Connect credentials. OAuth tokens are stored in the database — no password is persisted.

```bash
uv run garmin-sync auth login
```

You'll be prompted for your email and password (hidden input). If your account has MFA enabled, you'll also be asked for a verification code.

To skip the email prompt:

```bash
uv run garmin-sync auth login --email you@example.com
```

Check authentication status for all stored users:

```bash
uv run garmin-sync auth status
```

The legacy `garmin-postgres` command remains available as an alias for `garmin-sync`.

## Notion Sync

`notion-sync` reads from PostgreSQL only. It does not fetch new Garmin data.

Supported data types:

- `activities`
- `daily_steps`
- `personal_records`

Notion is configured per user in the database — there are no `NOTION_*`
environment variables. Each `sync_targets` row holds one user's integration
token and Notion database IDs. Seed a user (idempotent):

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

Each Notion database must be shared with the integration and already contain
the expected properties — see "Notion-Side Prerequisites" in
`specs/10-per-user-notion-sync.md` for the checklist.

Run all configured syncs:

```bash
uv run notion-sync run --user your-garmin-display-name
```

Run one data type:

```bash
uv run notion-sync run --user your-garmin-display-name --data-type personal_records
```

Run the Prefect flow locally:

```bash
uv run garmin-orchestrator run notion-sync --user your-garmin-display-name
```

The `notion-sync` Prefect deployment runs daily at 07:00
`America/Denver`, after the 06:00 Garmin archive. It syncs the last two
completed days of activities and daily steps, and refreshes all personal
records so the latest record for each Garmin `typeId` wins. An unpinned run
syncs every active Garmin user that has a `notion` target; the scheduled
deployment pins a single `user` so the daily run stays deterministic.

For the first sync, provide the earliest archived date to backfill existing
activities and daily steps:

```bash
uv run garmin-orchestrator run notion-sync \
  --user your-garmin-display-name \
  --start-date 2020-01-01
```

## Running Tests

```bash
# Unit tests only (no database required)
uv run pytest -k "not session"

# All tests (requires Podman for testcontainers)
uv run pytest
```

## CLI Commands

```bash
uv run garmin-sync --help
uv run garmin-sync auth --help
uv run notion-sync --help
```
