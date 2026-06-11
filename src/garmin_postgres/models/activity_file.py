from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class ActivityFile(BaseModel, table=True):
    __tablename__ = "activity_files"
    __table_args__ = (
        UniqueConstraint("activity_id", "file_format", name="uq_activity_files_activity_id_file_format"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    activity_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("activities.id"), nullable=False),
    )
    file_format: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=False),
    )
    file_data: bytes | None = Field(
        default=None,
        sa_column=Column(LargeBinary, nullable=True),
    )
    raw_json: dict | None = Field(
        default=None,
        sa_column=Column(JSONB),
    )
