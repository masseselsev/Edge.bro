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

def _existing_shard_floor() -> int:
    """One past the highest shard directory that already exists on disk.

    The count is allowed to grow and must never shrink: a node's shard is fixed
    at enrolment, so dropping a repository out of the fleet-wide list orphans
    everything in it — the prune stops visiting it and, worse, the SSH forced
    command stops naming it, so its nodes cannot write to their own archives.

    Rather than trust the environment variable to only ever move one way, the
    repositories on disk set a floor under it. A `shard-N` directory exists
    because a node was routed there, which is the fact that matters; the
    variable can then be lowered by mistake without taking anything away.

    Read from the filesystem rather than the database because every container
    needs the same answer at import time and only some of them have a session.
    The database is consulted separately at startup, which catches the one case
    this cannot see: a node assigned to a shard it has not yet initialised.
    """
    try:
        names = os.listdir(os.path.dirname(LEGACY_REPO_PATH))
    except OSError:
        # No storage mounted — a test run, or a container that does not carry
        # the volume. Nothing to protect.
        return 1

    highest = 0
    for name in names:
        if not name.startswith("shard-"):
            continue
        try:
            highest = max(highest, int(name.split("-", 1)[1]))
        except ValueError:
            continue
    return highest + 1


#: One by default: the layout that predates sharding, and the right answer for
#: any fleet whose backup groups spread the load across weeks and months —
#: which is what groups are for. Raise it to match the largest
#: concurrency_limit in use when you actually want parallel writers.
#:
#: Floored by the repositories that already exist, so lowering the variable
#: cannot strand them. `CONFIGURED_SHARD_COUNT` keeps what was asked for, so
#: startup can say plainly that it was overridden and why.
CONFIGURED_SHARD_COUNT = int(os.getenv("BORG_SHARD_COUNT", "1"))
SHARD_COUNT = max(CONFIGURED_SHARD_COUNT, _existing_shard_floor())


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


def stranded_shards(shard_indexes) -> list:
    """Shards that hold nodes but are no longer part of the fleet.

    `SHARD_COUNT` can safely be *raised* on a running deployment: a node's shard
    is stored, never recomputed, so existing nodes stay in their repository and
    only new enrolments reach the new shards. Lowering it is what breaks, and it
    breaks quietly — the node still resolves to its own repository, but that
    repository has dropped out of `all_shard_paths`, so the nightly prune skips
    it and the SSH forced command stops naming it. The node cannot write to its
    own archives and the failure says "restricted path", which points nowhere
    near the setting that caused it.

    Returns the offending indexes, sorted, so a caller can name them.
    """
    return sorted({i for i in shard_indexes if i is not None and i >= SHARD_COUNT})


def is_initialized(path: str) -> bool:
    """Whether a path is a borg repository rather than an empty directory.

    Shards past 0 do not exist until the first node assigned to one runs
    `borg init` on its first backup, so fleet-wide operations have to be able
    to tell "no repository here yet" apart from "repository is broken".
    """
    return os.path.isfile(os.path.join(path, "config"))
