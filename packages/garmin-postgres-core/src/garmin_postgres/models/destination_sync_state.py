from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DestinationSyncState(SQLModel, table=True):
    """Last values written to one item in a configured destination namespace."""

    __tablename__ = "destination_sync_states"

    destination_key: str = Field(primary_key=True, sa_type=String)
    item_id: str = Field(primary_key=True, sa_type=String)
    last_written_json: dict = Field(sa_column=Column(JSONB, nullable=False))
