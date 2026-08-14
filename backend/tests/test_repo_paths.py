"""Which repository a node's archives live in, and who may reach it.

Getting a node's shard wrong does not fail loudly — it points borg at a
repository that does not hold that node's archives, so a backup silently
starts a second chain and a restore reports the archive missing. These pin the
parts that decide it.
"""
import os

import pytest

from core import repo_lock, repo_paths, ssh_keys


class FakeNode:
    def __init__(self, borg_shard_index):
        self.borg_shard_index = borg_shard_index


def test_shard_zero_is_the_repository_that_predates_sharding():
    """Every archive written before sharding is in this path, and every
    already-deployed authorized_keys entry names it. It must not move."""
    assert repo_paths.shard_path(0) == "/data/borg/fleet"


def test_other_shards_are_siblings_under_the_same_volume():
    assert repo_paths.shard_path(1) == "/data/borg/shard-1"
    assert repo_paths.shard_path(4) == "/data/borg/shard-4"


def test_all_shard_paths_covers_every_shard_exactly_once():
    paths = repo_paths.all_shard_paths()
    assert len(paths) == repo_paths.SHARD_COUNT
    assert len(set(paths)) == len(paths)
    assert paths[0] == repo_paths.shard_path(0)


def test_a_new_node_is_assigned_by_its_own_id():
    assert repo_paths.shard_index_for_new_node(7) == 7 % repo_paths.SHARD_COUNT
    assert repo_paths.shard_index_for_new_node(0) == 0


def test_a_nodes_repository_comes_from_its_stored_shard_not_its_id():
    """The stored column is authoritative. Recomputing from the id would move a
    node off the repository holding its archives whenever SHARD_COUNT changed."""
    assert repo_paths.repo_path_for_node(FakeNode(0)) == "/data/borg/fleet"
    assert repo_paths.repo_path_for_node(FakeNode(3)) == "/data/borg/shard-3"


def test_a_node_with_no_shard_recorded_falls_back_to_shard_zero():
    """Pre-sharding rows and anything that skipped assignment. Shard 0 is the
    only repository guaranteed to exist."""
    assert repo_paths.repo_path_for_node(FakeNode(None)) == "/data/borg/fleet"


def test_is_initialized_distinguishes_a_repository_from_an_empty_directory(tmp_path):
    """Shards past 0 are created lazily by the first backup routed to one, so
    fleet-wide work has to tell 'not yet' apart from 'broken'."""
    empty = tmp_path / "shard-1"
    empty.mkdir()
    assert not repo_paths.is_initialized(str(empty))

    (empty / "config").write_text("[repository]\n")
    assert repo_paths.is_initialized(str(empty))


def test_is_initialized_is_false_for_a_path_that_does_not_exist(tmp_path):
    assert not repo_paths.is_initialized(str(tmp_path / "never-created"))


