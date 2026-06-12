from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class ActivityDetail(BaseModel, table=True):
    __tablename__ = "activity_details"
    __table_args__ = (
        UniqueConstraint("activity_id", name="uq_activity_details_activity_id"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    activity_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("activities.id"), nullable=False),
    )
    max_chart_size: int | None = Field(
        default=2000,
        sa_column=Column(Integer, nullable=False),
    )
    max_polyline_size: int | None = Field(
        default=4000,
        sa_column=Column(Integer, nullable=False),
    )
    raw_json: dict | None = Field(
        default=None,
        sa_column=Column(JSONB),
    )
