from __future__ import annotations

import subprocess

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolUpdate
from prefect.exceptions import ObjectNotFound


GARMIN_WORK_POOL = "garmin-docker"
GARMIN_WORK_POOL_CONCURRENCY = 1
WORK_QUEUE_PRIORITIES = {
    "scheduled": 1,
    "backfill": 10,
}


def configure_work_pool() -> None:
    """Serialize Garmin jobs and keep scheduled ingestion ahead of backfills."""
    with get_client(sync_client=True) as client:
        client.update_work_pool(
            GARMIN_WORK_POOL,
            WorkPoolUpdate(concurrency_limit=GARMIN_WORK_POOL_CONCURRENCY),
        )
        for name, priority in WORK_QUEUE_PRIORITIES.items():
            try:
                queue = client.read_work_queue_by_name(
                    name,
                    work_pool_name=GARMIN_WORK_POOL,
                )
            except ObjectNotFound:
                client.create_work_queue(
                    name=name,
                    priority=priority,
                    work_pool_name=GARMIN_WORK_POOL,
                )
            else:
                client.update_work_queue(queue.id, priority=priority)


def deploy_all() -> None:
    configure_work_pool()
    subprocess.run(["prefect", "deploy", "--all"], check=True)
