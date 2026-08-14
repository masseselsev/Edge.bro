"""Re-grant every existing borg key access to all shards.

Run once, after deploying sharding, before the first node lands on a shard
other than 0.

The forced command in every authorized_keys entry names the repositories that
key may reach. Before sharding that was one path; now it is one per shard. Keys
written before the change still carry the old single-path restriction, so a
node or kiosk holding one cannot reach any repository but shard 0 — which is
invisible until the first non-zero-shard node tries to back up or restore.

Only entries already carrying our borg forced command are touched. Anything
else in the file belongs to a human and is left exactly as it is.

Idempotent: `ssh_keys.authorize` rewrites an entry in place when its options
differ and leaves it alone when they do not, so a second run reports every
entry as already correct. Safe to run with backups in flight — rewriting
authorized_keys does not disturb SSH sessions already established.

    python3 -m scripts.reauthorize_shard_access [--dry-run]
"""
from __future__ import annotations

import sys

from core import repo_paths, ssh_keys


def main(dry_run: bool = False) -> int:
    path = ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS
    entries = ssh_keys.list_entries(path)

    print(f"Shards: {', '.join(repo_paths.all_shard_paths())}")
    print(f"Reading {path} — {len(entries)} entry/entries\n")

    ours = [
        entry for entry in entries
        if entry.options and "borg serve --restrict-to-path" in entry.options
    ]
    if not ours:
        print("No borg grants found. Nothing to do.")
        return 0

    already_current = [e for e in ours if e.options == ssh_keys.BORG_SERVE_OPTIONS]
    stale = [e for e in ours if e.options != ssh_keys.BORG_SERVE_OPTIONS]

    print(f"{len(ours)} borg grant(s): {len(stale)} to update, "
          f"{len(already_current)} already current.")

    if dry_run:
        for entry in stale:
            print(f"  would update {entry.fingerprint} tag={entry.tag or '(untagged)'}")
        return 0

    for entry in stale:
        action = ssh_keys.authorize(
            path,
            f"{entry.keytype} {entry.blob}",
            options=ssh_keys.BORG_SERVE_OPTIONS,
            # Preserved rather than recomputed: the tag ties the entry to its
            # node or kiosk record, and the audit tooling removes entries whose
            # tag matches nothing. Dropping it here would orphan a live grant.
            tag=entry.tag,
        )
        print(f"  {action.value} {entry.fingerprint} tag={entry.tag or '(untagged)'}")

    print(f"\nDone. {len(stale)} grant(s) now cover every shard.")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
