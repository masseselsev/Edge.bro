"""add_payload_outdated_to_kiosks

Revision ID: 4b1e39348142
Revises: f8c08968db77
Create Date: 2026-07-16 13:53:59.982426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b1e39348142'
down_revision: Union[str, None] = 'f8c08968db77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('kiosks', sa.Column('payload_outdated', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('kiosks', 'payload_outdated')
