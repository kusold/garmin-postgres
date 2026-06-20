# Garmin Orchestrator

Prefect orchestration for the Garmin archive. This app wraps the existing
`garmin-sync` object runners without moving SQLModel sessions, Garmin clients,
or ORM objects across task boundaries.

Local flow runs:

```bash
uv run garmin-orchestrator run archive --dry-run
uv run garmin-orchestrator run daily-summary --days-back 2
uv run garmin-orchestrator run activities --days-back 7
uv run garmin-orchestrator run personal-records
```

Local deployment setup:

```bash
uv run prefect work-pool create --type process garmin-local-process
uv run garmin-orchestrator deploy
uv run prefect worker start --pool garmin-local-process
```

Once Prefect deployments are active, Prefect should own scheduling. Keep
systemd for supervising the Prefect server and worker instead of also running
the old direct `garmin-sync ingest run` timer.
