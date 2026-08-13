"""add hot path indexes

Indexes for columns that are filtered or ordered on constantly but were never
indexed. Each one below is on a path that runs on a timer or on every request,
so at fleet scale these are sequential scans repeated indefinitely:

  nodes.status            scheduler tick (60s), bootstrap retry (5min), node list
  task_logs.status        daily prune, startup reconciliation
  backup_history.status   fleet-wide "all successful archives" aggregation
  system_logs.created_at  ORDER BY created_at DESC LIMIT 500, plus the new prune
  audit_logs.created_at   ORDER BY created_at DESC LIMIT 1000, plus the new prune
  alerts.status/node_id/last_seen   alert sweep and the notifications API

Revision ID: c1d47e9b8a02
Revises: 6ddf104e8f43
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d47e9b8a02'
down_revision: Union[str, None] = '6ddf104e8f43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index name, table, columns)
_INDEXES = [
    ('ix_nodes_status', 'nodes', ['status']),
    ('ix_task_logs_status', 'task_logs', ['status']),
    ('ix_backup_history_status', 'backup_history', ['status']),
    ('ix_system_logs_created_at', 'system_logs', ['created_at']),
    ('ix_audit_logs_created_at', 'audit_logs', ['created_at']),
    ('ix_alerts_status', 'alerts', ['status']),
    ('ix_alerts_node_id', 'alerts', ['node_id']),
    ('ix_alerts_last_seen', 'alerts', ['last_seen']),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
