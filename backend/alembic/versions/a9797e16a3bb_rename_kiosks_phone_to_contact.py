"""rename kiosks phone to contact

Revision ID: a9797e16a3bb
Revises: e4175f219b5b
Create Date: 2026-06-25 01:01:51.438867

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9797e16a3bb'
down_revision: Union[str, None] = 'e4175f219b5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('kiosks', 'phone', new_column_name='contact')


def downgrade() -> None:
    op.alter_column('kiosks', 'contact', new_column_name='phone')
