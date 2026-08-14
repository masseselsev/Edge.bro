"""Which borg repository a node's archives live in.

Borg holds a repository's lock for the whole of `borg create` — hours, not a
brief critical section — so one repository means one node in the entire fleet
writing at a time, whatever `BackupGroup.concurrency_limit` says. A fixed set
of repositories gives that many genuinely parallel writers, each with its own
lock.

Assignment is by node id, not by group: group membership is reassignable at
any time and a node's archives cannot follow it around. `Node.borg_shard_index`
is computed once at enrolment and then persisted, so neither a change to
`SHARD_COUNT` nor a re-run of bootstrap can point an existing node at a
repository its data is not in.

Shard 0 is the pre-existing `/data/borg/fleet` path, unrenamed and unmoved.
That is what makes this migration-free: every node that existed before
sharding backfills to 0 and keeps using the repository it was already using.
"""
from __future__ import annotations

import os

#: Shard 0 predates sharding. Its path is load-bearing, not cosmetic — every
#: archive written before this module existed is in it, and the fleet's
#: authorized_keys entries already name it.
LEGACY_REPO_PATH = "/data/borg/fleet"

SHARD_COUNT = int(os.getenv("BORG_SHARD_COUNT", "5"))


def shard_path(shard_index: int) -> str:
    if shard_index == 0:
        return LEGACY_REPO_PATH
    return f"/data/borg/shard-{shard_index}"


def all_shard_paths() -> list[str]:
    return [shard_path(i) for i in range(SHARD_COUNT)]


def shard_index_for_new_node(node_id: int) -> int:
    """The shard a freshly enrolled node belongs to.

    Only ever called once per node, at enrolment. Anything that needs an
    existing node's shard must read the stored column instead — recomputing it
    later would silently move a node off the repository holding its archives.
    """
    return node_id % SHARD_COUNT


def repo_path_for_node(node) -> str:
    """The repository holding this node's archives.

    Falls back to shard 0 when the column is unset. A node whose shard is
    unknown is far more likely to be a pre-sharding node than one belonging to
    a shard nobody assigned, and shard 0 is the only repository guaranteed to
    exist.
    """
    return shard_path(getattr(node, "borg_shard_index", 0) or 0)


def is_initialized(path: str) -> bool:
    """Whether a path is a borg repository rather than an empty directory.

    Shards past 0 do not exist until the first node assigned to one runs
    `borg init` on its first backup, so fleet-wide operations have to be able
    to tell "no repository here yet" apart from "repository is broken".
    """
    return os.path.isfile(os.path.join(path, "config"))
