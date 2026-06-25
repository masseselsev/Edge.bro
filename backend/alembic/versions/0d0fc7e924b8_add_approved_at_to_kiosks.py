"""add approved_at to kiosks

Revision ID: 0d0fc7e924b8
Revises: 0f201ea3cf93
Create Date: 2026-06-25 16:28:43.013976

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d0fc7e924b8'
down_revision: Union[str, None] = '0f201ea3cf93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('kiosks', sa.Column('approved_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('kiosks', 'approved_at')

