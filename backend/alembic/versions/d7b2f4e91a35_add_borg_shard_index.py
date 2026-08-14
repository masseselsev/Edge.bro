"""Add nodes.borg_shard_index, drop the vestigial settings.borg_repo_path

Revision ID: d7b2f4e91a35
Revises: a3f5c9d21e07
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7b2f4e91a35'
down_revision: Union[str, None] = 'a3f5c9d21e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='0' backfills every existing row as part of adding the
    # column. Shard 0 is the repository those nodes are already backing up to,
    # so this upgrade changes no node's behaviour — which is what makes the
    # rollout free of any data migration.
    op.add_column(
        'nodes',
        sa.Column('borg_shard_index', sa.Integer(), nullable=False, server_default='0'),
    )

    # Never read by anything that builds a borg command — the real path was a
    # hardcoded literal in a dozen modules, now core/repo_paths. Leaving a dead
    # field in the settings UI beside real sharding would misrepresent it.
    op.drop_column('settings', 'borg_repo_path')


def downgrade() -> None:
    op.add_column(
        'settings',
        sa.Column('borg_repo_path', sa.String(), nullable=True, server_default='/data/borg'),
    )
    op.drop_column('nodes', 'borg_shard_index')
