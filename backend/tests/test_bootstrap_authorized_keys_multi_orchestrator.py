"""bootstrap.yml's authorized_keys step, tagged per orchestrator.

Before this, every orchestrator wrote the same fixed `edge-bro-orchestrator`
tag, and the awk script removed any existing line carrying that tag —
regardless of which key it belonged to — before appending its own. A node
enrolled with two orchestrators would have the second one's bootstrap
silently strip the first one's grant. Exercises the exact shell Ansible would
run (see tests/_playbook_raw.py), not a hand-copied approximation of it.
"""
import subprocess

import pytest

from _playbook_raw import load_raw_task, render

PUBKEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMCNFjDpiVkEW4A/iUZ1q3LpiDP6xYSnEIxQULibIMbW"
PUBKEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINEWDIFFERENTKEYVALUEFORB0000000000000"


def has_bash_and_awk() -> bool:
    try:
        subprocess.run(["bash", "-c", "awk 'BEGIN{}'"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_shell = pytest.mark.skipif(
    not has_bash_and_awk(), reason="bash/awk not available in this environment"
)


def _run_authorize(tmp_path, pubkey: str, tag: str) -> str:
    """Runs the real bootstrap.yml authorized_keys task against a temp
    /root/.ssh, and returns the resulting authorized_keys content."""
    raw = load_raw_task("bootstrap.yml", "Authorize orchestrator SSH public key on edge node")
    rendered = render(raw, orchestrator_ssh_pub_key=pubkey, orchestrator_tag=tag)
    # The task hardcodes /root/.ssh; redirect it to a throwaway directory so
    # the test never touches the real root's SSH config.
    rendered = rendered.replace("/root/.ssh", str(tmp_path))

    result = subprocess.run(
        ["bash", "-c", rendered], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "authorized_keys").read_text()


@requires_shell
def test_a_second_orchestrator_does_not_remove_the_first_ones_key(tmp_path):
    content_a = _run_authorize(tmp_path, PUBKEY_A, "edge-bro-orchestrator-aaaaaaaa")
    assert "edge-bro-orchestrator-aaaaaaaa" in content_a

    content_b = _run_authorize(tmp_path, PUBKEY_B, "edge-bro-orchestrator-bbbbbbbb")
    assert "edge-bro-orchestrator-aaaaaaaa" in content_b, (
        "orchestrator B's bootstrap removed orchestrator A's tagged key"
    )
    assert "edge-bro-orchestrator-bbbbbbbb" in content_b
    assert content_b.count(PUBKEY_A.split()[1]) == 1
    assert content_b.count(PUBKEY_B.split()[1]) == 1


@requires_shell
def test_rebootstrapping_the_same_orchestrator_with_a_rotated_key_replaces_only_its_own(tmp_path):
    content_1 = _run_authorize(tmp_path, PUBKEY_A, "edge-bro-orchestrator-aaaaaaaa")
    assert PUBKEY_A.split()[1] in content_1

    # Same orchestrator, new key (e.g. re-image of the orchestrator itself).
    content_2 = _run_authorize(tmp_path, PUBKEY_B, "edge-bro-orchestrator-aaaaaaaa")
    assert PUBKEY_A.split()[1] not in content_2, "old key for the same orchestrator was not replaced"
    assert PUBKEY_B.split()[1] in content_2
    assert content_2.count("edge-bro-orchestrator-aaaaaaaa") == 1
