"""One user's Notion sync run, shared by the CLI and Prefect adapters."""

from collections.abc import Collection
from datetime import date

from notion_client import Client, RetryOptions
from sqlmodel import Session

from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User
from garmin_postgres.sync_state import SyncStateStore
from notion_sync.notion import NotionSink
from notion_sync.sync import DATA_TYPES, run_sync
from notion_sync.targets import _notion_config, sync_target


class _DatabasePreviewSink:
    """Map archived rows without contacting Notion when no token is present."""

    def upsert_page(
        self,
        database_id: str,
        *,
        filter_payload: dict,
        properties: dict,
        icon: dict | None = None,
        protected: Collection[str] = frozenset(),
    ) -> str:
        return "dry_run"


def normalize_notion_data_types(data_types: list[str] | None) -> list[str]:
    selected = list(dict.fromkeys(data_types or DATA_TYPES))
    invalid = sorted(set(selected) - set(DATA_TYPES))
    if invalid:
        raise ValueError(
            "Unsupported Notion data type(s): "
            f"{', '.join(invalid)}. Expected one of: {', '.join(DATA_TYPES)}"
        )
    return selected


def run_user_sync(
    user_id: int,
    *,
    data_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = False,
    include_updated_activities: bool = True,
) -> dict[str, dict]:
    """Sync selected archived data for one active user's Notion target.

    The date window applies to activities and daily steps. Personal records
    always represent the user's full archived snapshot. A dry run may read
    Notion when a token is configured, but never writes to it.
    """
    selected = normalize_notion_data_types(data_types)
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    engine = get_engine()
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError(f"Garmin user id={user_id} was not found")
        if user.is_active is not True:
            raise ValueError(f"Garmin user id={user_id} is inactive")

        config = sync_target(session, user_id)
        if config is None:
            raise ValueError(f"Garmin user id={user_id} has no 'notion' sync target")
        token, targets = _notion_config(config)
        if not targets:
            raise ValueError(f"Garmin user id={user_id} has no Notion databases")
        if not token and not dry_run:
            raise ValueError(f"Garmin user id={user_id} has no Notion token")

        if token:
            # SDK retries are method-aware; the sink owns only call pacing.
            with Client(auth=token, retry=RetryOptions(max_retries=5)) as client:
                sink = NotionSink(
                    client, dry_run=dry_run, state_store=SyncStateStore(session),
                )
                result = run_sync(
                    session, sink, targets, data_types=selected,
                    start_date=start_date, end_date=end_date, user_id=user_id,
                    include_updated_activities=include_updated_activities,
                )
                if not dry_run:
                    session.commit()
                return result

        return run_sync(
            session, _DatabasePreviewSink(), targets, data_types=selected,
            start_date=start_date, end_date=end_date, user_id=user_id,
            include_updated_activities=include_updated_activities,
        )
