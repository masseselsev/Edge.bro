"""Which compression a node's archives are written with.

Two places need this and they must agree. The backup writes the archive, and
the kiosk repacks it into the mini-repo a technician carries to site: if the
repack picks a different algorithm the data is decompressed and recompressed
for no reason, and the technician downloads whatever the mismatch costs.

That is not hypothetical. The repack passed no `--compression` at all, so borg
used its default of lz4 against archives written with zstd:3, and a 1.46 GiB
archive came out of it as a 1.81 GiB repository — measured, not estimated.
"""
from typing import Optional

#: What a deployment gets before anyone opens the settings UI. Matches the
#: column default on `Settings.default_compression`.
DEFAULT_COMPRESSION = "zstd:3"


def for_node(node, group=None, settings=None) -> str:
    """The compression this node's archives are written with.

    Most specific wins: the node's backup group, then the global setting, then
    the built-in default. `group` may be passed in when the caller already has
    it; otherwise it is read off the node.
    """
    if group is None:
        group = getattr(node, "group", None)

    return (
        (getattr(group, "compression", None) if group else None)
        or getattr(settings, "default_compression", None)
        or DEFAULT_COMPRESSION
    )


def to_borg_arg(compression: str) -> str:
    """Borg's spelling of it.

    The UI and the database store `zstd:3`; borg's `--compression` wants
    `zstd,3`. Colons are what every other tuning field in this project uses,
    so the translation happens here rather than changing what is stored.
    """
    return compression.replace(":", ",")
