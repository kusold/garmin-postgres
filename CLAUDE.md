# CLAUDE.md — garmin-postgres

## Project Summary

Archives available Garmin Connect health and fitness data into PostgreSQL and syncs archived data to Notion.

## Tech Stack

- Python 3.13, managed by **uv**
- **SQLModel** ORM + **Alembic** migrations
- **PostgreSQL 17** via Podman Compose
- **python-garminconnect** (uses **garth** for OAuth)
- **notion-client** for Notion export
- **pytest** + **testcontainers** for integration testing against real Postgres
- **typer** for CLI
- Future: FastAPI (deferred), Grafana dashboards (deferred)

## Commands

```bash
# Install dependencies
uv sync --all-packages --group dev

# Run tests (requires podman for testcontainers)
uv run pytest

# Run ingestion
uv run garmin-sync ingest run

# Sync already-ingested data to Notion
uv run notion-sync run --user <garmin-display-name>

# Run migrations
uv run alembic upgrade head

# Start dev database
podman compose up -d
```

## Project Structure

```
packages/garmin-postgres-core/
└── src/garmin_postgres/ # Shared config, DB, models, migrations
apps/garmin-sync/
└── src/garmin_sync/     # Garmin auth, ingest, and CLI
apps/notion-sync/
└── src/notion_sync/     # PostgreSQL to Notion sync CLI
tests/                   # pytest + real Postgres via testcontainers
specs/                   # Design documents (read before implementation)
```

## Key Design Decisions

- **Thin schema, JSONB-first** — Tables store identity columns + `raw_json` JSONB. Typed columns added via migrations only when Grafana needs them.
- **Multi-user from day one** — `users` table with tokens stored in DB (`tokens_json` JSONB)
- **Activity files in Postgres** — Original FIT files stored as `bytea`
- **Idempotent upserts** — All ingestion uses `ON CONFLICT DO UPDATE`
- **Sync engine for ingestion** — Async engine added later for FastAPI
- **Systemd timers** — Run twice daily, not a long-running daemon
- **Parsers are pure functions** — Easy to test without DB or API access
- **Notion sync is Postgres-only** — It must not call Garmin or add new ingests
- **Notion sync is single-user** — `notion-sync run` requires `--user`

## Specs

See `specs/` directory for detailed design documents:
1. `01-project-overview.md` — Goals and tech stack
2. `02-architecture.md` — Project structure and data flow
3. `03-database-schema.md` — All tables and columns
4. `04-authentication.md` — OAuth flow and token management
5. `05-data-ingestion.md` — Pipeline logic and scheduling
6. `06-testing.md` — Testing strategy
7. `07-future-fastapi.md` — Deferred FastAPI considerations
8. `08-grafana-dashboard.md` — Deferred Grafana dashboard vision

## Task Tracking

This project uses **Vikunja** (via the `vja` CLI) for task management. Do **not** use beads for task tracking — always use Vikunja skills instead.

## Conventions

- All timestamps stored as TIMESTAMPTZ (UTC)
- BIGINT auto-increment primary keys on all tables
- Alembic `env.py` must import all models and set `target_metadata = SQLModel.metadata`
- `script.py.mako` must include `import sqlmodel.sql.sqltypes`
- Alembic `context.configure()` must set `compare_type=True`
- Naming convention on SQLModel.metadata for predictable constraint names
