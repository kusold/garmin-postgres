"""add personal_records table

Revision ID: 8b7c4e6f2a11
Revises: 7ca5f1b2c3d4
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
import sqlmodel.sql.sqltypes  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = '8b7c4e6f2a11'
down_revision: Union[str, None] = '7ca5f1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'personal_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('type_id', sa.Integer(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=True),
        sa.Column('activity_type', sa.String(), nullable=True),
        sa.Column('value_text', sa.String(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_personal_records_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_personal_records')),
        sa.UniqueConstraint(
            'user_id',
            'type_id',
            'record_date',
            'value_text',
            name='uq_personal_records_user_id_type_id_record_date_value_text',
        ),
    )


def downgrade() -> None:
    op.drop_table('personal_records')
