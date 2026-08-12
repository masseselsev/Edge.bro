"""add alerts and notification prefs

Revision ID: 6ddf104e8f43
Revises: d1f6b83c94a7
Create Date: 2026-08-12 23:48:08.106781

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ddf104e8f43'
down_revision: Union[str, None] = 'd1f6b83c94a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('module', sa.String(), nullable=False),
        sa.Column('dedup_key', sa.String(), nullable=False),
        sa.Column('node_id', sa.Integer(), sa.ForeignKey('nodes.id', ondelete='CASCADE'), nullable=True),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='OPEN'),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('first_seen', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('acknowledged_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_alerts_dedup_key', 'alerts', ['dedup_key'])
    # Only one OPEN/ACKNOWLEDGED row per dedup_key at a time. A RESOLVED row
    # with the same key does not conflict, so a recurring problem opens a
    # fresh episode instead of colliding with its own history.
    op.create_index(
        'uq_alert_open_dedup', 'alerts', ['dedup_key'],
        unique=True,
        postgresql_where=sa.text("status != 'RESOLVED'"),
    )
    op.add_column('users', sa.Column('notification_prefs', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'notification_prefs')
    op.drop_index('uq_alert_open_dedup', table_name='alerts')
    op.drop_index('ix_alerts_dedup_key', table_name='alerts')
    op.drop_table('alerts')
