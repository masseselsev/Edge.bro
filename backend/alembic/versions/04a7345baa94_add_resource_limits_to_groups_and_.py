"""add_resource_limits_to_groups_and_settings

Revision ID: 04a7345baa94
Revises: eb89a8231897
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04a7345baa94'
down_revision: Union[str, None] = 'eb89a8231897'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backup_groups', sa.Column('upload_rate_limit', sa.Integer(), nullable=True))
    op.add_column('backup_groups', sa.Column('compression', sa.String(), nullable=True))
    op.add_column('backup_groups', sa.Column('checkpoint_interval', sa.Integer(), nullable=True))
    op.add_column('backup_groups', sa.Column('cpu_quota', sa.Integer(), nullable=True))
    op.add_column('settings', sa.Column('default_compression', sa.String(),
                  server_default='zstd:3', nullable=False))
    op.add_column('settings', sa.Column('default_cpu_quota', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('backup_groups', 'upload_rate_limit')
    op.drop_column('backup_groups', 'compression')
    op.drop_column('backup_groups', 'checkpoint_interval')
    op.drop_column('backup_groups', 'cpu_quota')
    op.drop_column('settings', 'default_compression')
    op.drop_column('settings', 'default_cpu_quota')
