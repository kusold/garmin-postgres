import logging

from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.sync_target import SyncTarget
from garmin_postgres.models.user import User
from notion_sync.sync import DATA_TYPES

logger = logging.getLogger(__name__)


def find_user(session: Session, display_name: str) -> User | None:
    stmt = select(User).where(User.garmin_display_name == display_name)
    return session.scalars(stmt).first()


def sync_target(session: Session, user_id: int, target: str = "notion") -> dict | None:
    """Return the destination-specific config_json for a user, or None."""
    stmt = select(SyncTarget).where(
        SyncTarget.user_id == user_id,
        SyncTarget.target == target,
    )
    row = session.scalars(stmt).first()
    return row.config_json if row is not None else None


def notion_sync_config(session: Session, user_id: int) -> tuple[str | None, dict[str, str]]:
    """Extract the Notion token and the {data_type: database_id} mapping.

    Database keys that are not syncable data types yet (e.g. 'sleep') are
    dropped with a warning so dormant configuration is harmless.
    """
    config = sync_target(session, user_id) or {}
    databases = {}
    databases_config = config.get("databases")
    if not isinstance(databases_config, dict):
        databases_config = {}
    for data_type, database_id in databases_config.items():
        if data_type not in DATA_TYPES:
            logger.warning(
                "Skipping data type %r in sync_targets 'notion' config "
                "(not a syncable data type)",
                data_type,
            )
            continue
        if database_id:
            databases[data_type] = database_id
    return config.get("token"), databases
