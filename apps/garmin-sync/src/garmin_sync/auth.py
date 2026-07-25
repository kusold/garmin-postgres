import getpass
import logging

from garminconnect import Garmin
from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.models.user import User

logger = logging.getLogger(__name__)


def _dump_tokens(garmin: Garmin) -> str:
    if hasattr(garmin, "client") and hasattr(garmin.client, "dumps"):
        return garmin.client.dumps()
    if hasattr(garmin, "garth") and hasattr(garmin.garth, "dumps"):
        return garmin.garth.dumps()
    raise RuntimeError("Garmin client does not support token serialization")


def _load_tokens(garmin: Garmin, tokens_json: str) -> None:
    if hasattr(garmin, "client") and hasattr(garmin.client, "loads"):
        garmin.client.loads(tokens_json)
        return
    if hasattr(garmin, "garth") and hasattr(garmin.garth, "loads"):
        garmin.login(tokens_json)
        return
    raise RuntimeError("Garmin client does not support token loading")


def _fetch_profile(garmin: Garmin) -> dict:
    if hasattr(garmin, "client") and hasattr(garmin.client, "connectapi"):
        return garmin.client.connectapi("/userprofile-service/socialProfile")
    if hasattr(garmin, "garth") and hasattr(garmin.garth, "connectapi"):
        profile = getattr(garmin.garth, "profile", None)
        if isinstance(profile, dict):
            return profile
        for path in (
            "/userprofile-service/socialProfile",
            "/userprofile-service/userprofile/profile",
        ):
            try:
                return garmin.garth.connectapi(path)
            except Exception:
                continue
    raise RuntimeError("Garmin client does not support profile loading")


def login_interactive(email: str | None = None) -> Garmin:
    if not email:
        email = input("Garmin email: ")

    password = getpass.getpass("Garmin password: ")

    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Enter MFA code: "),
    )
    garmin.login()

    return garmin


def upsert_user(session: Session, garmin: Garmin) -> User:
    profile = _fetch_profile(garmin)

    if not garmin.display_name:
        garmin.display_name = profile.get("displayName")
        garmin.full_name = profile.get("fullName", "")

    display_name = garmin.display_name

    stmt = select(User).where(User.garmin_display_name == display_name)
    existing = session.scalars(stmt).first()

    tokens = _dump_tokens(garmin)

    if existing:
        existing.garmin_display_name = display_name
        existing.tokens_json = tokens
        existing.raw_json = profile
        session.add(existing)
        session.commit()
        return existing

    user = User(
        garmin_display_name=display_name,
        tokens_json=tokens,
        raw_json=profile,
    )
    session.add(user)
    session.commit()
    return user


def load_user_client(session: Session, user: User) -> Garmin | None:
    if not user.tokens_json:
        return None

    try:
        garmin = Garmin()
        _load_tokens(garmin, user.tokens_json)
        if not garmin.display_name:
            garmin.display_name = user.garmin_display_name
        return garmin
    except Exception:
        logger.warning("Failed to load tokens for user %s", user.garmin_display_name)
        return None


def save_tokens(session: Session, user: User, garmin: Garmin) -> None:
    user.tokens_json = _dump_tokens(garmin)
    session.add(user)
    session.commit()


def refresh_tokens(session: Session, user: User) -> bool:
    garmin = load_user_client(session, user)
    if not garmin:
        return False

    try:
        garmin.get_user_profile()
        save_tokens(session, user, garmin)
        return True
    except (GarminConnectAuthenticationError, GarminConnectConnectionError):
        logger.warning("Token refresh failed for user %s", user.garmin_display_name)
        return False
