"""add_cpu_quota_to_nodes

Revision ID: 96a059ce4bd2
Revises: ec8383c7a149
Create Date: 2026-08-25 15:07:09.690085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96a059ce4bd2'
down_revision: Union[str, None] = 'ec8383c7a149'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable on purpose: NULL means "inherit" (node -> group -> global
    # default_cpu_quota). 0 is a valid, distinct value meaning "explicit
    # no limit" — see backend/models.py:Node.cpu_quota for why.
    op.add_column('nodes', sa.Column('cpu_quota', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'cpu_quota')
