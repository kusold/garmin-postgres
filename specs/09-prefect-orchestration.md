# Prefect Orchestration

## Goal

Integrate Prefect directly at the per-ingest-object level, skipping the
coarse "one Garmin sync task" bootstrap. The target design should make each
ingestable Garmin object visible and retryable in Prefect while preserving a
simple CLI for manual runs.

The first supported ingest objects are:

- `daily-summary`
- `activities`
- `personal-records`

The CLI should expose those objects directly:

```bash
uv run garmin-sync ingest daily-summary
uv run garmin-sync ingest activities
uv run garmin-sync ingest personal-records
uv run garmin-sync ingest run
uv run garmin-sync ingest backfill
```

`ingest run` remains as the compatibility umbrella command. It should call the
same object runners as the object-specific commands.

## Why Object-Level Prefect Tasks

Object-level tasks are a better fit than one coarse Garmin sync task because
Garmin failures are usually isolated:

- one date's daily summary can fail while other dates succeed
- one activity detail or file download can fail while other activities succeed
- personal records can fail independently of activity ingestion

Prefect only gives useful retry and observability at task boundaries. If the
entire Garmin sync is one task, a retry repeats all selected work. If each
object has its own task boundary, Prefect can show which object failed and can
retry only that object.

The design still needs to avoid excessive task fragmentation. "Object-level" in
this project means the stable data families the product understands, with finer
child tasks only where failures commonly happen:

| CLI object | Internal data type | Prefect task boundary | Finer child tasks |
| --- | --- | --- | --- |
| `daily-summary` | `daily_summary` | user plus date | one task per date |
| `activities` | `activities` | user plus activity range | one task to list activity IDs, one task per activity |
| `personal-records` | `personal_records` | user snapshot | none initially |

This gives meaningful resiliency without turning every database upsert into a
separate workflow task.

## Key Constraints

Do not pass these objects across Prefect task boundaries:

- SQLAlchemy or SQLModel sessions
- Garmin client instances
- Notion client instances
- ORM model instances

Prefect task parameters and return values should be serializable primitives:
IDs, dates, strings, booleans, lists, and dictionaries.

Each task should open its own database session, load the user by ID, construct
its own Garmin client from stored tokens, and commit its own successful work.
This makes retries safe and avoids stale connection/client state.

## Recommended Package Layout

Keep Garmin ingestion logic in `apps/garmin-sync`. Add Prefect in a thin
orchestration app so the normal manual CLI does not require importing Prefect
unless the orchestrator is used.

```text
apps/garmin-sync/src/garmin_sync/
  cli.py
  ingest/
    client.py
    date_windows.py
    object_registry.py
    pipeline.py
    results.py
    runners.py

apps/garmin-orchestrator/
  pyproject.toml
  src/garmin_orchestrator/
    cli.py
    flows.py
    tasks.py
    deployments.py

prefect.yaml
```

Responsibilities:

- `garmin_sync.ingest.runners`: pure application-service functions for each
  ingest object. These functions own DB sessions, Garmin client setup, upserts,
  token persistence, and result summaries.
- `garmin_sync.ingest.results`: typed result objects such as `IngestResult` and
  `IngestSummary`, with helpers for `success`, `partial`, and `error`.
- `garmin_sync.ingest.date_windows`: shared parsing and default date range
  logic for CLI and flows.
- `garmin_sync.ingest.object_registry`: supported object names and aliases,
  for example `daily-summary`, `daily_summary`, `daily`, `activities`,
  `personal-records`, and `personal_records`.
- `garmin_sync.ingest.pipeline`: compatibility wrapper around the new runners.
  Existing callers of `run_for_all_users` and `run_ingestion` can keep working
  while the internals move to object runners.
- `garmin_orchestrator.tasks`: Prefect `@task` wrappers around the runner
  functions.
