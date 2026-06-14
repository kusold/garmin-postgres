"""add users table

Revision ID: f044290879a0
Revises: e5b5cd9f66a8
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'f044290879a0'
down_revision: Union[str, None] = 'e5b5cd9f66a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('garmin_display_name', sa.String(), nullable=True),
        sa.Column('timezone', sa.String(), nullable=True),
        sa.Column('tokens_json', postgresql.JSONB(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('last_ingest_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
        sa.UniqueConstraint('garmin_display_name', name=op.f('uq_users_garmin_display_name')),
    )


def downgrade() -> None:
    op.drop_table('users')
