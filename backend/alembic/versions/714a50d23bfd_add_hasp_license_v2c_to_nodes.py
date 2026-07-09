"""add_hasp_license_v2c_to_nodes

Revision ID: 714a50d23bfd
Revises: 5cba392850b4
Create Date: 2026-07-09 17:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '714a50d23bfd'
down_revision: Union[str, None] = '5cba392850b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nodes', sa.Column('hasp_license_v2c', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('nodes', 'hasp_license_v2c')
