"""add orchestrator_id to settings

Revision ID: ec8383c7a149
Revises: e4a1c93b7d20
Create Date: 2026-08-18 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec8383c7a149'
down_revision: Union[str, None] = 'e4a1c93b7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Identifies this orchestrator install when a node is enrolled with more
    # than one (an on-site and an off-site server, say) — see
    # core.ssh_keys.orchestrator_tag. `settings` is a singleton table, so a
    # baked-in default here only ever backfills the one existing row.
    op.add_column(
        'settings',
        sa.Column('orchestrator_id', sa.String(), server_default=uuid.uuid4().hex, nullable=False),
    )


def downgrade() -> None:
    op.drop_column('settings', 'orchestrator_id')
