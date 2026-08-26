"""add_default_rate_limit_to_settings

Revision ID: 04e15e8f5fa0
Revises: c8c106fdd95c
Create Date: 2026-08-26 17:48:01.987092

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04e15e8f5fa0'
down_revision: Union[str, None] = 'c8c106fdd95c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('settings', sa.Column('default_rate_limit', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('settings', 'default_rate_limit')
