# Project Overview: garmin-postgres

## Purpose

Archive every possible bit of health and fitness data from Garmin Connect into PostgreSQL, on an ongoing basis.

## Goals

1. **Complete data archival** — Ingest all available Garmin Connect data via python-garminconnect and store it durably in Postgres.
2. **Grafana dashboards** — After ingestion is solid, build Grafana dashboards (similar to garmin-grafana) backed by Postgres instead of InfluxDB.
3. **Future FastAPI layer** — Defer for now, but design the architecture to make adding a REST API straightforward.

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.13 |
| Package manager | uv |
| ORM | SQLModel |
| Migrations | Alembic |
| Database | PostgreSQL 17 |
| Garmin client | python-garminconnect (via garth for OAuth) |
| Testing | pytest + testcontainers (real Postgres) |
| Container runtime | Podman + Podman Compose |
| Scheduling | Systemd timers (user units) |
| Future API | FastAPI (deferred) |
| Future dashboards | Grafana |

## Non-Goals (for now)

- FastAPI server
- Grafana dashboards
- Mobile or web UI
- Writing data back to Garmin
- Real-time data streaming
- Multi-tenancy beyond authenticated Garmin users

## Key Design Decisions

- **Multi-user from day one** — Schema includes user identity, even though initial usage is single-user.
- **Raw JSON preservation** — Every ingested record stores the original Garmin API response in a `raw_json` JSONB column alongside typed columns. This enables re-parsing if the schema evolves.
- **Activity file storage in Postgres** — Original FIT/GPX/TCX files stored as `bytea` in Postgres, not on disk.
- **Tokens in database** — OAuth tokens stored in `users.tokens_json` JSONB column, not on the filesystem. Single source of truth.
- **Idempotent ingestion** — All ingestion uses upserts (`INSERT ... ON CONFLICT DO UPDATE`). The cron can be re-run safely without duplicating data.
- **Systemd timers for scheduling** — Run twice daily via systemd user timer. The garth OAuth tokens are valid for ~1 year, but we refresh them on each run.
- **CLI for auth setup** — Interactive CLI command for initial OAuth flow (email/password + MFA in terminal).
