import logging

from garmin_postgres.models.sync_target import SyncTarget
from notion_sync.targets import find_user, notion_sync_config, sync_target


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    """Fake session; ``rows`` is a list of row-lists popped in call order."""

    def __init__(self, rows):
        self.rows = rows

    def scalars(self, stmt):
        return FakeScalarResult(self.rows.pop(0))


def test_sync_target_returns_config_json_for_matching_row():
    row = SyncTarget(id=1, user_id=7, target="notion", config_json={"token": "t"})
    session = FakeSession(rows=[[row]])

    assert sync_target(session, user_id=7) == {"token": "t"}


def test_sync_target_returns_none_when_no_row():
    session = FakeSession(rows=[[]])

    assert sync_target(session, user_id=7) is None


def test_notion_sync_config_extracts_token_and_known_databases():
    config = {
        "token": "secret_abc",
        "databases": {
            "activities": "db-act",
            "daily_steps": "db-steps",
            "personal_records": "db-pr",
            "sleep": "db-sleep",
        },
    }
    session = FakeSession(rows=[[SyncTarget(config_json=config)]])

    token, databases = notion_sync_config(session, user_id=1)

    assert token == "secret_abc"
    assert databases == {
        "activities": "db-act",
        "daily_steps": "db-steps",
        "personal_records": "db-pr",
    }


def test_notion_sync_config_warns_and_drops_unknown_data_types(caplog):
    session = FakeSession(
        rows=[[SyncTarget(config_json={"databases": {"sleep": "db-sleep"}})]]
    )

    with caplog.at_level(logging.WARNING):
        token, databases = notion_sync_config(session, user_id=1)

    assert token is None
    assert databases == {}
    assert any("sleep" in record.message for record in caplog.records)


def test_notion_sync_config_returns_empty_databases_when_absent():
    session = FakeSession(rows=[[SyncTarget(config_json={"token": "t"})]])

    token, databases = notion_sync_config(session, user_id=1)

    assert token == "t"
    assert databases == {}


def test_notion_sync_config_handles_malformed_databases_value():
    session = FakeSession(
        rows=[[SyncTarget(config_json={"databases": ["activities"]})]]
    )

    token, databases = notion_sync_config(session, user_id=1)

    assert token is None
    assert databases == {}


def test_notion_sync_config_handles_missing_row():
    session = FakeSession(rows=[[]])

    token, databases = notion_sync_config(session, user_id=1)

    assert token is None
    assert databases == {}


def test_find_user_matches_by_display_name():
    from garmin_postgres.models.user import User

    user = User(id=3, garmin_display_name="f65ac324-0000")
    session = FakeSession(rows=[[user]])

    assert find_user(session, "f65ac324-0000") is user

    missing = FakeSession(rows=[[]])
    assert find_user(missing, "nobody") is None
