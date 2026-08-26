"""add_os_arch_to_nodes

Revision ID: c8c106fdd95c
Revises: 96a059ce4bd2
Create Date: 2026-08-26 17:24:36.789416

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8c106fdd95c'
down_revision: Union[str, None] = '96a059ce4bd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nodes', sa.Column('os_arch', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'os_arch')
