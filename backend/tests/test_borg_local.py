"""Tests for running borg as the repository's owner.

The failure these guard against: a repository whose files are 0600/0700 and
owned by the borg-server user, sitting on storage where root does not get DAC
override (NFS with root_squash). Every local borg call then dies with

    PermissionError: [Errno 13] Permission denied: '<repo>/config'

That exact condition is reproduced below by running borg as a uid that is
neither root nor the owner, which is what a squashed root effectively is.
"""
import os
import shutil
import subprocess

import pytest

from core.borg_local import borg_kwargs, grant_workdir, repo_run_as

NOT_ROOT = os.geteuid() != 0
requires_root = pytest.mark.skipif(NOT_ROOT, reason="needs root to change identity")

OTHER_UID = 4321  # arbitrary uid that owns nothing here


# --- repo_run_as: who should borg be? ---

def test_owned_by_us_means_no_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert repo_run_as(str(repo)) == (None, None)


@requires_root
def test_owned_by_someone_else_returns_that_identity(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.chown(repo, OTHER_UID, OTHER_UID)
    assert repo_run_as(str(repo)) == (OTHER_UID, OTHER_UID)


def test_missing_repo_is_not_fatal(tmp_path):
    """A path that isn't there must not raise — the caller reports it better."""
    assert repo_run_as(str(tmp_path / "nope")) == (None, None)


# --- borg_kwargs: the subprocess plumbing ---

def test_no_identity_change_leaves_call_untouched(tmp_path):
    """Deployments that already work must produce byte-identical calls."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"BORG_PASSPHRASE": "x"}
    assert borg_kwargs(str(repo), env) == {}
    assert env == {"BORG_PASSPHRASE": "x"}, "env must not be touched when nothing changes"


@requires_root
def test_identity_change_sets_user_group_and_writable_base_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.chown(repo, OTHER_UID, OTHER_UID)

    env = {"BORG_PASSPHRASE": "x"}
    kwargs = borg_kwargs(str(repo), env)

    assert kwargs == {"user": OTHER_UID, "group": OTHER_UID, "extra_groups": []}
    # borg writes its cache and per-repo security records under HOME; leaving
    # HOME at /root makes it fail on its own bookkeeping instead of the repo.
    assert env["BORG_BASE_DIR"] == env["HOME"]
    st = os.stat(env["BORG_BASE_DIR"])
    assert st.st_uid == OTHER_UID


@requires_root
def test_env_mutation_is_visible_in_the_same_call(tmp_path):
    """Guards the call shape used everywhere:

        subprocess.run(cmd, env=env, **borg_kwargs(repo_path, env))

    `env=env` is bound before borg_kwargs runs, so this only works because the
    same dict object is mutated rather than copied. Anyone who changes
    borg_kwargs to build a new dict breaks every call site silently.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    os.chown(repo, OTHER_UID, OTHER_UID)

    env = {"PATH": os.environ["PATH"]}
    res = subprocess.run(
        ["sh", "-c", "echo $BORG_BASE_DIR"],
        env=env, capture_output=True, text=True,
        **borg_kwargs(str(repo), env),
    )
    assert res.stdout.strip() == f"/tmp/borg-base-{OTHER_UID}"


@requires_root
def test_grant_workdir_hands_directory_to_the_same_identity(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.chown(repo, OTHER_UID, OTHER_UID)

    work = tmp_path / "work"
    work.mkdir()
    grant_workdir(str(work), str(repo))
    assert os.stat(work).st_uid == OTHER_UID


# --- end to end against a real borg repository ---

def _make_traversable(path):
    """Let any uid walk down to `path`.

    pytest's tmp dirs are 0700/root, so a non-owner could not even reach the
    repository. On a real deployment /data and /data/borg are traversable, and
    it is the repository's own 0600/0700 contents that do the excluding — this
    reproduces that shape rather than the harness's.
    """
    p = str(path)
    while p != "/":
        os.chmod(p, os.stat(p).st_mode | 0o055)
        p = os.path.dirname(p)


@pytest.fixture
def borg_repo(tmp_path):
    """A real borg repository, owned by OTHER_UID with borg's own 0077 modes."""
    if shutil.which("borg") is None:
        pytest.skip("borg not installed")
    _make_traversable(tmp_path)

    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("hello")

    repo = tmp_path / "repo"
    env = {**os.environ, "BORG_PASSPHRASE": "test", "BORG_BASE_DIR": str(tmp_path / "base")}
    subprocess.run(["borg", "init", "--encryption=repokey", str(repo)],
                   env=env, capture_output=True, check=True)
    subprocess.run(["borg", "create", f"{repo}::arch1", str(src)],
                   env=env, capture_output=True, check=True)

    # borg's default umask is 0077, so this is what a real repository looks like.
    assert os.stat(repo / "config").st_mode & 0o077 == 0, "expected owner-only modes"
    return repo


@requires_root
def test_reproduces_the_permission_denied_without_the_fix(borg_repo, tmp_path):
    """Running as a non-owner, non-root uid must fail exactly as reported."""
    for path in (borg_repo, *borg_repo.rglob("*")):
        os.chown(path, OTHER_UID, OTHER_UID)

    base = tmp_path / "foreign_base"
    base.mkdir()
    os.chown(base, 9999, 9999)

    res = subprocess.run(
        ["borg", "list", "--bypass-lock", "--json-lines", f"{borg_repo}::arch1"],
        env={**os.environ, "BORG_PASSPHRASE": "test", "BORG_BASE_DIR": str(base)},
        capture_output=True, text=True,
        user=9999, group=9999, extra_groups=[],
    )
    assert res.returncode != 0
    assert "Permission denied" in res.stderr
    assert f"{borg_repo}/config" in res.stderr


@requires_root
def test_borg_kwargs_makes_the_same_call_succeed(borg_repo):
    """The fix: same command, run as the repository's owner, now works."""
    for path in (borg_repo, *borg_repo.rglob("*")):
        os.chown(path, OTHER_UID, OTHER_UID)

    env = {**os.environ, "BORG_PASSPHRASE": "test"}
    kwargs = borg_kwargs(str(borg_repo), env)
    assert kwargs, "expected to drop to the repository owner"

    res = subprocess.run(
        ["borg", "list", "--bypass-lock", "--json-lines", f"{borg_repo}::arch1"],
        env=env, capture_output=True, text=True, **kwargs,
    )
    assert res.returncode == 0, res.stderr
    assert "f.txt" in res.stdout
