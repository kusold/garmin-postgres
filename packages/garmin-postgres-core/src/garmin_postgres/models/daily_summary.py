from datetime import date, datetime

from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class DailySummary(BaseModel, table=True):
    __tablename__ = "daily_summaries"
    __table_args__ = (
        UniqueConstraint("user_id", "calendar_date", name="uq_daily_summaries_user_id_calendar_date"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id"), nullable=False),
    )
    calendar_date: date | None = Field(
        default=None,
        sa_column=Column(Date, nullable=False),
    )
    raw_json: dict | None = Field(
        default=None,
        sa_column=Column(JSONB),
    )
