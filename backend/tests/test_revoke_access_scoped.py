"""revoke_access.yml removes only the calling orchestrator's own tagged key.

It used to match any tag starting `edge-bro-`, which was harmless while every
orchestrator shared one fixed tag but became a real cross-orchestrator hazard
the moment bootstrap.yml started tagging per install: decommissioning a node
from orchestrator A must never also strip orchestrator B's still-live grant.
"""
import subprocess

import pytest

from _playbook_raw import load_raw_task, render


def has_bash_and_awk() -> bool:
    try:
        subprocess.run(["bash", "-c", "awk 'BEGIN{}'"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_shell = pytest.mark.skipif(
    not has_bash_and_awk(), reason="bash/awk not available in this environment"
)


def _run_revoke(tmp_path, orchestrator_tag: str) -> str:
    raw = load_raw_task(
        "revoke_access.yml", "Remove edge-bro tagged keys from the node authorized_keys"
    )
    rendered = render(raw, orchestrator_tag=orchestrator_tag)
    rendered = rendered.replace("/root/.ssh/authorized_keys", str(tmp_path / "authorized_keys"))

    result = subprocess.run(
        ["bash", "-c", rendered], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "authorized_keys").read_text()


@requires_shell
def test_revoking_one_orchestrator_leaves_the_others_key_in_place(tmp_path):
    auth = tmp_path / "authorized_keys"
    auth.write_text(
        "ssh-ed25519 AAAA_KEY_A edge-bro-orchestrator-aaaaaaaa\n"
        "ssh-ed25519 AAAA_KEY_B edge-bro-orchestrator-bbbbbbbb\n"
    )

    content = _run_revoke(tmp_path, "edge-bro-orchestrator-aaaaaaaa")

    assert "edge-bro-orchestrator-aaaaaaaa" not in content
    assert "edge-bro-orchestrator-bbbbbbbb" in content


@requires_shell
def test_revoking_also_removes_a_pre_migration_legacy_tagged_key(tmp_path):
    """A node bootstrapped before per-orchestrator tagging existed still
    carries the old fixed `edge-bro-orchestrator` tag until it happens to be
    re-bootstrapped. Deleting it before that must still remove its key —
    safe to do unconditionally, since multi-orchestrator support did not
    exist when that tag was written, so it can only belong to the one
    orchestrator now decommissioning the node."""
    auth = tmp_path / "authorized_keys"
    auth.write_text("ssh-ed25519 AAAA_KEY_LEGACY edge-bro-orchestrator\n")

    content = _run_revoke(tmp_path, "edge-bro-orchestrator-aaaaaaaa")

    assert "edge-bro-orchestrator" not in content


@requires_shell
def test_a_missing_authorized_keys_file_is_a_quiet_no_op(tmp_path):
    raw = load_raw_task(
        "revoke_access.yml", "Remove edge-bro tagged keys from the node authorized_keys"
    )
    rendered = render(raw, orchestrator_tag="edge-bro-orchestrator-aaaaaaaa")
    rendered = rendered.replace("/root/.ssh/authorized_keys", str(tmp_path / "authorized_keys"))

    result = subprocess.run(["bash", "-c", rendered], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "REVOKE_RESULT: no authorized_keys file" in result.stdout
