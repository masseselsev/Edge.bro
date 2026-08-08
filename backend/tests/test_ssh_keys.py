import pytest

from core import ssh_keys

# Throwaway ed25519 public keys used as fixtures.
KEY_A = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIBP7VZ2m3vI0k1V3sK1vJ8xk5cQ0hE9jL2mN4pR6tU8w"
)
KEY_B = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIHc9Kx1LmQ7bR4tY6uI8oP2aS5dF7gH0jK3lZ9xC1vN4"
)


def test_parse_bare_key():
    entry = ssh_keys.parse_line(KEY_A)
    assert entry is not None
    assert entry.keytype == "ssh-ed25519"
    assert entry.blob == KEY_A.split()[1]
    assert entry.comment is None
    assert entry.options is None


def test_parse_key_with_comment():
    entry = ssh_keys.parse_line(f"{KEY_A} edge-bro-node-7")
    assert entry.comment == "edge-bro-node-7"
    assert entry.tag == "edge-bro-node-7"


def test_parse_comment_containing_spaces():
    entry = ssh_keys.parse_line(f"{KEY_A} some admin laptop")
    assert entry.comment == "some admin laptop"
    assert entry.tag is None


def test_parse_line_with_forced_command_options():
    line = f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A} edge-bro-node-7"
    entry = ssh_keys.parse_line(line)
    assert entry is not None
    assert entry.options == ssh_keys.BORG_SERVE_OPTIONS
    assert entry.keytype == "ssh-ed25519"
    assert entry.blob == KEY_A.split()[1]
    assert entry.comment == "edge-bro-node-7"


@pytest.mark.parametrize("line", ["", "   ", "# a comment", "garbage", "ssh-ed25519"])
def test_parse_line_rejects_non_keys(line):
    assert ssh_keys.parse_line(line) is None


def test_fingerprint_is_stable_and_distinguishes_keys():
    fp_a = ssh_keys.fingerprint(KEY_A)
    assert fp_a.startswith("SHA256:")
    assert not fp_a.endswith("=")
    assert fp_a == ssh_keys.fingerprint(f"{KEY_A} a-different-comment")
    assert fp_a != ssh_keys.fingerprint(KEY_B)


