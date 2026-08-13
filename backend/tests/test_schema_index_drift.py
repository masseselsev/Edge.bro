"""Guards the performance indexes against silent removal.

These indexes were originally created by migration only, with no matching
declaration in models.py. That is a quiet trap: `alembic revision
--autogenerate` compares the database against Base.metadata, sees indexes the
metadata does not know about, and emits DROP INDEX for every one of them. The
next person to autogenerate a migration would have removed the fleet's hot-path
indexes without ever intending to.

Declaring them in models.py fixes that. This test keeps them declared.
"""
import models


# name -> (table, ordered columns). Each of these is created by a migration and
# must stay visible to Base.metadata so autogenerate never proposes dropping it.
REQUIRED_INDEXES = {
    'ix_nodes_group_id': ('nodes', ('group_id',)),
    'ix_backup_history_timestamp': ('backup_history', ('timestamp',)),
    'ix_backup_history_node_id_status': ('backup_history', ('node_id', 'status')),
    'ix_task_log_node_id_created_at': ('task_logs', ('node_id', 'created_at')),
    'ix_telemetry_rollups_node_bucket': ('telemetry_rollups', ('node_id', 'bucket_start')),
    'ix_thermal_fits_node_window': ('thermal_fits', ('node_id', 'window_start')),
    'ix_smart_snapshots_node_captured': ('smart_snapshots', ('node_id', 'captured_at')),
    # Added by c1d47e9b8a02 for columns filtered or ordered on by code that
    # runs on a timer or on every request.
    'ix_nodes_status': ('nodes', ('status',)),
    'ix_task_logs_status': ('task_logs', ('status',)),
    'ix_backup_history_status': ('backup_history', ('status',)),
    'ix_system_logs_created_at': ('system_logs', ('created_at',)),
    'ix_audit_logs_created_at': ('audit_logs', ('created_at',)),
    'ix_alerts_status': ('alerts', ('status',)),
    'ix_alerts_node_id': ('alerts', ('node_id',)),
    'ix_alerts_last_seen': ('alerts', ('last_seen',)),
}


def _declared_indexes():
    found = {}
    for table in models.Base.metadata.tables.values():
        for index in table.indexes:
            found[index.name] = (table.name, tuple(c.name for c in index.columns))
    return found


def test_migration_created_indexes_are_declared_in_models():
    declared = _declared_indexes()
    missing = sorted(name for name in REQUIRED_INDEXES if name not in declared)
    assert not missing, (
        "These indexes exist in the database but not in Base.metadata, so "
        "`alembic revision --autogenerate` would emit DROP INDEX for them: "
        f"{missing}"
    )


def test_declared_indexes_cover_the_expected_columns():
    declared = _declared_indexes()
    for name, expected in REQUIRED_INDEXES.items():
        assert declared[name] == expected, (
            f"{name} covers {declared[name]}, expected {expected}. "
            "Column order matters: a composite index only serves queries that "
            "filter on a leading subset of its columns."
        )
