"""add sync_targets, backfill activity columns

Revision ID: c2f7a91d4e30
Revises: 8b7c4e6f2a11
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'c2f7a91d4e30'
down_revision: Union[str, None] = '8b7c4e6f2a11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sync_targets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('target', sa.String(), nullable=False),
        sa.Column('config_json', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_targets')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sync_targets_user_id_users')),
        sa.UniqueConstraint('user_id', 'target', name=op.f('uq_sync_targets_user_id_target')),
    )
    # Garmin list endpoints nest these under summaryDTO/activityTypeDTO.
    # startTimeGMT is GMT, so interpret the naive value as UTC explicitly.
    op.execute(
        """
        UPDATE activities
        SET start_time = (raw_json->'summaryDTO'->>'startTimeGMT')::timestamp AT TIME ZONE 'UTC'
        WHERE start_time IS NULL
          AND raw_json->'summaryDTO'->>'startTimeGMT' IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE activities
        SET activity_type = raw_json->'activityTypeDTO'->>'typeKey'
        WHERE activity_type IS NULL
          AND raw_json->'activityTypeDTO'->>'typeKey' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table('sync_targets')
