from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from garmin_postgres.models.base import BaseModel, created_at_field, pk_field, updated_at_field


class SyncTarget(BaseModel, table=True):
    """Per-user configuration for one sync destination (e.g. Notion).

    config_json is destination-specific. For target='notion' it holds
    {"token": "...", "databases": {"activities": "<id>", ...}}.
    """

    __tablename__ = "sync_targets"
    __table_args__ = (
        UniqueConstraint("user_id", "target", name="uq_sync_targets_user_id_target"),
    )

    id: int | None = pk_field()
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id"), nullable=False),
    )
    target: str = Field(sa_column=Column(String, nullable=False))
    config_json: dict = Field(sa_column=Column(JSONB, nullable=False))