def test_fingerprint_matches_ssh_keygen(tmp_path):
    """Cross-check our pure-Python fingerprint against the real ssh-keygen."""
    import shutil
    import subprocess

    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not available")
    key_file = tmp_path / "k.pub"
    key_file.write_text(KEY_A + "\n")
    out = subprocess.run(
        ["ssh-keygen", "-lf", str(key_file)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert ssh_keys.fingerprint(KEY_A) in out


def test_tag_helpers():
    assert ssh_keys.node_tag(7) == "edge-bro-node-7"
    assert ssh_keys.kiosk_tag("abc-123") == "edge-bro-kiosk-abc-123"


def _auth_file(tmp_path):
    path = tmp_path / "authorized_keys"
    path.write_text("")
    return str(path)


def test_authorize_adds_tagged_entry(tmp_path):
    path = _auth_file(tmp_path)
    action = ssh_keys.authorize(
        path, KEY_A, options=ssh_keys.BORG_SERVE_OPTIONS, tag=ssh_keys.node_tag(7)
    )
    assert action == ssh_keys.Action.ADDED
    entries = ssh_keys.list_entries(path)
    assert len(entries) == 1
    assert entries[0].tag == "edge-bro-node-7"
    assert entries[0].options == ssh_keys.BORG_SERVE_OPTIONS


def test_authorize_is_idempotent(tmp_path):
    path = _auth_file(tmp_path)
    tag = ssh_keys.node_tag(7)
    ssh_keys.authorize(path, KEY_A, options=ssh_keys.BORG_SERVE_OPTIONS, tag=tag)
    action = ssh_keys.authorize(path, KEY_A, options=ssh_keys.BORG_SERVE_OPTIONS, tag=tag)
    assert action == ssh_keys.Action.SKIPPED
    assert len(ssh_keys.list_entries(path)) == 1


def test_authorize_rewrites_legacy_untagged_entry_in_place(tmp_path):
    """A pre-existing untagged entry for the same key is re-tagged, not duplicated."""
    path = _auth_file(tmp_path)
    with open(path, "w") as handle:
        handle.write(f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A}\n")
    action = ssh_keys.authorize(
        path, KEY_A, options=ssh_keys.BORG_SERVE_OPTIONS, tag=ssh_keys.node_tag(7)
    )
    assert action == ssh_keys.Action.REWRITTEN
    entries = ssh_keys.list_entries(path)
    assert len(entries) == 1
    assert entries[0].tag == "edge-bro-node-7"


def test_authorize_preserves_foreign_entries(tmp_path):
    path = _auth_file(tmp_path)
    with open(path, "w") as handle:
        handle.write(f"{KEY_B} admin@laptop\n")
    ssh_keys.authorize(path, KEY_A, tag=ssh_keys.node_tag(7))
    entries = ssh_keys.list_entries(path)
    assert len(entries) == 2
    assert any(e.comment == "admin@laptop" for e in entries)


def test_revoke_matches_by_fingerprint_despite_comment_drift(tmp_path):
    path = _auth_file(tmp_path)
    ssh_keys.authorize(path, KEY_A, tag=ssh_keys.node_tag(7))
    action = ssh_keys.revoke(path, f"{KEY_A} some-other-comment")
    assert action == ssh_keys.Action.REMOVED
    assert ssh_keys.list_entries(path) == []


def test_revoke_accepts_a_bare_fingerprint(tmp_path):
    path = _auth_file(tmp_path)
    ssh_keys.authorize(path, KEY_A, tag=ssh_keys.node_tag(7))
    assert ssh_keys.revoke(path, ssh_keys.fingerprint(KEY_A)) == ssh_keys.Action.REMOVED


def test_revoke_missing_key_is_not_an_error(tmp_path):
    path = _auth_file(tmp_path)
    assert ssh_keys.revoke(path, KEY_A) == ssh_keys.Action.NOT_FOUND


def test_backup_written_before_mutation(tmp_path):
    import os
    path = _auth_file(tmp_path)
    ssh_keys.authorize(path, KEY_A, tag=ssh_keys.node_tag(7))
    backups = [n for n in os.listdir(tmp_path) if n.startswith("authorized_keys.bak.")]
    assert len(backups) == 1


def test_skipped_authorize_writes_no_backup(tmp_path):
    """A no-op must not churn backup files on every scheduled run."""
    import os
    path = _auth_file(tmp_path)
    tag = ssh_keys.node_tag(7)
    ssh_keys.authorize(path, KEY_A, tag=tag)
    before = len(os.listdir(tmp_path))
    ssh_keys.authorize(path, KEY_A, tag=tag)
    assert len(os.listdir(tmp_path)) == before


def test_list_entries_on_missing_file(tmp_path):
    assert ssh_keys.list_entries(str(tmp_path / "nope")) == []


def test_classify_tagged_and_known_is_matched():
    entry = ssh_keys.parse_line(f"{KEY_A} {ssh_keys.node_tag(7)}")
    result, _ = ssh_keys.classify(entry, {ssh_keys.fingerprint(KEY_A)})
    assert result == ssh_keys.Classification.OURS_MATCHED


def test_classify_tagged_and_unknown_is_orphaned():
    entry = ssh_keys.parse_line(f"{KEY_A} {ssh_keys.node_tag(7)}")
    result, reason = ssh_keys.classify(entry, set())
    assert result == ssh_keys.Classification.OURS_ORPHANED
    assert reason


def test_classify_untagged_borg_serve_entry_is_legacy_never_orphaned():
    """The forced command is strong evidence but reproducible by hand, so an
    untagged entry must never become eligible for automatic deletion."""
    entry = ssh_keys.parse_line(f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A}")
    result, _ = ssh_keys.classify(entry, set())
    assert result == ssh_keys.Classification.OURS_LEGACY


def test_classify_foreign_key_is_unknown():
    entry = ssh_keys.parse_line(f"{KEY_B} admin@laptop")
    result, _ = ssh_keys.classify(entry, set())
    assert result == ssh_keys.Classification.UNKNOWN


def test_classify_foreign_key_is_unknown_even_if_fingerprint_known():
    """Membership in OURS_* requires our marker, not merely a familiar key."""
    entry = ssh_keys.parse_line(f"{KEY_B} admin@laptop")
    result, _ = ssh_keys.classify(entry, {ssh_keys.fingerprint(KEY_B)})
    assert result == ssh_keys.Classification.UNKNOWN


def test_classification_matrix_over_a_realistic_file(tmp_path):
    path = tmp_path / "authorized_keys"
    path.write_text(
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A} {ssh_keys.node_tag(7)}\n"
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_B} {ssh_keys.node_tag(99)}\n"
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A}\n"
        f"{KEY_B} admin@laptop\n"
    )
    known = {ssh_keys.fingerprint(KEY_A)}
    results = [ssh_keys.classify(e, known)[0] for e in ssh_keys.list_entries(str(path))]
    assert results == [
        ssh_keys.Classification.OURS_MATCHED,
        ssh_keys.Classification.OURS_ORPHANED,
        ssh_keys.Classification.OURS_LEGACY,
        ssh_keys.Classification.UNKNOWN,
    ]


