from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class User(BaseModel, table=True):
    __tablename__ = "users"

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    garmin_display_name: str | None = Field(
        default=None,
        sa_column=Column(String, unique=True),
    )
    timezone: str | None = Field(default=None, sa_column=Column(String))
    tokens_json: str | None = Field(default=None, sa_column=Column(JSONB))
    is_active: bool | None = Field(
        default=True,
        sa_column=Column(Boolean, server_default="true"),
    )
    last_ingest_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )
    raw_json: dict | None = Field(default=None, sa_column=Column(JSONB))
