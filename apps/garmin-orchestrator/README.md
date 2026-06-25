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

The `Deploy Prefect` workflow runs on GitHub-hosted Ubuntu runners, joins the
tailnet with `tailscale/github-action`, installs the uv workspace, and registers
all deployments from `prefect.yaml` to the existing `garmin` work pool. It
deploys on pushes to `main` that affect the orchestrator, Garmin sync package,
shared core package, or deployment metadata. The `garmin` worker and work pool
are managed outside this workflow.

Required GitHub secrets:

- `TS_OAUTH_CLIENT_ID` - Tailscale workload identity federation client ID
- `TS_AUDIENCE` - Tailscale workload identity federation audience

Recommended Tailscale access:

- Tag GitHub-hosted deploy runners as `tag:github-actions-prefect-deploy`
- Tag the Prefect server as `tag:prefect-server`
- Allow only the Prefect API port from the deploy runner tag to the Prefect tag

For the default `http://prefect.svc.rockymtn.org/api` target, the API is on
TCP port `80`. If the target moves to HTTPS or direct Prefect, use only the
actual port in use, such as `443` or `4200`.

```json
{
  "tagOwners": {
    "tag:github-actions-prefect-deploy": [],
    "tag:prefect-server": ["autogroup:admin"]
  },
  "acls": [
    {
      "action": "accept",
      "src": ["tag:github-actions-prefect-deploy"],
      "proto": "tcp",
      "dst": ["tag:prefect-server:80"]
    }
  ],
  "tests": [
    {
      "src": "tag:github-actions-prefect-deploy",
      "proto": "tcp",
      "accept": ["tag:prefect-server:80"],
      "deny": [
        "tag:prefect-server:22",
        "tag:prefect-server:4200",
        "tag:prefect-server:5432"
      ]
    }
  ]
}
```

By default the workflow deploys to:

```bash
http://prefect.svc.rockymtn.org/api
```

Set a repository variable named `PREFECT_API_URL` to override that target.

Once Prefect deployments are active, Prefect should own scheduling. Keep
systemd for supervising the local Prefect worker instead of also running the
old direct `garmin-sync ingest run` timer.
