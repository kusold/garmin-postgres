"""Track the protected values last written to Notion pages.

Revision ID: 4d08c6a719f2
Revises: c2f7a91d4e30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401

revision: str = "4d08c6a719f2"
down_revision: Union[str, None] = "c2f7a91d4e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notion_page_states",
        sa.Column("page_id", sa.String(), nullable=False),
        sa.Column("properties_json", postgresql.JSONB(), nullable=False),
        sa.Column("icon_json", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("page_id", name=op.f("pk_notion_page_states")),
    )


def downgrade() -> None:
    op.drop_table("notion_page_states")
