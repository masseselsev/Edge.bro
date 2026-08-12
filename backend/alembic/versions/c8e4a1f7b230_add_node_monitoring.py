"""add node monitoring: telemetry rollups, thermal fits, smart snapshots

Revision ID: c8e4a1f7b230
Revises: b7d31c9a4e02
Create Date: 2026-08-12 09:41:18.552104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e4a1f7b230'
down_revision: Union[str, None] = 'b7d31c9a4e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fleet-wide monitoring defaults. Non-null with server defaults so existing
    # rows get sensible values without a backfill pass.
    op.add_column('settings', sa.Column('monitoring_enabled', sa.Boolean(),
                                        nullable=False, server_default=sa.true()))
    op.add_column('settings', sa.Column('monitoring_interval_days', sa.Integer(),
                                        nullable=False, server_default='30'))
    op.add_column('settings', sa.Column('smart_temp_warn_c', sa.Integer(),
                                        nullable=False, server_default='60'))
    op.add_column('settings', sa.Column('smart_temp_crit_c', sa.Integer(),
                                        nullable=False, server_default='70'))
    op.add_column('settings', sa.Column('telemetry_retention_days', sa.Integer(),
                                        nullable=False, server_default='90'))

    # Per-node overrides. Nullable throughout: NULL means inherit, which is
    # deliberately distinct from an explicit value equal to the current global.
    op.add_column('nodes', sa.Column('monitoring_enabled', sa.Boolean(), nullable=True))
    op.add_column('nodes', sa.Column('monitoring_interval_days', sa.Integer(), nullable=True))
    op.add_column('nodes', sa.Column('smart_temp_warn_c', sa.Integer(), nullable=True))
    op.add_column('nodes', sa.Column('smart_temp_crit_c', sa.Integer(), nullable=True))
    op.add_column('nodes', sa.Column('last_harvest_at', sa.DateTime(), nullable=True))
    op.add_column('nodes', sa.Column('monitoring_capabilities', sa.JSON(), nullable=True))

    op.create_table(
        'telemetry_rollups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('bucket_start', sa.DateTime(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column('power_w_mean', sa.Float(), nullable=True),
        sa.Column('power_w_max', sa.Float(), nullable=True),
        sa.Column('cpu_temp_c_mean', sa.Float(), nullable=True),
        sa.Column('cpu_temp_c_max', sa.Float(), nullable=True),
        sa.Column('board_temp_c_mean', sa.Float(), nullable=True),
        sa.Column('ssd_temp_c_mean', sa.Float(), nullable=True),
        sa.Column('cpu_util_mean', sa.Float(), nullable=True),
        sa.Column('io_service_ms_mean', sa.Float(), nullable=True),
        sa.Column('throttled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        # A re-harvest of an overlapping buffer must update the bucket rather
        # than duplicate it.
        sa.UniqueConstraint('node_id', 'bucket_start', name='uq_telemetry_rollup'),
    )
    op.create_index('ix_telemetry_rollups_id', 'telemetry_rollups', ['id'])
    op.create_index('ix_telemetry_rollups_bucket_start', 'telemetry_rollups', ['bucket_start'])
    # Charts always ask for one node over a time range, and the retention
    # sweep always asks for everything before a date.
    op.create_index('ix_telemetry_rollups_node_bucket', 'telemetry_rollups',
                    ['node_id', 'bucket_start'])

    op.create_table(
        'thermal_fits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('window_end', sa.DateTime(), nullable=False),
        sa.Column('rejection', sa.String(), nullable=False),
        sa.Column('n_samples', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('excitation', sa.Float(), nullable=True),
        sa.Column('theta_c_per_w', sa.Float(), nullable=True),
        sa.Column('theta_normalised', sa.Float(), nullable=True),
        sa.Column('tau_seconds', sa.Float(), nullable=True),
        sa.Column('t_ambient_c', sa.Float(), nullable=True),
        sa.Column('mean_temp_c', sa.Float(), nullable=True),
        sa.Column('r_squared', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('node_id', 'window_start', name='uq_thermal_fit_window'),
    )
    op.create_index('ix_thermal_fits_id', 'thermal_fits', ['id'])
    op.create_index('ix_thermal_fits_window_start', 'thermal_fits', ['window_start'])
    # The cohort detector asks for every node's fits inside one window; the
    # per-node trend asks for one node's fits over time.
    op.create_index('ix_thermal_fits_node_window', 'thermal_fits',
                    ['node_id', 'window_start'])

    op.create_table(
        'smart_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('captured_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('device', sa.String(), nullable=False),
        sa.Column('protocol', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('serial', sa.String(), nullable=True),
        sa.Column('firmware', sa.String(), nullable=True),
        sa.Column('health_passed', sa.Boolean(), nullable=True),
        sa.Column('temperature_c', sa.Integer(), nullable=True),
        sa.Column('power_on_hours', sa.Integer(), nullable=True),
        sa.Column('written_bytes', sa.BigInteger(), nullable=True),
        sa.Column('percent_used', sa.Float(), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('grade', sa.String(), nullable=True),
        sa.Column('subscores', sa.JSON(), nullable=True),
        sa.Column('overrides', sa.JSON(), nullable=True),
        sa.Column('advisories', sa.JSON(), nullable=True),
        # The full smartctl report, for the "full statistics of the last
        # query" view. Nulled out on older rows by the retention sweep.
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_smart_snapshots_id', 'smart_snapshots', ['id'])
    op.create_index('ix_smart_snapshots_captured_at', 'smart_snapshots', ['captured_at'])
    op.create_index('ix_smart_snapshots_node_captured', 'smart_snapshots',
                    ['node_id', 'captured_at'])


def downgrade() -> None:
    op.drop_index('ix_smart_snapshots_node_captured', table_name='smart_snapshots')
    op.drop_index('ix_smart_snapshots_captured_at', table_name='smart_snapshots')
    op.drop_index('ix_smart_snapshots_id', table_name='smart_snapshots')
    op.drop_table('smart_snapshots')

    op.drop_index('ix_thermal_fits_node_window', table_name='thermal_fits')
    op.drop_index('ix_thermal_fits_window_start', table_name='thermal_fits')
    op.drop_index('ix_thermal_fits_id', table_name='thermal_fits')
    op.drop_table('thermal_fits')

    op.drop_index('ix_telemetry_rollups_node_bucket', table_name='telemetry_rollups')
    op.drop_index('ix_telemetry_rollups_bucket_start', table_name='telemetry_rollups')
    op.drop_index('ix_telemetry_rollups_id', table_name='telemetry_rollups')
    op.drop_table('telemetry_rollups')

    op.drop_column('nodes', 'monitoring_capabilities')
    op.drop_column('nodes', 'last_harvest_at')
    op.drop_column('nodes', 'smart_temp_crit_c')
    op.drop_column('nodes', 'smart_temp_warn_c')
    op.drop_column('nodes', 'monitoring_interval_days')
    op.drop_column('nodes', 'monitoring_enabled')

    op.drop_column('settings', 'telemetry_retention_days')
    op.drop_column('settings', 'smart_temp_crit_c')
    op.drop_column('settings', 'smart_temp_warn_c')
    op.drop_column('settings', 'monitoring_interval_days')
    op.drop_column('settings', 'monitoring_enabled')
