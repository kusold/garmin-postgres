from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "/app/.env"),
        env_file_encoding="utf-8",
    )

    database_url: str = "postgresql://garmin:garmin@localhost:5432/garmin"
    log_level: str = "INFO"
    ingest_days_back: int = 1


def get_settings() -> Settings:
    return Settings()
