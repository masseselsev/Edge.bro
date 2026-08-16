"""The decisions the pre-upgrade dump script makes before it touches anything.

PostgreSQL 18's official image moved its default PGDATA from
`/var/lib/postgresql/data` to `/var/lib/postgresql/18/docker`. That single
change is the sharpest edge in the whole upgrade: if PGDATA ever points at the
new location while the volume holds a cluster at the old one, nothing errors.
The upgrade sees no data and skips, initdb makes an empty cluster, migrations
create empty tables, and the orchestrator comes up looking healthy with an
empty fleet -- while the real database sits untouched one directory away.

It is not hypothetical. The first rehearsal of this chain did exactly that,
and only a row-count comparison caught it.

So the script refuses to call an empty PGDATA a fresh install when a cluster
exists elsewhere on the volume. These cover that refusal and the other
decisions taken before any irreversible step, none of which need a real
PostgreSQL to exercise.
"""
import pathlib
import subprocess

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent.parent
          / "docker" / "postgres" / "pre-upgrade-dump.sh")

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="repository not checked out whole"
)


def run(pgdata, search_root, target="18", extra_path=None):
    """Run the script far enough to reach a decision, and report it."""
    env = {
        "PATH": f"{extra_path}:/usr/bin:/bin" if extra_path else "/usr/bin:/bin",
        "PGDATA": str(pgdata),
        "PG_TARGET_MAJOR": target,
        "PG_CLUSTER_SEARCH_ROOT": str(search_root),
        "PG_UPGRADE_BACKUP_DIR": str(pathlib.Path(pgdata).parent / "backup"),
        "POSTGRES_USER": "postgres",
    }
    return subprocess.run(["/bin/sh", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)


def stub(directory, name, exit_code=0, output=""):
    """Put a fake binary on PATH so a decision can be reached without a real
    PostgreSQL installation."""
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{('echo ' + repr(output)) if output else ':'}\nexit {exit_code}\n")
    path.chmod(0o755)
    return directory


@pytest.fixture
def volume(tmp_path):
    """A stand-in for the pg-data volume, with PGDATA inside it."""
    root = tmp_path / "postgresql"
    (root / "data").mkdir(parents=True)
    return root


def test_a_genuinely_fresh_install_is_a_no_op(volume):
    """Nothing on the volume at all: the db service will initdb onto the
    target version and there is nothing worth preserving."""
    result = run(volume / "data", volume)

    assert result.returncode == 0, result.stderr
    assert "fresh install" in result.stdout


def test_an_already_upgraded_cluster_is_a_no_op(volume):
    """Runs on every ordinary restart, so it has to be cheap and silent."""
    (volume / "data" / "PG_VERSION").write_text("18\n")

    result = run(volume / "data", volume)

    assert result.returncode == 0, result.stderr
    assert "already PostgreSQL 18" in result.stdout


def test_a_cluster_hiding_at_another_path_stops_everything(volume):
    """The PGDATA-moved-under-us case. Empty PGDATA plus a real cluster
    elsewhere must fail loudly, because every downstream step reads this as a
    fresh install and would quietly replace the database with an empty one."""
    (volume / "18" / "docker").mkdir(parents=True)
    (volume / "18" / "docker" / "PG_VERSION").write_text("18\n")

    result = run(volume / "data", volume)

    assert result.returncode == 1, "an empty PGDATA beside a real cluster must not pass"
    assert "Refusing to continue" in result.stdout
    assert "18/docker" in result.stdout, "the operator needs the path to point PGDATA at"


def test_the_guard_does_not_fire_on_the_cluster_it_was_given(volume):
    """The search would find PGDATA's own PG_VERSION too. Finding the cluster
    you were pointed at is not a reason to refuse."""
    (volume / "data" / "PG_VERSION").write_text("18\n")

    result = run(volume / "data", volume)

    assert result.returncode == 0, result.stderr


def test_binaries_that_cannot_read_the_data_directory_stop_everything(volume, tmp_path):
    """A v15 image cannot read a v14 directory, so it cannot take the safety
    dump either. Without that dump the upgrade is irreversible, so this has to
    fail rather than let the next step proceed unprotected."""
    (volume / "data" / "PG_VERSION").write_text("14\n")

    stub = tmp_path / "bin"
    stub.mkdir()
    fake = stub / "postgres"
    fake.write_text("#!/bin/sh\necho 'postgres (PostgreSQL) 15.18'\n")
    fake.chmod(0o755)

    result = run(volume / "data", volume, extra_path=str(stub))

    assert result.returncode == 1
    assert "Cannot take a safety dump" in result.stdout


def test_a_still_running_database_stops_everything(volume, tmp_path):
    """Two postmasters on one data directory corrupt it, and `pg_ctl` does not
    prevent it across containers: postmaster.pid holds a PID from the database
    container's namespace, so pg_ctl reports "another server might be running"
    and starts anyway. Verified by doing it -- the second process ran a
    shutdown checkpoint on a directory the first still had open.

    Compose cannot express "stop that service before starting this one", so
    this refusal is what stands between an ordinary `up -d --build` and a
    damaged cluster.
    """
    (volume / "data" / "PG_VERSION").write_text("15\n")
    bin_dir = stub(tmp_path / "bin", "pg_isready", exit_code=0)
    stub(bin_dir, "postgres", output="postgres (PostgreSQL) 15.18")

    result = run(volume / "data", volume, extra_path=str(bin_dir))

    assert result.returncode == 1
    assert "still serving" in result.stdout
    assert "docker compose down" in result.stdout, "the operator needs the fix, not just the fault"


def test_an_upgrade_proceeds_once_nothing_is_serving(volume, tmp_path):
    """The converse: a stale postmaster.pid left by a crash must not become a
    permanent block, so the check is liveness, not the presence of a file."""
    (volume / "data" / "PG_VERSION").write_text("15\n")
    (volume / "data" / "postmaster.pid").write_text("999999\n")
    bin_dir = stub(tmp_path / "bin", "pg_isready", exit_code=1)
    stub(bin_dir, "postgres", output="postgres (PostgreSQL) 15.18")
    stub(bin_dir, "su-exec", exit_code=1)  # stops the run right after the guard

    result = run(volume / "data", volume, extra_path=str(bin_dir))

    assert "still serving" not in result.stdout, "a stale pid file is not a running server"
    assert "taking a safety dump first" in result.stdout


def test_the_target_version_is_configurable(volume):
    """Pinned in compose beside the image tag; a mismatch between the two is
    how this silently stops protecting anything."""
    (volume / "data" / "PG_VERSION").write_text("19\n")

    result = run(volume / "data", volume, target="19")

    assert result.returncode == 0
    assert "already PostgreSQL 19" in result.stdout
