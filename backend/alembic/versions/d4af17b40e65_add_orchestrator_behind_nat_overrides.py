"""add_orchestrator_behind_nat_overrides

Revision ID: d4af17b40e65
Revises: 0e5530d4b5c0
Create Date: 2026-07-31 20:23:57.698015

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4af17b40e65'
down_revision: Union[str, None] = '0e5530d4b5c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable on purpose: NULL means "inherit" (node -> group -> global setting),
    # which is different from an explicit False.
    op.add_column('backup_groups', sa.Column('orchestrator_behind_nat', sa.Boolean(), nullable=True))
    op.add_column('nodes', sa.Column('orchestrator_behind_nat', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'orchestrator_behind_nat')
    op.drop_column('backup_groups', 'orchestrator_behind_nat')
