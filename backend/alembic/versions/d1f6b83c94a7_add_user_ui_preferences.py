"""add user ui preferences

Revision ID: d1f6b83c94a7
Revises: c8e4a1f7b230
Create Date: 2026-08-12 11:02:47.913204

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1f6b83c94a7'
down_revision: Union[str, None] = 'c8e4a1f7b230'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable with no default: an absent value means "this user has never
    # expressed a preference", which the API answers with the built-in
    # defaults rather than persisting a copy of them per user.
    op.add_column('users', sa.Column('ui_preferences', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'ui_preferences')
