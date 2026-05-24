import os

import pytest


def test_settings_loads_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://testuser:testpass@localhost:5432/testdb")
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.database_url == "postgresql://testuser:testpass@localhost:5432/testdb"


def test_settings_default_log_level():
    os.environ.pop("LOG_LEVEL", None)
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.log_level == "INFO"


def test_settings_default_ingest_days_back():
    os.environ.pop("INGEST_DAYS_BACK", None)
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.ingest_days_back == 1


def test_get_settings_returns_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    from garmin_postgres.config import get_settings

    settings = get_settings()
    assert settings.database_url is not None
