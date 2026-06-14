import os
from dataclasses import dataclass

from dotenv import dotenv_values


@dataclass(frozen=True)
class NotionSettings:
    token: str | None
    activities_database_id: str | None
    daily_steps_database_id: str | None
    personal_records_database_id: str | None
    timezone: str


def _env_value(values: dict[str, str | None], key: str) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    return values.get(key)


def get_settings() -> NotionSettings:
    values = dotenv_values(".env")
    default_database_id = _env_value(values, "NOTION_DB_ID")
    return NotionSettings(
        token=_env_value(values, "NOTION_TOKEN"),
        activities_database_id=_env_value(values, "NOTION_ACTIVITIES_DB_ID") or default_database_id,
        daily_steps_database_id=_env_value(values, "NOTION_DAILY_STEPS_DB_ID"),
        personal_records_database_id=_env_value(values, "NOTION_PERSONAL_RECORDS_DB_ID"),
        timezone=_env_value(values, "NOTION_TIMEZONE") or "UTC",
    )
