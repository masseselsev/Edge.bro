"""add_orchestrator_behind_nat_to_settings

Revision ID: 0e5530d4b5c0
Revises: 4b1e39348142
Create Date: 2026-07-31 18:42:21.510624

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e5530d4b5c0'
down_revision: Union[str, None] = '4b1e39348142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('settings', sa.Column('orchestrator_behind_nat', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('settings', 'orchestrator_behind_nat')
