"""add backup duration and failure category

Revision ID: b7d31c9a4e02
Revises: a48109185167
Create Date: 2026-08-11 09:12:04.118293

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d31c9a4e02'
down_revision: Union[str, None] = 'a48109185167'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backup_history', sa.Column('duration_seconds', sa.Float(), nullable=True))
    op.add_column('backup_history', sa.Column('error_category', sa.String(), nullable=True))
    # The insight queries all scan a trailing time window across the whole fleet,
    # which the existing (node_id, status) index cannot serve.
    op.create_index('ix_backup_history_timestamp', 'backup_history', ['timestamp'])


def downgrade() -> None:
    op.drop_index('ix_backup_history_timestamp', table_name='backup_history')
    op.drop_column('backup_history', 'error_category')
    op.drop_column('backup_history', 'duration_seconds')
