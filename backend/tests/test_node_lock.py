"""The node-lock atomicity fix: pkill/cleanup, `borg init` and `borg create`
have to run as one locked script, or a second orchestrator's cleanup can
still kill a live backup before it ever tries the lock. See core.node_lock
and the "gap the proposal missed" section of the multi-server backup plan.
"""
from core import node_lock


def test_locked_script_checks_the_lock_before_anything_else():
    script = node_lock.build_locked_remote_script(
        "CLEANUP; ", "INIT; ", 'bash -c "CREATE"'
    )
    lock_index = script.index("flock -n 9")
    assert script.index("CLEANUP") > lock_index
    assert script.index("INIT") > lock_index
    assert script.index("CREATE") > lock_index


def test_locked_script_exits_with_the_busy_code_before_running_anything():
    script = node_lock.build_locked_remote_script(
        "CLEANUP; ", "INIT; ", 'bash -c "CREATE"'
    )
    # The busy branch (everything between `||` and the cleanup step) must
    # short-circuit with the marker and exit code before cleanup ever starts.
    busy_branch = script.split("||", 1)[1].split("CLEANUP", 1)[0]
    assert f"exit {node_lock.LOCK_BUSY_EXIT_CODE}" in busy_branch
    assert node_lock.LOCK_BUSY_MARKER in busy_branch


def test_locked_script_does_not_break_the_outer_ssh_argv_quoting():
    """create_cmd already comes wrapped in its own `bash -c "..."` (see
    backup_tasks.build_borg_create_inner_cmd) — the assembled script must not
    add a second layer of double quotes around the whole thing, which is
    exactly the kind of double-wrap bug core.ssh.borg_rsh's history warns
    about. If it did, the quotes in create_cmd would end up unbalanced."""
    create_cmd = 'bash -c "BORG_PASSPHRASE=\'secret\' borg create ...::archive / "'
    script = node_lock.build_locked_remote_script("CLEANUP; ", "INIT; ", create_cmd)
    # No outer quote wraps the whole script: it starts with `exec`, not `"`.
    assert script.startswith("exec 9>")
    assert not script.startswith('"')
    assert script.count('"') == create_cmd.count('"')


def test_locked_script_uses_the_shared_node_lock_path():
    script = node_lock.build_locked_remote_script("C; ", "I; ", "X")
    assert node_lock.NODE_LOCK_PATH in script
    assert script.count(node_lock.NODE_LOCK_PATH) == 1


def test_create_is_the_last_statement_so_its_exit_code_is_the_scripts():
    """No `set -e` in the script, so bash's own exit status is whatever the
    last command run reported — this only holds if create really is last."""
    script = node_lock.build_locked_remote_script("C; ", "I; ", "LAST_CMD")
    assert script.rstrip().endswith("LAST_CMD")


# --- the regression test the ordering fix hinges on: the old cleanup call ---
# --- must no longer touch the node's processes/cache files at all.       ---

def test_resolve_orchestrator_ip_no_longer_kills_or_deletes_anything():
    """`cleanup_locks_and_resolve_ip` used to pkill and blanket-delete cache
    lock files on the node, unguarded, before every backup — the exact thing
    that could kill a second orchestrator's live backup. That destructive
    work now only ever runs inside the locked script (see
    backup_tasks._transfer_and_record), so the IP-resolution probe itself
    must be read-only again."""
    import inspect
    import backup_tasks

    source = inspect.getsource(backup_tasks.cleanup_locks_and_resolve_ip)
    # Only the docstring may still say the words; the actual remote_cmd must
    # not build a pkill or a destructive find anymore.
    remote_cmd_start = source.index('remote_cmd = (')
    remote_cmd_body = source[remote_cmd_start:source.index(")\n", remote_cmd_start)]
    assert "pkill" not in remote_cmd_body
    assert "-delete" not in remote_cmd_body
