"""Move Notion page baselines to destination-neutral sync state.

Revision ID: 8c7e5246d3ab
Revises: 4d08c6a719f2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401

revision: str = "8c7e5246d3ab"
down_revision: Union[str, None] = "4d08c6a719f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "destination_sync_states",
        sa.Column("destination_key", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("last_written_json", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "destination_key", "item_id", name=op.f("pk_destination_sync_states")
        ),
    )
    op.execute("""
        INSERT INTO destination_sync_states
            (destination_key, item_id, last_written_json)
        SELECT 'notion', page_id,
               jsonb_build_object('properties', properties_json, 'icon', icon_json)
        FROM notion_page_states
    """)
    op.drop_table("notion_page_states")


def downgrade() -> None:
    op.create_table(
        "notion_page_states",
        sa.Column("page_id", sa.String(), nullable=False),
        sa.Column("properties_json", postgresql.JSONB(), nullable=False),
        sa.Column("icon_json", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("page_id", name=op.f("pk_notion_page_states")),
    )
    op.execute("""
        INSERT INTO notion_page_states (page_id, properties_json, icon_json)
        SELECT item_id,
               COALESCE(last_written_json->'properties', '{}'::jsonb),
               last_written_json->'icon'
        FROM destination_sync_states
        WHERE destination_key = 'notion'
    """)
    op.drop_table("destination_sync_states")