def test_reimage_replaces_the_stale_grant(tmp_path):
    """A node that comes back with a new key must leave exactly one entry."""
    path = _auth_file(tmp_path)
    tag = ssh_keys.node_tag(7)
    ssh_keys.authorize(path, KEY_A, options=ssh_keys.BORG_SERVE_OPTIONS, tag=tag)

    # Node is re-imaged and now presents KEY_B.
    ssh_keys.revoke(path, KEY_A)
    ssh_keys.authorize(path, KEY_B, options=ssh_keys.BORG_SERVE_OPTIONS, tag=tag)

    entries = ssh_keys.list_entries(path)
    assert len(entries) == 1
    assert entries[0].fingerprint == ssh_keys.fingerprint(KEY_B)


def test_normalized_pubkey_strips_the_comment():
    entry = ssh_keys.parse_line(f"{KEY_A} root@a1b2c3d4e5f6")
    assert f"{entry.keytype} {entry.blob}" == KEY_A


# The bootstrap playbook filters the node's authorized_keys with this awk
# program. Exercising it directly is far cheaper than debugging it inside YAML.
AWK_FILTER = r"""
    { n = split($0, f, /[ \t]+/)
      hasblob = 0
      for (i = 1; i <= n; i++) if (f[i] == blob) hasblob = 1
      if (n >= 2 && f[n] == tag) next
      if (hasblob && f[1] ~ /^(ssh-|ecdsa-|sk-)/) next
      print $0
    }
"""


def _run_filter(content, blob, tag="edge-bro-orchestrator"):
    import shutil
    import subprocess

    if shutil.which("awk") is None:
        pytest.skip("awk not available")
    return subprocess.run(
        ["awk", "-v", f"blob={blob}", "-v", f"tag={tag}", AWK_FILTER],
        input=content, capture_output=True, text=True, check=True,
    ).stdout


def test_node_filter_drops_stale_tagged_orchestrator_keys():
    blob_a = KEY_A.split()[1]
    content = (
        f"{KEY_B} edge-bro-orchestrator\n"      # stale rotation leftover, tagged
        f"{KEY_A} edge-bro-orchestrator\n"      # current key, tagged
        f"{KEY_B} admin@laptop\n"               # foreign, must survive
    )
    out = _run_filter(content, blob_a)
    assert "admin@laptop" in out
    assert "edge-bro-orchestrator" not in out


def test_node_filter_drops_untagged_duplicate_of_current_key():
    """The legacy untagged copy is removed so the tagged one can replace it."""
    blob_a = KEY_A.split()[1]
    out = _run_filter(f"{KEY_A}\n{KEY_B} admin@laptop\n", blob_a)
    assert blob_a not in out
    assert "admin@laptop" in out


def test_node_filter_never_touches_foreign_keys():
    blob_a = KEY_A.split()[1]
    content = f"{KEY_B} admin@laptop\nfrom=\"10.0.0.1\" {KEY_B} ops@jump\n"
    out = _run_filter(content, blob_a)
    assert out.count(KEY_B.split()[1]) == 2


def test_node_filter_keeps_a_restricted_foreign_line_sharing_our_blob():
    """If someone authorized the orchestrator key with their own options, that
    line must survive rather than be collapsed into our unrestricted entry."""
    blob_a = KEY_A.split()[1]
    content = f'from="10.0.0.1" {KEY_A} ops@jump\n{KEY_A}\n'
    out = _run_filter(content, blob_a)
    assert 'from="10.0.0.1"' in out
    assert out.strip().count("\n") == 0  # only that one line survives
