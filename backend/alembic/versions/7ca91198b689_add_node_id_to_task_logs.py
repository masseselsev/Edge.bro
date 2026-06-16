"""add_node_id_to_task_logs

Revision ID: 7ca91198b689
Revises: 04a7345baa94
Create Date: 2026-06-16 13:50:27.744383

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ca91198b689'
down_revision: Union[str, None] = '04a7345baa94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('task_logs', sa.Column('node_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_task_logs_node_id_nodes',
        'task_logs', 'nodes',
        ['node_id'], ['id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    op.drop_constraint('fk_task_logs_node_id_nodes', 'task_logs', type_='foreignkey')
    op.drop_column('task_logs', 'node_id')
