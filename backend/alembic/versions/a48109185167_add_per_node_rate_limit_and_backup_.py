"""add per node rate limit and backup speed metrics

Revision ID: a48109185167
Revises: fbfb84473589
Create Date: 2026-08-10 20:20:41.379537

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a48109185167'
down_revision: Union[str, None] = 'fbfb84473589'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nodes', sa.Column('upload_rate_limit', sa.Integer(), nullable=True))
    op.add_column('backup_history', sa.Column('avg_speed_mbps', sa.Float(), nullable=True))
    op.add_column('backup_history', sa.Column('max_speed_mbps', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('backup_history', 'max_speed_mbps')
    op.drop_column('backup_history', 'avg_speed_mbps')
    op.drop_column('nodes', 'upload_rate_limit')
