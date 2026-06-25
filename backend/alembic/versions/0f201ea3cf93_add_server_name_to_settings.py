"""add server_name to settings

Revision ID: 0f201ea3cf93
Revises: 2c2804f7c0ef
Create Date: 2026-06-25 16:15:48.804474

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f201ea3cf93'
down_revision: Union[str, None] = '2c2804f7c0ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('settings', sa.Column('server_name', sa.String(), server_default='orchestrator', nullable=False))


def downgrade() -> None:
    op.drop_column('settings', 'server_name')