- `garmin_orchestrator.flows`: Prefect `@flow` definitions and task ordering.
- `garmin_orchestrator.deployments`: deployment helpers if we choose Python
  deployment creation in addition to `prefect.yaml`.

Alternative: put `flows.py` and `tasks.py` directly inside `apps/garmin-sync`.
That is simpler, but it makes Prefect part of the Garmin CLI app dependency
surface. The separate app is cleaner if manual ingestion should stay lightweight.

## Runner API

Add runner functions that can be called by both CLI commands and Prefect tasks.

```python
def ingest_daily_summary_day(
    *,
    user_id: int,
    calendar_date: date,
    dry_run: bool = False,
) -> IngestResult:
    ...


def list_activity_ids(
    *,
    user_id: int,
    start_date: date,
    end_date: date,
) -> list[int]:
    ...


def ingest_activity(
    *,
    user_id: int,
    activity_id: int,
    dry_run: bool = False,
    include_details: bool = True,
    include_files: bool = True,
) -> IngestResult:
    ...


def ingest_personal_records(
    *,
    user_id: int,
    dry_run: bool = False,
) -> IngestResult:
    ...
```

Add aggregate helpers for CLI compatibility:

```python
def ingest_daily_summary_range(...): ...
def ingest_activities_range(...): ...
def ingest_selected_objects(...): ...
```

The aggregate helpers should be thin loops over the same object-level runners.
They are useful for the local CLI. Prefect flows should call the lower-level
runners through tasks so Prefect sees the individual task outcomes.

## Prefect Flow Shape

The main flow should orchestrate object tasks without hiding failures inside one
large task.

```python
@flow(name="garmin-archive")
def garmin_archive_flow(
    *,
    user: str | None = None,
    data_types: list[str] | None = None,
    days_back: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = False,
    fail_on_partial: bool = False,
) -> dict:
    ...
```

Flow steps:

1. Ensure the database is reachable and migrations are applied.
2. Resolve the date window once.
3. Resolve active users to serializable user references: `{"id": 1,
   "display_name": "..."}`.
4. For each user, run selected object tasks.
5. Collect results into one summary artifact or structured log.
6. Fail the flow if any object has `error`, or if `fail_on_partial=True` and any
   object has `partial`.

Use a subflow per user if the UI becomes too noisy:

```python
@flow(name="garmin-archive-user")
def garmin_archive_user_flow(user_ref: dict, ...):
    ...
```

This keeps the top-level flow readable while preserving task-level visibility.

## Task Details

### Daily Summary

Direct phase-2 task granularity:

- one task per user per date
- retries on transient Garmin/API failures
- each task upserts at most one `daily_summaries` row

Example:

```python
@task(retries=3, retry_delay_seconds=[60, 300, 900], tags=["garmin-api"])
def ingest_daily_summary_day_task(user_id: int, calendar_date: date, dry_run: bool):
    return ingest_daily_summary_day(
        user_id=user_id,
        calendar_date=calendar_date,
        dry_run=dry_run,
    ).as_dict()
```

Why this granularity: daily summaries are naturally date keyed. If Garmin fails
for one day in a backfill, only that day should retry.

### Activities

Direct phase-2 task granularity:

- one task to list activity IDs for a user and date range
- one task per activity ID to fetch detail, upsert activity, fetch chart/polyline
  detail, and optionally download the original file

Example:

```python
@task(retries=3, retry_delay_seconds=[60, 300, 900], tags=["garmin-api"])
def list_activity_ids_task(user_id: int, start_date: date, end_date: date):
    return list_activity_ids(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
    )


@task(retries=3, retry_delay_seconds=[60, 300, 900], tags=["garmin-api"])
def ingest_activity_task(
    user_id: int,
    activity_id: int,
    dry_run: bool,
    include_details: bool,
    include_files: bool,
):
    return ingest_activity(
        user_id=user_id,
        activity_id=activity_id,
        dry_run=dry_run,
        include_details=include_details,
        include_files=include_files,
    ).as_dict()
```

