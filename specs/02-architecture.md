# Architecture

## Project Structure

```
garmin-postgres/
├── pyproject.toml
├── CLAUDE.md
├── specs/
├── compose.yaml                    # Podman Compose (Postgres + future Grafana)
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── src/
│   └── garmin_postgres/
│       ├── __init__.py
│       ├── cli.py                  # CLI entry point (auth setup, manual ingest)
│       ├── config.py               # Pydantic Settings (env vars, DB URL)
│       ├── db.py                   # Engine, session factory, init
│       ├── auth.py                 # OAuth login flow, token management
│       ├── models/                 # SQLModel table definitions
│       │   ├── __init__.py         # Re-exports all models
│       │   ├── base.py             # Shared mixins, naming convention
│       │   └── user.py             # User model
│       ├── ingest/                 # Ingestion logic
│       │   ├── __init__.py
│       │   ├── client.py           # Garmin client wrapper (auth + API calls)
│       │   ├── pipeline.py         # Orchestration: fetch → parse → upsert
│       │   └── parsers/            # Per-data-type parsing from raw JSON → models
│       │       └── ...
├── tests/
│   ├── conftest.py                 # Shared fixtures (Postgres container, session)
│   ├── factories.py                # Test data factories
│   ├── models/                     # Model-level tests
│   ├── ingest/                     # Ingestion pipeline tests
│   └── parsers/                    # Parser unit tests (with fixture JSON files)
│       └── fixtures/               # Sample Garmin API JSON responses
├── systemd/
│   ├── garmin-postgres-ingest.service
│   └── garmin-postgres-ingest.timer
└── .env.example
```

## Module Responsibilities

### `cli.py`
Entry point for all CLI commands. Uses `typer`. Commands:
- `garmin-postgres auth login` — Interactive OAuth flow (email/password + MFA)
- `garmin-postgres auth status` — Show current auth status
- `garmin-postgres ingest run` — Run a single ingestion pass
- `garmin-postgres ingest backfill` — Backfill historical data
- `garmin-postgres db upgrade` — Run Alembic migrations
- `garmin-postgres db downgrade` — Rollback migrations

### `config.py`
Pydantic `BaseSettings` class. All config via environment variables or `.env` file:
- `DATABASE_URL` — Postgres connection string
- `LOG_LEVEL` — Logging verbosity
- `INGEST_DAYS_BACK` — How many days back to fetch on each run (default: 1)

### `db.py`
- Creates the SQLModel engine (sync for now; async later for FastAPI)
- Provides a session context manager
- Runs startup init (Alembic migrations via `alembic.command.upgrade`)

### `models/`
Each file defines one or more SQLModel table models. All models inherit from a shared base that provides:
- `id` (BIGINT auto-increment primary key)
- `created_at` / `updated_at` timestamps

Tables are **thin** — identity columns (user_id, timestamp/date, Garmin IDs) plus `raw_json` JSONB. Typed columns are added via Alembic migrations only when Grafana or a specific feature needs them. This avoids premature schema design.

### `ingest/client.py`
Wraps `garminconnect.Garmin`. Handles:
- Loading tokens from disk
- Refreshing expired tokens
- Retries with backoff on 429s
- Logging all API calls

### `ingest/pipeline.py`
Orchestration layer. For each data type:
1. Determine the date range to fetch
2. Call the Garmin API via `client.py`
3. Parse the response via the appropriate parser
4. Upsert into Postgres via SQLModel

### `ingest/parsers/`
Each parser is a pure function: takes raw Garmin JSON, returns a list of SQLModel instances. No DB access, no API calls. Easy to unit test with fixture JSON files.

## Data Flow

```
Systemd Timer
    │
    ▼
CLI (cli.py ingest run)
    │
    ▼
Pipeline (pipeline.py)
    │
    ├──► Client (client.py) ──► Garmin Connect API
    │                                    │
    │                              raw JSON response
    │                                    │
    ├──► Parser (parsers/*.py) ◄─────────┘
    │         │
    │    SQLModel instances
    │         │
    └──► DB Session (db.py) ──► PostgreSQL
```

## Database Connection Strategy

- **Sync engine for ingestion** — The cron job doesn't need async. Sync is simpler and matches Alembic's requirements.
- **Async engine ready** — The config and engine setup will support switching to async when FastAPI is added. Models don't change.
- **Connection pooling** — Use SQLModel's default pool with configurable pool size.

## Podman Compose

```yaml
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_DB: garmin
      POSTGRES_USER: garmin
      POSTGRES_PASSWORD: garmin
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

Grafana will be added to compose.yaml later when dashboard work begins.
