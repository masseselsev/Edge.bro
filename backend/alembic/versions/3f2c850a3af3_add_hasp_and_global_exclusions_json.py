"""add_hasp_and_global_exclusions_json

Revision ID: 3f2c850a3af3
Revises: 1a81d84adb9f
Create Date: 2026-07-08 11:59:48.510048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f2c850a3af3'
down_revision: Union[str, None] = '1a81d84adb9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


import json

def upgrade() -> None:
    # 1. Add hasp_runtime_version to nodes
    op.add_column('nodes', sa.Column('hasp_runtime_version', sa.String(), nullable=True))
    
    # 2. Convert global_exclusions in settings from TEXT to JSON
    # Fetch current values, split by comma, and convert to JSON array
    connection = op.get_bind()
    results = connection.execute(sa.text("SELECT id, global_exclusions FROM settings")).fetchall()
    
    # Alter column type to JSON (PostgreSQL syntax)
    op.execute("ALTER TABLE settings ALTER COLUMN global_exclusions TYPE JSON USING '[]'::json")
    
    for row in results:
        row_id = row[0]
        old_exclusions = row[1]
        if old_exclusions:
            exclusions_list = [x.strip() for x in old_exclusions.split(",") if x.strip()]
        else:
            exclusions_list = []
        exclusions_json = json.dumps(exclusions_list)
        connection.execute(
            sa.text("UPDATE settings SET global_exclusions = :exclusions WHERE id = :id"),
            {"exclusions": exclusions_json, "id": row_id}
        )


def downgrade() -> None:
    # 1. Drop hasp_runtime_version
    op.drop_column('nodes', 'hasp_runtime_version')
    
    # 2. Convert global_exclusions back to TEXT
    connection = op.get_bind()
    results = connection.execute(sa.text("SELECT id, global_exclusions FROM settings")).fetchall()
    
    op.execute("ALTER TABLE settings ALTER COLUMN global_exclusions TYPE TEXT USING ''")
    
    for row in results:
        row_id = row[0]
        exclusions_json = row[1]
        try:
            exclusions_list = json.loads(exclusions_json) if exclusions_json else []
            old_exclusions = ",".join(exclusions_list)
        except Exception:
            old_exclusions = ""
        connection.execute(
            sa.text("UPDATE settings SET global_exclusions = :exclusions WHERE id = :id"),
            {"exclusions": old_exclusions, "id": row_id}
        )

