from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    token: str | None = Field(default=None, validation_alias=AliasChoices("NOTION_TOKEN"))
    activities_database_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NOTION_ACTIVITIES_DB_ID"),
    )
    daily_steps_database_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NOTION_DAILY_STEPS_DB_ID"),
    )
    personal_records_database_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NOTION_PERSONAL_RECORDS_DB_ID"),
    )


def get_settings() -> NotionSettings:
    return NotionSettings()
