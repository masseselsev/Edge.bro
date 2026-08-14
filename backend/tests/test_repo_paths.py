"""Which repository a node's archives live in, and who may reach it.

Getting a node's shard wrong does not fail loudly — it points borg at a
repository that does not hold that node's archives, so a backup silently
starts a second chain and a restore reports the archive missing. These pin the
parts that decide it.
"""
import os

import pytest

from core import repo_lock, repo_paths


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
