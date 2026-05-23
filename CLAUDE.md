# CLAUDE.md — garmin-postgres

## Project Summary

Archives all available Garmin Connect health and fitness data into PostgreSQL on an ongoing basis.

## Tech Stack

- Python 3.13, managed by **uv**
- **SQLModel** ORM + **Alembic** migrations
- **PostgreSQL 17** via Podman Compose
- **python-garminconnect** (uses **garth** for OAuth)
- **pytest** + **testcontainers** for integration testing against real Postgres
- **typer** for CLI
- Future: FastAPI (deferred), Grafana dashboards (deferred)

## Commands

```bash
# Install dependencies
uv sync

# Run tests (requires podman for testcontainers)
uv run pytest

# Run ingestion
uv run garmin-postgres ingest run

# Run migrations
uv run alembic upgrade head

# Start dev database
podman compose up -d
```

## Project Structure

```
src/garmin_postgres/     # Main package
├── cli.py               # CLI entry point (typer)
├── config.py            # Pydantic Settings
├── db.py                # Engine + session factory
├── models/              # SQLModel table definitions
├── ingest/              # Fetch → parse → upsert pipeline
│   ├── client.py        # Garmin client wrapper
│   ├── pipeline.py      # Orchestration
│   └── parsers/         # Pure functions: JSON → SQLModel instances
alembic/                 # Migrations
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

## Conventions

- All timestamps stored as TIMESTAMPTZ (UTC)
- BIGINT auto-increment primary keys on all tables
- Alembic `env.py` must import all models and set `target_metadata = SQLModel.metadata`
- `script.py.mako` must include `import sqlmodel.sql.sqltypes`
- Alembic `context.configure()` must set `compare_type=True`
- Naming convention on SQLModel.metadata for predictable constraint names
