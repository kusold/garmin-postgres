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

Configure Notion in `.env` or exported environment variables:

```bash
export NOTION_TOKEN=secret_...
export NOTION_ACTIVITIES_DB_ID=...
export NOTION_DAILY_STEPS_DB_ID=...
export NOTION_PERSONAL_RECORDS_DB_ID=...
```

Run all configured syncs:

```bash
uv run notion-sync run --user your-garmin-display-name
```

Run one data type:

```bash
uv run notion-sync run --user your-garmin-display-name --data-type personal_records
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
