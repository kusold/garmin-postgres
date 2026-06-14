from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from garmin_postgres.config import get_settings


def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url)


def get_session() -> Generator[Session, None]:
    engine = get_engine()
    with Session(engine) as session:
        yield session
