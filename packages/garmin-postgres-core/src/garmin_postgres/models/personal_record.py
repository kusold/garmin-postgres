from datetime import date, datetime

from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class PersonalRecord(BaseModel, table=True):
    __tablename__ = "personal_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "type_id",
            "record_date",
            "value_text",
            name="uq_personal_records_user_id_type_id_record_date_value_text",
        ),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id"), nullable=False),
    )
    type_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False),
    )
    record_date: date = Field(
        sa_column=Column(Date, nullable=False),
    )
    activity_type: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )
    value_text: str = Field(
        sa_column=Column(String, nullable=False),
    )
    raw_json: dict | None = Field(
        default=None,
        sa_column=Column(JSONB),
    )
