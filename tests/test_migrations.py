import json
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_alembic_upgrade_head(postgres_container):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")


def test_alembic_version_stamped(postgres_container, engine):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()
        assert version is not None


def test_alembic_downgrade_base(postgres_container):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")


def test_migration_backfills_activity_columns_from_raw_json(postgres_container, engine):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", postgres_container.get_connection_url()
    )
    # Other tests in this file may leave the shared container below head.
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "8b7c4e6f2a11")

    payload = {
        "activityTypeDTO": {"typeKey": "walking"},
        "summaryDTO": {"startTimeGMT": "2026-06-20T14:26:41.0"},
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (garmin_display_name) "
                "VALUES ('migration-test-user') "
                "ON CONFLICT (garmin_display_name) DO NOTHING"
            )
        )
        user_id = conn.execute(
            text("SELECT id FROM users WHERE garmin_display_name = 'migration-test-user'")
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO activities (user_id, activity_id, raw_json) "
                "VALUES (:user_id, 999999, CAST(:payload AS jsonb))"
            ),
            {"user_id": user_id, "payload": json.dumps(payload)},
        )

    command.upgrade(alembic_cfg, "head")

    with engine.begin() as conn:
        start_time, activity_type = conn.execute(
            text("SELECT start_time, activity_type FROM activities WHERE activity_id = 999999")
        ).one()
        table_exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE tablename = 'sync_targets')"
            )
        ).scalar()

    assert start_time == datetime(2026, 6, 20, 14, 26, 41, tzinfo=timezone.utc)
    assert activity_type == "walking"
    assert table_exists is True
