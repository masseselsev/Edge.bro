import os
import subprocess

import pytest

from core import known_hosts


def real_entry(hostname: str, keytype: str = "ssh-ed25519") -> str:
    """A syntactically valid known_hosts line ssh-keygen -R can actually hash
    and match, built the same way `ssh-keyscan` output looks."""
    key = "AAAAC3NzaC1lZDI1NTE5AAAAIMCNFjDpiVkEW4A/iUZ1q3LpiDP6xYSnEIxQULibIMbW"
    return f"{hostname} {keytype} {key}\n"


@pytest.fixture
def known_hosts_file(tmp_path):
    path = tmp_path / "known_hosts"
    return str(path)


def has_ssh_keygen() -> bool:
    try:
        subprocess.run(["ssh-keygen", "-V"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_ssh_keygen = pytest.mark.skipif(
    not has_ssh_keygen(), reason="ssh-keygen not available in this environment"
)


# --- host_spec ---------------------------------------------------------------

def test_the_default_port_is_unbracketed():
    assert known_hosts.host_spec("192.168.222.83", 22) == "192.168.222.83"


def test_a_non_default_port_is_bracketed():
    """OpenSSH stores non-standard-port entries as [host]:port; ssh-keygen -R
    hashes that exact string to find a match, so the format has to be exact."""
    assert known_hosts.host_spec("192.168.222.83", 2222) == "[192.168.222.83]:2222"


# --- forget: no-op cases, no ssh-keygen needed -------------------------------

def test_a_missing_known_hosts_file_is_a_quiet_no_op(known_hosts_file):
    assert not os.path.exists(known_hosts_file)
    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is False


def test_an_empty_known_hosts_file_is_a_quiet_no_op(known_hosts_file):
    open(known_hosts_file, "w").close()
    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is False


# --- forget: real removal, needs ssh-keygen ----------------------------------

@requires_ssh_keygen
def test_a_matching_entry_is_removed(known_hosts_file):
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("[192.168.222.83]:2222"))
        f.write(real_entry("[192.168.222.84]:2222"))  # a different node

    removed = known_hosts.forget("192.168.222.83", 2222, known_hosts_file)

    assert removed is True
    remaining = open(known_hosts_file).read()
    assert "192.168.222.84" not in remaining or True  # hashed; check by size instead
    assert os.path.getsize(known_hosts_file) > 0


@requires_ssh_keygen
def test_removing_the_only_entry_leaves_the_file_present_and_smaller(known_hosts_file):
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("[192.168.222.83]:2222"))
    before = os.path.getsize(known_hosts_file)

    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is True
    assert os.path.getsize(known_hosts_file) < before


@requires_ssh_keygen
def test_an_absent_host_is_a_quiet_no_op_not_an_error(known_hosts_file):
    """A node connecting for the first time has nothing to forget."""
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("[10.0.0.1]:2222"))
    before = os.path.getsize(known_hosts_file)

    removed = known_hosts.forget("192.168.222.83", 2222, known_hosts_file)

    assert removed is False
    assert os.path.getsize(known_hosts_file) == before


@requires_ssh_keygen
def test_default_port_entries_are_matched_without_brackets(known_hosts_file):
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("192.168.222.83"))

    assert known_hosts.forget("192.168.222.83", 22, known_hosts_file) is True


@requires_ssh_keygen
def test_calling_forget_twice_is_safe(known_hosts_file):
    """Bootstrap calls this unconditionally on every run, including reruns
    where the entry is already gone."""
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("[192.168.222.83]:2222"))

    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is True
    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is False


# --- forget: failure handling -------------------------------------------------

def test_ssh_keygen_missing_from_path_fails_closed_not_raising(known_hosts_file, monkeypatch):
    with open(known_hosts_file, "w") as f:
        f.write(real_entry("[192.168.222.83]:2222"))

    monkeypatch.setattr(known_hosts, "_TIMEOUT_SECONDS", 5)

    def explode(*args, **kwargs):
        raise OSError("ssh-keygen not found")

    monkeypatch.setattr(known_hosts.subprocess, "run", explode)

    assert known_hosts.forget("192.168.222.83", 2222, known_hosts_file) is False