Why this granularity: activity list discovery is one Garmin API call, but the
expensive and failure-prone work is per activity. A failed FIT download should
not force a refetch of every activity in the date range.

Optional later split:

- `ingest_activity_summary_task`
- `ingest_activity_detail_task`
- `download_activity_file_task`

Do this only if detail/file failures are common enough to justify more task
noise.

### Personal Records

Direct phase-2 task granularity:

- one task per user snapshot
- parse/upsert each record inside the task
- return `partial` if individual records fail

Why this granularity: Garmin returns personal records as a current snapshot, not
a date range. Splitting per record is possible later, but it is not necessary
until record-level failures become common.

## Failure Semantics

Prefect can only retry exceptions automatically. The current ingestion code often
catches exceptions and returns status dictionaries. The refactor should make this
explicit.

Recommended rules:

- Transient call-level errors should raise inside a Prefect task so Prefect can
  retry the task.
- Expected per-row parse or upsert issues can be counted and returned as
  `partial` when the rest of the object can still complete.
- A task returns `success`, `partial`, or `error` only after retries are
  exhausted or after all rows/dates/activities assigned to that task have been
  attempted.
- The flow aggregates all task results and decides final flow state.
- `error` always fails the flow.
- `partial` logs a warning by default and fails only when `fail_on_partial=True`.

This keeps the current "capture as much data as possible" behavior while making
the Prefect run state honest.

## Rate Limiting and Concurrency

Start sequential within each user, even though the tasks are separate. This
avoids token write races and respects the existing Garmin rate-limit posture.

Use Prefect concurrency controls only after the object-level shape is stable:

- apply a `garmin-api` tag to all Garmin API tasks
- set an initial global or tag-based concurrency limit of `1`
- increase cautiously if real usage shows it is safe

Do not run multiple tasks for the same user in parallel unless token persistence
is made concurrency-safe. Each task may refresh tokens and write
`users.tokens_json`; parallel writes could lose a refresh.

If we later need parallelism, add one of these first:

- a database advisory lock around token load/save per user
- a `user_token_locks` table
- a rule that only one task owns token refresh and other tasks use a short-lived
  client snapshot

## CLI Design

The manual CLI should stay useful without requiring Prefect to be running.

Recommended command tree:

```text
garmin-sync
  auth
    login
    status
  ingest
    run
    backfill
    daily-summary
    activities
    personal-records
```

Shared options:

```text
--user DISPLAY_NAME
--days-back N
--start-date YYYY-MM-DD
--end-date YYYY-MM-DD
--dry-run
--fail-on-partial
```

Object-specific options:

```text
garmin-sync ingest activities
  --include-details / --skip-details
  --include-files / --skip-files
```

Compatibility behavior:

- `ingest run` should default to all objects.
- `ingest run --data-type daily_summary` should continue to work.
- `ingest run --data-type daily-summary` should also work.
- `ingest backfill` should use the same object registry and runner functions.
- Existing `garmin-postgres` command alias should keep working.

Example implementation shape:

```python
@ingest_app.command("daily-summary")
def daily_summary(...):
    results = ingest_daily_summary_range(...)
    print_results(results)


@ingest_app.command("activities")
def activities(...):
    results = ingest_activities_range(...)
    print_results(results)


@ingest_app.command("personal-records")
def personal_records(...):
    results = ingest_personal_records_for_users(...)
    print_results(results)
```

The Prefect app can expose matching commands for operational workflows:

```text
garmin-orchestrator deploy
garmin-orchestrator run archive
garmin-orchestrator run daily-summary
garmin-orchestrator run activities
garmin-orchestrator run personal-records
```

Those commands should create or submit Prefect deployment runs. They should not
replace the local `garmin-sync ingest ...` commands.

## Deployments

