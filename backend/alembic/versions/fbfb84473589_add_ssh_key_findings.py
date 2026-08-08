"""add ssh key findings

Revision ID: fbfb84473589
Revises: d4af17b40e65
Create Date: 2026-08-09 02:36:16.853987

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbfb84473589'
down_revision: Union[str, None] = 'd4af17b40e65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ssh_key_findings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('location', sa.String(), nullable=False),
        sa.Column('host', sa.String(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=True),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('key_type', sa.String(), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('options', sa.Text(), nullable=True),
        sa.Column('classification', sa.String(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('first_seen', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('orphan_since', sa.DateTime(), nullable=True),
        sa.Column('orphan_scan_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('pruned_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('location', 'host', 'fingerprint', name='uq_ssh_finding'),
    )
    op.create_index(op.f('ix_ssh_key_findings_id'), 'ssh_key_findings', ['id'])
    op.create_index(op.f('ix_ssh_key_findings_fingerprint'), 'ssh_key_findings', ['fingerprint'])
    op.add_column('nodes', sa.Column('node_authorized_keys', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'node_authorized_keys')
    op.drop_index(op.f('ix_ssh_key_findings_fingerprint'), table_name='ssh_key_findings')
    op.drop_index(op.f('ix_ssh_key_findings_id'), table_name='ssh_key_findings')
    op.drop_table('ssh_key_findings')
