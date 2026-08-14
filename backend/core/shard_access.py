"""Keeping the fleet's SSH grants in step with the shard count.

The forced command in every `authorized_keys` entry names the repositories that
key may reach, and it is derived from `BORG_SHARD_COUNT`. So raising the count
is only half a change: until the grants are rewritten, the first node routed to
a new shard fails with a restricted-path error that names a setting nobody
touched recently.

Doing that automatically is safe in one direction only, and the ordering here
is the entire point of the module. The rewrite makes every grant say exactly
what the current count says — so running it while the count is *too low* does
not fail, it narrows the grants and removes access that currently works. A
mistyped environment variable would become an outage, caused by the very thing
meant to prevent one.

Hence: establish that nothing is stranded, and only then rewrite.
"""
import logging
from typing import Callable, List, Optional

from core import repo_paths

logger = logging.getLogger(__name__)


class Outcome:
    """What reconciliation did, so a caller can log or test it."""

    def __init__(self, floored: bool, stranded: List[int], rewrote: bool):
        #: The count on disk was higher than the one configured, and won.
        self.floored = floored
        #: Shards holding nodes that the count in use does not cover.
        self.stranded = stranded
        #: Whether the grants were touched.
        self.rewrote = rewrote


def reconcile(shard_indexes, reauthorize: Optional[Callable] = None) -> Outcome:
    """Bring the SSH grants in line with the shard count, if that is safe.

    `shard_indexes` is every shard a node is assigned to. `reauthorize` is
    injected so this can be exercised without an authorized_keys file.
    """
    floored = repo_paths.SHARD_COUNT > repo_paths.CONFIGURED_SHARD_COUNT
    if floored:
        logger.warning(
            "BORG_SHARD_COUNT is set to %s, but %s repositories already exist and hold "
            "archives. Using %s. The count can be raised but never lowered — a node's "
            "shard is fixed when it is enrolled and its archives do not follow it. Set "
            "BORG_SHARD_COUNT=%s to make this explicit.",
            repo_paths.CONFIGURED_SHARD_COUNT, repo_paths.SHARD_COUNT,
            repo_paths.SHARD_COUNT, repo_paths.SHARD_COUNT,
        )

    stranded = repo_paths.stranded_shards(shard_indexes)
    if stranded:
        # The floor covers shards that exist on disk. This is the case it cannot
        # see: a node routed to a shard it has not yet written to, so there is
        # no directory to infer it from.
        logger.error(
            "Nodes are assigned to shard(s) %s, beyond the %s in use. Those nodes cannot "
            "back up or restore. Set BORG_SHARD_COUNT to at least %s and restart. SSH "
            "grants have been left untouched — rewriting them now would remove access "
            "that still works.",
            stranded, repo_paths.SHARD_COUNT, max(stranded) + 1,
        )
        return Outcome(floored, stranded, rewrote=False)

    if reauthorize is None:
        from scripts.reauthorize_shard_access import main as reauthorize

    reauthorize()
    return Outcome(floored, stranded, rewrote=True)