Use `prefect.yaml` for deployment definitions because it keeps schedules,
entrypoints, parameters, and work pools versioned with the repo.

Initial deployments:

| Deployment | Entrypoint | Schedule | Parameters |
| --- | --- | --- | --- |
| `garmin-archive/incremental` | `garmin_orchestrator.flows:garmin_archive_flow` | twice daily | all objects |
| `garmin-archive/daily-summary` | same flow | optional | `data_types=["daily_summary"]` |
| `garmin-archive/activities` | same flow | optional | `data_types=["activities"]` |
| `garmin-archive/personal-records` | same flow | optional | `data_types=["personal_records"]` |
| `garmin-archive/backfill` | same flow | none | explicit dates |

Use a local process work pool first:

```bash
uv run prefect work-pool create --type process garmin-local-process
uv run prefect deploy --all
uv run prefect worker start --pool garmin-local-process
```

Systemd should supervise the Prefect server and worker, not also run the old
twice-daily `garmin-sync ingest run` timer. Otherwise duplicate ingestion runs
will be scheduled.

## Self-Hosted Prefect

For local health data, prefer self-hosted Prefect first.

Recommended local setup:

- keep Garmin archive data in the existing `garmin` database
- use a separate Prefect metadata database
- add a `compose.prefect.yaml` or Compose profile for Prefect server services
- run a local process worker against the checked-out uv workspace

Do not store Prefect metadata in the Garmin archive database. Prefect stores run
state, logs, parameters, and orchestration metadata; keeping that separate avoids
mixing app data with scheduler data.

Prefect Cloud is a valid alternative if remote UI, hosted infrastructure, and
managed notifications are worth the data-governance tradeoff.

## Migration Plan

### Step 1: Introduce object registry and results

Add:

- `garmin_sync.ingest.object_registry`
- `garmin_sync.ingest.results`
- tests for data type aliases and result aggregation

Acceptance criteria:

```bash
uv run pytest tests/test_ingest.py -q
```

### Step 2: Extract date window logic

Move date parsing and default date window calculation out of CLI handlers and
pipeline functions into `garmin_sync.ingest.date_windows`.

Also resolve the current design question: should incremental ingestion default
through today or yesterday? The spec currently says today, while the current
pipeline defaults to yesterday. Prefect schedules will make this visible, so the
behavior should be made explicit before deployments go live.

### Step 3: Extract object runners

Refactor the existing pipeline into runner functions:

- `ingest_daily_summary_day`
- `ingest_daily_summary_range`
- `list_activity_ids`
- `ingest_activity`
- `ingest_activities_range`
- `ingest_personal_records`
- `ingest_selected_objects`

Keep `run_ingestion` and `run_for_all_users` as compatibility wrappers.

Acceptance criteria:

```bash
uv run pytest tests/test_ingest.py tests/test_parsers.py -q
```

### Step 4: Add CLI subcommands

Add:

- `garmin-sync ingest daily-summary`
- `garmin-sync ingest activities`
- `garmin-sync ingest personal-records`

Keep:

- `garmin-sync ingest run`
- `garmin-sync ingest backfill`
- `garmin-postgres` compatibility alias

Acceptance criteria:

```bash
uv run garmin-sync ingest --help
uv run garmin-sync ingest daily-summary --help
uv run garmin-sync ingest activities --help
uv run garmin-sync ingest personal-records --help
```

### Step 5: Add the Prefect orchestration app

Add `apps/garmin-orchestrator` with dependencies:

- `garmin-sync`
- `garmin-postgres-core`
- `prefect`
- optionally `notion-sync` later, if archive-then-Notion flows are added

Add Prefect tasks around object runners and a `garmin_archive_flow`.

Acceptance criteria:

```bash
uv run garmin-orchestrator run archive --dry-run
uv run pytest apps/garmin-orchestrator/tests -q
```

### Step 6: Add deployments

