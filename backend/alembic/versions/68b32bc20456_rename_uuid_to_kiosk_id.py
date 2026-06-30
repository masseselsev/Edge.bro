"""rename_uuid_to_kiosk_id

Revision ID: 68b32bc20456
Revises: 0d0fc7e924b8
Create Date: 2026-06-30 18:18:07.848494

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68b32bc20456'
down_revision: Union[str, None] = '0d0fc7e924b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('kiosks', 'uuid', new_column_name='kiosk_id')


def downgrade() -> None:
    op.alter_column('kiosks', 'kiosk_id', new_column_name='uuid')
