"""add_allow_admin_key_terminal_access_to_settings

Revision ID: f2b7e4a19c3d
Revises: a1f3c9b2d7e4
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b7e4a19c3d'
down_revision: Union[str, None] = 'a1f3c9b2d7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'settings',
        sa.Column('allow_admin_key_terminal_access', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('settings', 'allow_admin_key_terminal_access')