Add `prefect.yaml` with the initial deployments and a local process work pool.
Document the systemd units or Compose commands needed to run the Prefect server
and worker.

Acceptance criteria:

```bash
uv run prefect deploy --all
uv run prefect deployment ls
```

### Step 7: Retire direct systemd scheduling

Keep systemd for process supervision, but stop using the old timer to run
`garmin-sync ingest run` directly. Prefect should own schedules once deployments
are active.

## Testing Strategy

Unit tests:

- object registry alias handling
- date window parsing
- result aggregation and final failure policy
- CLI option validation

Integration tests:

- each object runner against real Postgres
- compatibility wrappers still call the same runners
- dry run fetches but does not write
- activity detail failure produces a `partial` result without losing the base
  activity row

Prefect tests:

- task wrappers call runner functions with serializable parameters
- flow aggregation fails on `error`
- `fail_on_partial=True` fails on partial results
- default flow preserves sequential user execution

## Operational Runbook

Manual local run:

```bash
uv run garmin-sync ingest daily-summary --user mike --days-back 2
uv run garmin-sync ingest activities --user mike --days-back 7
uv run garmin-sync ingest personal-records --user mike
```

Manual Prefect deployment run:

```bash
uv run prefect deployment run garmin-archive/incremental
uv run prefect deployment run garmin-archive/activities \
  --param user=mike \
  --param days_back=7
```

Worker process:

```bash
uv run prefect worker start --pool garmin-local-process
```

## Alternatives

### Put Prefect directly in `garmin-sync`

This reduces one workspace app and makes deployment entrypoints shorter. The
tradeoff is that the normal Garmin CLI app now imports a heavier orchestration
dependency and becomes less clearly separated from scheduler infrastructure.

### Use one task per data type only

This is simpler than per-date and per-activity tasks. It still improves
observability over one coarse sync task, but retries are less precise. A failed
daily summary for one date retries the whole date range. A failed activity file
download retries the whole activity range.

### Use one task per database row

This maximizes retry isolation but creates too much task noise for the current
project. It also increases Prefect metadata volume and makes the UI harder to
scan.

### Run users in parallel

This may become useful for multiple Garmin users, but it should not be the
default. The existing design intentionally processes users sequentially to avoid
rate limits. Parallel user flows should wait until the global Garmin API
concurrency limit and token persistence strategy are proven.

### Use Prefect Cloud

Prefect Cloud reduces local operational work and adds hosted UI/notifications.
For personal health data, self-hosted Prefect is the safer default because run
metadata and logs stay local.

## Open Questions

- Should incremental ingestion include today or default to yesterday?
- Should `notion-sync` become part of a later `archive-then-sync` Prefect flow?
- Should the object-specific CLI commands support a `--submit` option that
  creates a Prefect deployment run, or should Prefect submission stay in
  `garmin-orchestrator`?
- What retention policy should be used for Prefect logs and run history?
- Should partial activity file/detail failures fail scheduled runs, or only warn?

## Prefect References

- [Write and run workflows](https://docs.prefect.io/v3/how-to-guides/workflows/write-and-run.md)
- [Tasks](https://docs.prefect.io/v3/concepts/tasks.md)
- [Retries](https://docs.prefect.io/v3/how-to-guides/workflows/retries.md)
- [Create deployments](https://docs.prefect.io/v3/how-to-guides/deployments/create-deployments.md)
- [Define deployments with YAML](https://docs.prefect.io/v3/how-to-guides/deployments/prefect-yaml.md)
- [Create schedules](https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules.md)
- [Work pools](https://docs.prefect.io/v3/concepts/work-pools.md)
- [Manage work pools](https://docs.prefect.io/v3/how-to-guides/deployment_infra/manage-work-pools.md)
- [Run flows in local processes](https://docs.prefect.io/v3/how-to-guides/deployment_infra/run-flows-in-local-processes.md)
- [Self-hosted Docker Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose.md)
