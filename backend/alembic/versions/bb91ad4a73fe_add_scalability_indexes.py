"""add_scalability_indexes

Revision ID: bb91ad4a73fe
Revises: 236715e36c6e
Create Date: 2026-07-14 09:43:45.752505

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb91ad4a73fe'
down_revision: Union[str, None] = '236715e36c6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_nodes_group_id', 'nodes', ['group_id'])
    op.create_index('ix_task_log_node_id_created_at', 'task_logs', ['node_id', 'created_at'])
    op.create_index('ix_backup_history_node_id_status', 'backup_history', ['node_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_nodes_group_id', table_name='nodes')
    op.drop_index('ix_task_log_node_id_created_at', table_name='task_logs')
    op.drop_index('ix_backup_history_node_id_status', table_name='backup_history')
