from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class Activity(BaseModel, table=True):
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_id", name="uq_activities_user_id_activity_id"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id"), nullable=False),
    )
    activity_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=False),
    )
    activity_type: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )
    start_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    raw_json: dict | None = Field(
        default=None,
        sa_column=Column(JSONB),
    )
