#!/bin/sh
# A restorable copy of the database, taken before the engine is upgraded.
#
# The upgrade itself is done by pgautoupgrade, which runs `pg_upgrade --link`.
# Hardlink mode is fast and needs almost no extra disk, but it consumes the old
# cluster: once the new one is live the old data directory is no longer a
# working database, so "just start the previous image again" is not a recovery
# path. pgautoupgrade says as much itself -- it expects you to already have a
# backup. This is that backup.
#
# It runs with the *old* version's binaries, because a v15 data directory can
# only be read by v15. That is also why this refuses to act when the versions
# do not line up, rather than guessing.
#
# Nothing here writes to the data directory. The cluster is started on a unix
# socket only, dumped, and stopped.
set -eu

TARGET_MAJOR="${PG_TARGET_MAJOR:-18}"
DUMP_DIR="${PG_UPGRADE_BACKUP_DIR:-/upgrade-backup}"
PGDATA="${PGDATA:-/var/lib/postgresql/data}"

log() { echo "[pre-upgrade-dump] $*"; }

# A fresh install has no data directory: the db service will initdb straight
# onto the target version and there is nothing to preserve.
if [ ! -f "$PGDATA/PG_VERSION" ]; then
    # ...unless a cluster exists somewhere else on the volume, which is the one
    # way this whole chain can destroy data silently. PostgreSQL 18's official
    # image moved its default PGDATA from /var/lib/postgresql/data to
    # /var/lib/postgresql/18/docker. If PGDATA ever points at the new location
    # while the volume holds a cluster at the old one, every service here sees
    # "no data", the upgrade is skipped, initdb makes an empty cluster and the
    # application starts against it -- migrations create empty tables and the
    # fleet looks wiped, while the real data sits untouched a directory away.
    STRAY="$(find "${PG_CLUSTER_SEARCH_ROOT:-/var/lib/postgresql}" -maxdepth 3 -name PG_VERSION 2>/dev/null | head -3)"
    if [ -n "$STRAY" ]; then
        log "ERROR: PGDATA=$PGDATA holds no cluster, but one exists elsewhere on the volume:"
        echo "$STRAY" | sed 's/^/    /'
        log "Refusing to continue: starting the database now would initialise an empty"
        log "cluster and hide the real one. Set PGDATA to the directory listed above."
        exit 1
    fi
    log "no existing data directory - fresh install, nothing to dump"
    exit 0
fi

CURRENT_MAJOR="$(cat "$PGDATA/PG_VERSION")"

if [ "$CURRENT_MAJOR" = "$TARGET_MAJOR" ]; then
    log "data directory is already PostgreSQL $TARGET_MAJOR - nothing to do"
    exit 0
fi

# The binaries in this image have to match the data directory, or the cluster
# will not start. Failing loudly beats leaving an unverified gap in front of an
# irreversible upgrade.
MY_MAJOR="$(postgres --version | sed -n 's/.* \([0-9]\{1,\}\)\..*/\1/p')"
if [ "$CURRENT_MAJOR" != "$MY_MAJOR" ]; then
    log "ERROR: data directory is PostgreSQL $CURRENT_MAJOR but this image carries $MY_MAJOR."
    log "Cannot take a safety dump, so the upgrade must not proceed."
    log "Point the db-predump service at a postgres:$CURRENT_MAJOR image and retry."
    exit 1
fi

mkdir -p "$DUMP_DIR"
DUMP="$DUMP_DIR/pre-upgrade-pg${CURRENT_MAJOR}.sql"
MARKER="$DUMP.verified"

# Idempotent across restarts: compose may run this service again after an
# unrelated failure further down the chain, and re-dumping is wasted work.
# Only a dump that passed verification counts.
if [ -f "$MARKER" ]; then
    log "verified dump already present at $DUMP - skipping"
    exit 0
fi

# Refuse to open a data directory another server still has mounted.
#
# `pg_ctl` is not a safeguard here, which was verified rather than assumed:
# postmaster.pid records a PID from the database container's namespace, which
# does not exist in this one, so pg_ctl prints "another server might be
# running" and starts anyway. A second postmaster on a live data directory is
# a corruption path, and it will happen by default -- compose schedules this
# service before it recreates the db container, and nothing in a compose file
# can express "stop that one first".
#
# Reached only when an upgrade is actually due; on an ordinary restart the
# checks above have already exited, so a healthy running database never
# trips this.
LIVE_HOST="${PG_LIVE_CHECK_HOST:-db}"
if pg_isready -q -h "$LIVE_HOST" -U "${POSTGRES_USER:-postgres}" 2>/dev/null; then
    log "ERROR: PostgreSQL is still serving on host '$LIVE_HOST'."
    log "An engine upgrade needs the data directory to itself. Stop the stack first:"
    log "    docker compose down"
    log "    docker compose up -d --build"
    exit 1
fi

log "PostgreSQL $CURRENT_MAJOR -> $TARGET_MAJOR: taking a safety dump first"

# Socket only. No TCP port, so this cannot collide with a db service that is
# somehow still up, and nothing outside the container can reach it.
su-exec postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w -t 120 start
# Stop the cluster whatever happens next, so a failed dump does not leave a
# stray postmaster holding the data directory against the upgrade.
trap 'su-exec postgres pg_ctl -D "$PGDATA" -m fast -w stop || true' EXIT INT TERM

su-exec postgres pg_dumpall --username="${POSTGRES_USER:-postgres}" > "$DUMP.partial"

# `pg_dumpall` exiting 0 is not proof of a complete file: a full disk truncates
# the output while the process still succeeds. The dump ends with a known
# trailer, so check for it.
if ! tail -5 "$DUMP.partial" | grep -q "PostgreSQL database cluster dump complete"; then
    log "ERROR: the dump does not end with its completion marker - treating it as truncated."
    log "Check free space on the backup volume. The upgrade will not proceed."
    rm -f "$DUMP.partial"
    exit 1
fi

# Renamed only once verified, so a half-written file can never be mistaken for
# a usable backup.
mv "$DUMP.partial" "$DUMP"
date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER"

log "safety dump written: $DUMP ($(wc -c < "$DUMP") bytes)"
log "if the upgrade fails, this file restores the database onto an empty volume"