def test_a_node_stored_without_a_shard_lands_on_zero(tmp_path):
    """What the migration relies on: the column's server default backfills
    every pre-sharding row onto the repository already holding its archives."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import models
    from database import Base

    engine = create_engine(f"sqlite:///{tmp_path}/shard_default.db")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    node = models.Node(hostname="pre-sharding", ip_address="192.168.1.10")
    session.add(node)
    session.commit()
    session.refresh(node)

    assert node.borg_shard_index == 0
    assert repo_paths.repo_path_for_node(node) == "/data/borg/fleet"
    session.close()


# --- the SSH forced command has to cover every shard ---

def test_the_forced_command_restricts_to_every_shard():
    """A node cannot know from its key which repository it will be routed to,
    so the grant has to name all of them."""
    for path in repo_paths.all_shard_paths():
        assert f"--restrict-to-path {path}" in ssh_keys.BORG_SERVE_OPTIONS

    assert ssh_keys.BORG_SERVE_OPTIONS.count("--restrict-to-path") == repo_paths.SHARD_COUNT
    # The hardening flags are the other half of the grant and must survive.
    assert "no-port-forwarding" in ssh_keys.BORG_SERVE_OPTIONS
    assert "no-pty" in ssh_keys.BORG_SERVE_OPTIONS


def test_reauthorizing_rewrites_an_existing_grant_rather_than_duplicating_it(tmp_path):
    """What the shard-access migration script relies on: an entry whose options
    changed is rewritten in place. A duplicate would leave the old, narrower
    restriction in the file, still matching first."""
    key = (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGvSn4KpVvV3z0dQ0Kx7Zn8sVJ3xQ1lWc2mB4tYuIoPq"
    )
    path = str(tmp_path / "authorized_keys")
    old_options = (
        'command="borg serve --restrict-to-path /data/borg/fleet",'
        "no-port-forwarding,no-X11-forwarding,no-pty"
    )

    ssh_keys.authorize(path, key, options=old_options, tag=ssh_keys.node_tag(7))
    action = ssh_keys.authorize(
        path, key, options=ssh_keys.BORG_SERVE_OPTIONS, tag=ssh_keys.node_tag(7)
    )

    assert action is ssh_keys.Action.REWRITTEN
    entries = ssh_keys.list_entries(path)
    assert len(entries) == 1
    assert entries[0].options == ssh_keys.BORG_SERVE_OPTIONS
    assert entries[0].tag == ssh_keys.node_tag(7)


# --- one maintenance flag per repository ---

@pytest.fixture
def fake_redis(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {}

        def get(self, key):
            value = self.store.get(key)
            return value.encode() if isinstance(value, str) else value

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        def expire(self, key, ttl):
            return key in self.store

        def delete(self, key):
            return self.store.pop(key, None) is not None

        def eval(self, script, numkeys, key, arg):
            if self.store.get(key) == arg:
                del self.store[key]
                return 1
            return 0

    fake = FakeRedis()
    monkeypatch.setattr(repo_lock, "redis_client", fake)
    return fake


def test_two_shards_can_be_maintained_at_once(fake_redis):
    """The point of sharding: pruning one repository must not stand down
    backups bound for another."""
    with repo_lock.repository_maintenance("prune", repo_path="/data/borg/fleet") as a:
        assert a is not None
        with repo_lock.repository_maintenance("prune", repo_path="/data/borg/shard-1") as b:
            assert b is not None
            assert repo_lock.maintenance_in_progress("/data/borg/fleet")
            assert repo_lock.maintenance_in_progress("/data/borg/shard-1")


def test_one_shard_still_excludes_a_second_prune_of_itself(fake_redis):
    """Two prunes against one repository is the corruption case the flag exists
    for; sharding must not have loosened it."""
    with repo_lock.repository_maintenance("prune-a", repo_path="/data/borg/shard-2") as first:
        assert first is not None
        with repo_lock.repository_maintenance("prune-b", repo_path="/data/borg/shard-2") as second:
            assert second is None


def test_maintaining_one_shard_leaves_the_others_free(fake_redis):
    with repo_lock.repository_maintenance("prune", repo_path="/data/borg/shard-1"):
        assert not repo_lock.maintenance_in_progress("/data/borg/fleet")
        assert not repo_lock.maintenance_in_progress("/data/borg/shard-2")


def test_a_shards_flag_is_released_after_its_prune(fake_redis):
    with repo_lock.repository_maintenance("prune", repo_path="/data/borg/shard-3"):
        pass
    assert not repo_lock.maintenance_in_progress("/data/borg/shard-3")
    assert fake_redis.store == {}


# --- The repository path the restore kiosk is told to use -------------------
#
# The kiosk builds its own `ssh://borg@orchestrator/<path>` URL and has no way
# to derive the shard layout: it knows a node's id and hostname, not the
# orchestrator's `BORG_SHARD_COUNT` or its directory convention. It used to
# assume every node lived in the pre-sharding repository, which is correct for
# shard 0 and silently wrong for every other node — "archive does not exist",
# at a customer site, with the disk already wiped. So the orchestrator states
# the path in the node payload the kiosk reads.


def test_a_node_reports_the_repository_its_shard_resolves_to():
    import models

    node = models.Node(hostname="ws-1", ip_address="10.0.0.1", borg_shard_index=3)
    assert node.borg_repo_path == repo_paths.shard_path(3)


def test_a_shard_zero_node_reports_the_pre_sharding_repository():
    import models

    node = models.Node(hostname="ws-0", ip_address="10.0.0.2", borg_shard_index=0)
    assert node.borg_repo_path == repo_paths.LEGACY_REPO_PATH


def test_the_node_response_carries_the_repository_path():
    """Serialization is the part that actually reaches the kiosk: a property
    the response model does not declare is a property the kiosk never sees."""
    import models
    import schemas

    node = models.Node(
        id=11, hostname="ws-11", ip_address="10.0.0.11", ssh_port=22,
        status="READY", disk_type="SSD", borg_shard_index=1,
        backup_paused=False, backup_today=False, missed_window=False,
    )
    payload = schemas.NodeResponse.model_validate(node).model_dump()
    assert payload["borg_repo_path"] == repo_paths.shard_path(1)


# --- changing BORG_SHARD_COUNT after nodes exist ----------------------------
#
# A deployment that starts small should be able to start at 1 — which is the
# pre-sharding layout exactly — and add shards when it grows. Whether that is
# safe is not a matter of taste: a node's shard is stored, never recomputed, so
# raising the count leaves every existing node exactly where it was, while
# lowering it strands any node above the new ceiling.


def _with_shard_count(monkeypatch, count):
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", count)


def test_a_single_shard_is_the_pre_sharding_layout(monkeypatch):
    """The opt-out. One shard must be indistinguishable from before sharding
    existed, or "turn it off" is not actually available."""
    _with_shard_count(monkeypatch, 1)
    assert repo_paths.all_shard_paths() == [repo_paths.LEGACY_REPO_PATH]
    for node_id in (1, 7, 114, 2000):
        assert repo_paths.shard_index_for_new_node(node_id) == 0
        assert repo_paths.repo_path_for_node(FakeNode(0)) == repo_paths.LEGACY_REPO_PATH


def test_raising_the_count_leaves_existing_nodes_where_they_are(monkeypatch):
    """The growth path. Their shard is read from the column, not recomputed, so
    a node enrolled under one setting keeps its repository under another."""
    _with_shard_count(monkeypatch, 1)
    settled = [FakeNode(repo_paths.shard_index_for_new_node(i)) for i in range(1, 20)]
    before = [repo_paths.repo_path_for_node(n) for n in settled]

    _with_shard_count(monkeypatch, 5)
    assert [repo_paths.repo_path_for_node(n) for n in settled] == before


def test_raising_the_count_routes_only_new_nodes_to_new_shards(monkeypatch):
    _with_shard_count(monkeypatch, 5)
    fresh = {repo_paths.shard_index_for_new_node(i) for i in range(100, 120)}
    assert fresh == {0, 1, 2, 3, 4}


def test_lowering_the_count_strands_the_nodes_above_it(monkeypatch):
    """Why the docs may say "raise, never lower". The node still resolves to
    its own repository, but the fleet-wide list no longer contains it: the
    nightly prune skips it and, worse, the SSH forced command stops naming it,
    so the node cannot write to its own archives."""
    _with_shard_count(monkeypatch, 5)
    stranded = FakeNode(4)
    its_repo = repo_paths.repo_path_for_node(stranded)

    _with_shard_count(monkeypatch, 2)
    assert repo_paths.repo_path_for_node(stranded) == its_repo
    assert its_repo not in repo_paths.all_shard_paths(), (
        "this is the failure being pinned, not a passing condition"
    )


def test_the_grant_follows_the_count(monkeypatch):
    """The forced command is derived, so raising the count is not complete
    until the fleet's keys are re-authorized against the longer path list."""
    _with_shard_count(monkeypatch, 1)
    assert ssh_keys._borg_serve_options().count("--restrict-to-path") == 1

    _with_shard_count(monkeypatch, 3)
    assert ssh_keys._borg_serve_options().count("--restrict-to-path") == 3


def test_nodes_left_above_the_ceiling_are_reported(monkeypatch):
    """So lowering the count is caught at startup rather than as a restricted
    path error on the next backup."""
    _with_shard_count(monkeypatch, 2)
    assert repo_paths.stranded_shards([0, 1, 4, 4, 3]) == [3, 4]


def test_nothing_is_reported_when_every_node_fits(monkeypatch):
    _with_shard_count(monkeypatch, 5)
    assert repo_paths.stranded_shards([0, 1, 4]) == []


def test_a_node_with_no_shard_recorded_is_not_stranded(monkeypatch):
    """Null means pre-sharding, which is shard 0 — always valid."""
    _with_shard_count(monkeypatch, 1)
    assert repo_paths.stranded_shards([None, 0]) == []
