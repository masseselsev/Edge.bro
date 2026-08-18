"""bootstrap.yml's known_hosts step, keyed per orchestrator host:port.

It used to unconditionally wipe the node's known_hosts and rescan just the
current orchestrator_ip, on the assumption that a node only ever talks to one
orchestrator. A node enrolled with two orchestrators would have the second
one's bootstrap erase the first one's entry. Exercises the exact shell
Ansible would run for this step (see tests/_playbook_raw.py) — everything
else in that task (creating the borg system user) needs root and is
unrelated to what's under test here, so only the known_hosts sub-block is
sliced out and run standalone.
"""
import os
import subprocess

import pytest

from _playbook_raw import load_raw_task, render

#: Stands in for the real ssh-keyscan: the orchestrator IPs used in these
#: tests are not real listening hosts, so the shell snippet's own call to
#: ssh-keyscan is pointed at this instead via PATH, exactly the way one would
#: stub out a network dependency for a shell script under test.
FAKE_SSH_KEYSCAN = """#!/bin/sh
port=""
host=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p) port="$2"; shift 2 ;;
    -t) shift 2 ;;
    *) host="$1"; shift ;;
  esac
done
if [ -n "$port" ]; then
  echo "[$host]:$port ssh-ed25519 FAKEKEYBLOB$port"
else
  echo "$host ssh-ed25519 FAKEKEYBLOBDEFAULT"
fi
"""


def has_ssh_keygen() -> bool:
    try:
        subprocess.run(["ssh-keygen", "-V"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_ssh_keygen = pytest.mark.skipif(
    not has_ssh_keygen(), reason="ssh-keygen not available in this environment"
)


@pytest.fixture
def fake_bin(tmp_path_factory):
    bin_dir = tmp_path_factory.mktemp("fakebin")
    script = bin_dir / "ssh-keyscan"
    script.write_text(FAKE_SSH_KEYSCAN)
    script.chmod(0o755)
    return str(bin_dir)


def _known_hosts_snippet() -> str:
    raw = load_raw_task("bootstrap.yml", "Create borg system user and generate SSH key")
    start = raw.index("KH=/home/borg/.ssh/known_hosts")
    end = raw.index("if [ ! -f /home/borg/.ssh/id_ed25519 ]")
    return raw[start:end]


def _run_known_hosts_step(tmp_path, fake_bin, orchestrator_ip: str, borg_ssh_port: int = 12345) -> str:
    rendered = render(
        _known_hosts_snippet(), orchestrator_ip=orchestrator_ip, borg_ssh_port=borg_ssh_port
    )
    rendered = rendered.replace("/home/borg/.ssh/known_hosts", str(tmp_path / "known_hosts"))

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", rendered], capture_output=True, text=True, timeout=10, env=env
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "known_hosts").read_text()


@requires_ssh_keygen
def test_a_second_orchestrators_bootstrap_keeps_the_first_ones_entry(tmp_path, fake_bin):
    content_a = _run_known_hosts_step(tmp_path, fake_bin, "203.0.113.10")
    assert "203.0.113.10" in content_a

    content_b = _run_known_hosts_step(tmp_path, fake_bin, "203.0.113.20")
    assert "203.0.113.10" in content_b, (
        "orchestrator B's bootstrap dropped orchestrator A's known_hosts entry"
    )
    assert "203.0.113.20" in content_b


@requires_ssh_keygen
def test_rebootstrapping_the_same_orchestrator_does_not_accumulate_duplicates(tmp_path, fake_bin):
    content_1 = _run_known_hosts_step(tmp_path, fake_bin, "203.0.113.10")
    # One entry for the borg_ssh_port scan, one for the bare default-port scan.
    assert len([l for l in content_1.splitlines() if "203.0.113.10" in l]) == 2

    content_2 = _run_known_hosts_step(tmp_path, fake_bin, "203.0.113.10")
    lines_2 = [l for l in content_2.splitlines() if "203.0.113.10" in l]
    assert len(lines_2) == 2, "re-bootstrapping the same orchestrator duplicated its entry"
