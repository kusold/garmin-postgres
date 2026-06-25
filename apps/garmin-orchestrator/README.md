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
podman compose -f compose.yaml -f compose.prefect.yaml up -d
export PREFECT_API_URL=http://127.0.0.1:4200/api
uv run garmin-orchestrator deploy
uv run prefect worker start --pool garmin
```

GitHub deployment setup:

The `Deploy Prefect` workflow runs on the `self-hosted` runner label and uses
the repo's Nix shell to install the uv workspace before registering all
deployments from `prefect.yaml` to the existing `garmin` work pool. It deploys
on pushes to `main` that affect the orchestrator, Garmin sync package, shared
core package, or deployment metadata. The `garmin` worker and work pool are
managed outside this workflow.

By default the workflow deploys to:

```bash
http://prefect.svc.rockymtn.org/api
```

Set a repository variable named `PREFECT_API_URL` to override that target.

Once Prefect deployments are active, Prefect should own scheduling. Keep
systemd for supervising the local Prefect worker instead of also running the
old direct `garmin-sync ingest run` timer.
