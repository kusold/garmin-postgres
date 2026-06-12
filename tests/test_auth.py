from unittest.mock import MagicMock, patch

from sqlalchemy import select

from garmin_postgres.auth import (
    load_user_client,
    refresh_tokens,
    save_tokens,
    upsert_user,
)
from garmin_postgres.models.user import User


def _make_mock_garmin(
    display_name="testuser",
    full_name="Test User",
    tokens_json='{"di_token": "abc123"}',
    profile=None,
):
    """Build a mock Garmin client with sensible defaults."""
    garmin = MagicMock()
    garmin.display_name = display_name
    garmin.full_name = full_name
    garmin.client.dumps.return_value = tokens_json
    garmin.client.connectapi.return_value = profile or {
        "displayName": display_name,
        "fullName": full_name,
    }
    return garmin


# -- upsert_user tests --


def test_upsert_user_creates_new_user(session):
    garmin = _make_mock_garmin(
        display_name="runner42",
        full_name="Runner Forty-Two",
        tokens_json='{"di_token": "tok_new"}',
    )

    user = upsert_user(session, garmin)

    assert user.id is not None
    assert user.garmin_display_name == "runner42"
    assert user.tokens_json == '{"di_token": "tok_new"}'
    assert user.raw_json["displayName"] == "runner42"
    assert user.raw_json["fullName"] == "Runner Forty-Two"

    # Verify it is persisted
    found = session.scalars(select(User).where(User.garmin_display_name == "runner42")).first()
    assert found is not None
    assert found.id == user.id


def test_upsert_user_updates_existing_user(session):
    # Create a user first
    garmin_v1 = _make_mock_garmin(
        display_name="runner42",
        tokens_json='{"di_token": "tok_v1"}',
    )
    original = upsert_user(session, garmin_v1)
    original_id = original.id

    # Now call upsert_user again with updated tokens
    garmin_v2 = _make_mock_garmin(
        display_name="runner42",
        tokens_json='{"di_token": "tok_v2"}',
        profile={"displayName": "runner42", "fullName": "Updated Name"},
    )
    updated = upsert_user(session, garmin_v2)

    assert updated.id == original_id
    assert updated.tokens_json == '{"di_token": "tok_v2"}'
    assert updated.raw_json["fullName"] == "Updated Name"

    # Only one user row for this display_name
    rows = session.scalars(select(User).where(User.garmin_display_name == "runner42")).all()
    assert len(rows) == 1


# -- save_tokens tests --


def test_save_tokens(session):
    user = User(
        garmin_display_name="tokentest",
        tokens_json="old",
        raw_json={},
    )
    session.add(user)
    session.commit()

    garmin = MagicMock()
    garmin.client.dumps.return_value = '{"di_token": "fresh"}'

    save_tokens(session, user, garmin)

    assert user.tokens_json == '{"di_token": "fresh"}'

    # Verify persistence
    found = session.get(User, user.id)
    assert found.tokens_json == '{"di_token": "fresh"}'


# -- load_user_client tests --


@patch("garmin_postgres.auth.Garmin")
def test_load_user_client_success(mock_garmin_cls):
    session = MagicMock()
    user = User(
        garmin_display_name="loader",
        tokens_json='{"di_token": "valid"}',
        raw_json={},
    )

    mock_instance = MagicMock()
    mock_garmin_cls.return_value = mock_instance

    result = load_user_client(session, user)

    assert result is mock_instance
    mock_garmin_cls.assert_called_once()
    mock_instance.client.loads.assert_called_once_with('{"di_token": "valid"}')


def test_load_user_client_no_tokens():
    session = MagicMock()
    user = User(
        garmin_display_name="notokens",
        tokens_json=None,
        raw_json={},
    )

    result = load_user_client(session, user)
    assert result is None


@patch("garmin_postgres.auth.Garmin")
def test_load_user_client_invalid_tokens(mock_garmin_cls):
    session = MagicMock()
    user = User(
        garmin_display_name="badtokens",
        tokens_json="not-valid-json",
        raw_json={},
    )

    mock_instance = MagicMock()
    mock_instance.client.loads.side_effect = Exception("corrupt tokens")
    mock_garmin_cls.return_value = mock_instance

    result = load_user_client(session, user)
    assert result is None


# -- refresh_tokens tests --


@patch("garmin_postgres.auth.save_tokens")
@patch("garmin_postgres.auth.load_user_client")
def test_refresh_tokens_success(mock_load, mock_save):
    session = MagicMock()
    user = User(garmin_display_name="refresher", tokens_json="some", raw_json={})

    mock_garmin = MagicMock()
    mock_load.return_value = mock_garmin

    result = refresh_tokens(session, user)

    assert result is True
    mock_garmin.get_user_profile.assert_called_once()
    mock_save.assert_called_once_with(session, user, mock_garmin)


@patch("garmin_postgres.auth.load_user_client")
def test_refresh_tokens_failure(mock_load):
    from garminconnect import GarminConnectAuthenticationError

    session = MagicMock()
    user = User(garmin_display_name="expired", tokens_json="old", raw_json={})

    mock_garmin = MagicMock()
    mock_garmin.get_user_profile.side_effect = GarminConnectAuthenticationError("expired")
    mock_load.return_value = mock_garmin

    result = refresh_tokens(session, user)
    assert result is False


# -- CLI tests --


def test_auth_status_no_users():
    from typer.testing import CliRunner

    from garmin_postgres.cli import app

    with (
        patch("garmin_postgres.cli.get_engine"),
        patch("garmin_postgres.cli.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session_cls.return_value = mock_session
        # Simulate no users
        mock_session.scalars.return_value.all.return_value = []

        runner = CliRunner()
        result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "No users found" in result.output


def test_auth_login():
    from typer.testing import CliRunner

    from garmin_postgres.cli import app

    mock_garmin = _make_mock_garmin(display_name="clinew", tokens_json='{"di_token": "cli"}')

    with (
        patch("garmin_postgres.auth.login_interactive", return_value=mock_garmin) as mock_login,
        patch("garmin_postgres.auth.upsert_user") as mock_upsert,
        patch("garmin_postgres.cli.get_engine"),
    ):
        mock_upsert.return_value = MagicMock(garmin_display_name="clinew", raw_json=None)
        # Patch the Session used inside the login command so it doesn't need a real DB
        with patch("garmin_postgres.cli.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session_cls.return_value = mock_session

            runner = CliRunner()
            result = runner.invoke(app, ["auth", "login", "--email", "test@example.com"])

    assert result.exit_code == 0
    mock_login.assert_called_once_with("test@example.com")
    assert "clinew" in result.output
