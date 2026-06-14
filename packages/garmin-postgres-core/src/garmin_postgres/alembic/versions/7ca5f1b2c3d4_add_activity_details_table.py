"""add activity_details table

Revision ID: 7ca5f1b2c3d4
Revises: 58e6260250a7
Create Date: 2026-06-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7ca5f1b2c3d4'
down_revision: Union[str, None] = '58e6260250a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'activity_details',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('activity_id', sa.BigInteger(), nullable=False),
        sa.Column('max_chart_size', sa.Integer(), nullable=False),
        sa.Column('max_polyline_size', sa.Integer(), nullable=False),
        sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], name=op.f('fk_activity_details_activity_id_activities')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_activity_details')),
        sa.UniqueConstraint('activity_id', name='uq_activity_details_activity_id'),
    )


def downgrade() -> None:
    op.drop_table('activity_details')
