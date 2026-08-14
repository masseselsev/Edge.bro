"""Add backup_history.compressed_size

Revision ID: e4a1c93b7d20
Revises: d7b2f4e91a35
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a1c93b7d20'
down_revision: Union[str, None] = 'd7b2f4e91a35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable with no backfill, deliberately: the value comes from the borg
    # JSON of the run that created the archive, and that output is not kept.
    # It cannot be recovered for existing rows without re-reading every archive
    # out of the repository, so those keep the old estimate and rows written
    # from here on carry the real figure.
    op.add_column(
        'backup_history',
        sa.Column('compressed_size', sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('backup_history', 'compressed_size')
