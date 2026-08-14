"""Every borg command that takes a repository lock has to be willing to wait.

Borg's default `--lock-wait` is one second. Against a repository someone else
is already using, that is not a wait, it is an immediate failure: the nightly
prune cannot delete, a node deletion leaves its archives behind forever, an ISO
export returns an error to the kiosk. At 2000 nodes across five shards, several
hundred nodes share each repository and something is nearly always using it.

The fix that introduced `--lock-wait` applied it to `borg create` and
`borg init` and stopped there, which is exactly the kind of gap that reads as
finished. This walks the source instead of trusting that, so a borg invocation
added later cannot quietly reintroduce the one-second default.

Read-only commands passing `--bypass-lock` are exempt and are meant to be: they
take no lock, which is the right call for archive browsing that must work while
a backup runs.

So is the throwaway repository the ISO builder creates under a per-request uuid
and hands to nobody: it cannot contend, and giving it a ten-minute patience for
a lock only it can hold would say something untrue about it.
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: Commands that acquire a repository lock. `borg with-lock` and `break-lock`
#: are deliberately absent — the first is not used here and the second exists
#: precisely to act on a lock nobody is waiting for.
LOCKING_COMMANDS = {
    "create", "init", "delete", "prune", "compact",
    "list", "info", "export-tar", "import-tar", "check", "rename",
}

#: Only argv-style invocations are checked here. The two shell-string commands
#: sent to a node (`borg init`, `borg create` inside an ssh command line) carry
#: the flag through their own f-strings and are pinned by test_pruning and
#: test_ssh_command instead.
SOURCES = [
    "backup_tasks.py",
    "restore_tasks.py",
    "restore_logic.py",
    "core/archive_cleanup.py",
    "core/repo_usage.py",
    "routers/iso.py",
    "routers/archives.py",
    "routers/nodes_crud.py",
]


def _string_of(node):
    """The literal value of an argv element, or None if it is computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _borg_argvs(path):
    """Every list literal in a file whose first element is the string "borg"."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.List) or not node.elts:
            continue
        if _string_of(node.elts[0]) != "borg":
            continue
        yield node


def _flags(argv_node):
    return {_string_of(e) for e in argv_node.elts}


#: Names that hold a repository this process created for itself and never
#: shares. Nothing else can hold their lock, so waiting on one is meaningless.
PRIVATE_REPO_NAMES = {"temp_repo_dir"}


def _targets_a_private_repo(argv_node):
    return any(
        isinstance(n, ast.Name) and n.id in PRIVATE_REPO_NAMES
        for e in argv_node.elts
        for n in ast.walk(e)
    )


@pytest.mark.parametrize("source", SOURCES)
def test_every_locking_borg_call_waits_for_the_lock(source):
    path = BACKEND / source
    if not path.exists():
        pytest.skip(f"{source} does not exist")

    offenders = []
    for argv in _borg_argvs(path):
        if len(argv.elts) < 2:
            continue
        subcommand = _string_of(argv.elts[1])
        if subcommand not in LOCKING_COMMANDS:
            continue
        flags = _flags(argv)
        if "--bypass-lock" in flags or "--lock-wait" in flags:
            continue
        if _targets_a_private_repo(argv):
            continue
        offenders.append(f"{source}:{argv.lineno} borg {subcommand}")

    assert not offenders, (
        "these borg commands take a repository lock but keep borg's one-second "
        "default wait, so they fail outright the moment anything else is using "
        "the repository:\n  " + "\n  ".join(offenders)
    )


def test_the_check_can_actually_see_a_violation():
    """A source walker that silently matches nothing would pass forever."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bad = pathlib.Path(tmp) / "bad.py"
        bad.write_text('subprocess.run(["borg", "delete", repo, name])\n')
        argvs = list(_borg_argvs(bad))
        assert len(argvs) == 1
        assert "--lock-wait" not in _flags(argvs[0])


def test_the_check_is_actually_reaching_the_real_invocations():
    """And one that matched nothing in the real tree would pass too."""
    found = sum(
        1
        for source in SOURCES
        if (BACKEND / source).exists()
        for argv in _borg_argvs(BACKEND / source)
        if len(argv.elts) >= 2 and _string_of(argv.elts[1]) in LOCKING_COMMANDS
    )
    assert found >= 8, f"only found {found} locking borg invocations to check"
