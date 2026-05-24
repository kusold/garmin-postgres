"""initial

Revision ID: e5b5cd9f66a8
Revises: 
Create Date: 2026-05-24 03:27:01.569612
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401



# revision identifiers, used by Alembic.
revision: str = 'e5b5cd9f66a8'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
