"""add daily_summaries table

Revision ID: a1b2c3d4e5f6
Revises: f044290879a0
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f044290879a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_summaries',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('calendar_date', sa.Date(), nullable=False),
        sa.Column('raw_json', postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_daily_summaries')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_daily_summaries_user_id_users')),
        sa.UniqueConstraint('user_id', 'calendar_date', name='uq_daily_summaries_user_id_calendar_date'),
    )


def downgrade() -> None:
    op.drop_table('daily_summaries')
