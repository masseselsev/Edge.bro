"""Add thermal_fit_retention_days

Revision ID: a3f5c9d21e07
Revises: c1d47e9b8a02
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f5c9d21e07'
down_revision: Union[str, None] = 'c1d47e9b8a02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no server_default: NULL means "keep forever", which is the
    # existing behaviour every current row must preserve on upgrade.
    op.add_column('settings', sa.Column('thermal_fit_retention_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('settings', 'thermal_fit_retention_days')
