"""add_ssh_login_to_nodes

Revision ID: a1f3c9b2d7e4
Revises: 04e15e8f5fa0
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c9b2d7e4'
down_revision: Union[str, None] = '04e15e8f5fa0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nodes', sa.Column('ssh_login', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'ssh_login')
