"""Which archives a retention policy keeps — borg's algorithm, in Python.

Reimplementing this is not something to do lightly, so here is why.

`borg prune` selects archives with a single `--prefix`, and every node's
archives live in one shared `fleet` repository. Pruning the fleet therefore
meant one `borg prune` per node, each taking the repository's exclusive lock
and each re-reading a manifest that holds every archive of every node. At 2000
nodes that is hours of serialised work starting at 03:00, still running when
the backup windows open — and for the whole of it no backup can run, because
they need the same lock.

Deciding here and issuing one `borg delete` with every doomed archive turns
2000 locked passes into three: list, delete, compact.

**This is a faithful port, not an improvement.** It mirrors
`borg/helpers/misc.py` at 1.2.4 exactly, including two behaviours that are easy
to miss and wrong to "clean up":

* `prune_split` keeps the **oldest** archive as well, when a rule ends up
  keeping fewer than `n`. That is the `rule+"[oldest]"` branch. Drop it and you
  delete the oldest backup of a young node.
* `kept_because` is shared across rules, and a period whose newest archive was
  already kept by an earlier rule is skipped without consuming a slot from the
  later one. So keep-daily=7 with keep-weekly=4 does not simply mean eleven
  archives.

Period keys are computed on the naive local timestamps `borg list --json`
emits, which is the same clock `to_localtime()` gives borg's own prune, so the
bucket boundaries agree. The one place this port is not bit-identical is
`--keep-within` across a DST change, where borg compares aware UTC and this
compares naive local: a policy measured in days can differ by an hour at the
boundary.

Verified against borg 1.2.4 source, not from memory. If the pinned borg version
changes, re-read `prune_within`/`prune_split` before trusting this.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from operator import attrgetter
from typing import Dict, List, Optional, Sequence, Tuple

#: strftime keys defining each rule's bucket, and the order the rules run in.
#: Both copied from borg's PRUNING_PATTERNS — the order matters because
#: `kept_because` is shared and earlier rules claim archives first.
PRUNING_PATTERNS = {
    "secondly": "%Y-%m-%d %H:%M:%S",
    "minutely": "%Y-%m-%d %H:%M",
    "hourly": "%Y-%m-%d %H",
    "daily": "%Y-%m-%d",
    "weekly": "%G-%V",
    "monthly": "%Y-%m",
    "yearly": "%Y",
}

#: `--keep-within` interval suffixes, in hours. Borg counts a month as 31 days
#: and a year as 365, deliberately erring towards keeping more.
WITHIN_UNIT_HOURS = {"H": 1, "d": 24, "w": 24 * 7, "m": 24 * 31, "y": 24 * 365}


@dataclass(frozen=True)
class Archive:
    """One archive as `borg list --json` reports it: a name and a local time."""
    name: str
    ts: datetime


@dataclass
class RetentionRules:
    """A policy in borg's own terms.

    `None` means the rule is not applied at all, which is distinct from 0 —
    borg treats an explicit 0 as "keep none by this rule".
    """
    within_hours: Optional[float] = None
    secondly: Optional[int] = None
    minutely: Optional[int] = None
    hourly: Optional[int] = None
    daily: Optional[int] = None
    weekly: Optional[int] = None
    monthly: Optional[int] = None
    yearly: Optional[int] = None

    def is_empty(self) -> bool:
        """No rule set at all, which must never be treated as "delete all"."""
        return self.within_hours is None and all(
            getattr(self, rule) is None for rule in PRUNING_PATTERNS
        )


def parse_within(value: str) -> Optional[float]:
    """"2d" -> 48.0 hours. None if it is not a form borg accepts."""
    if not value or len(value) < 2:
        return None
    number, unit = value[:-1], value[-1]
    if unit not in WITHIN_UNIT_HOURS:
        return None
    try:
        return int(number) * WITHIN_UNIT_HOURS[unit]
    except ValueError:
        return None


def prune_within(
    archives: Sequence[Archive], hours: float, now: datetime, kept_because: Dict[str, tuple]
) -> List[Archive]:
    """Every archive newer than `hours` ago. Port of borg's prune_within."""
    target = now - timedelta(seconds=hours * 3600)
    kept_counter = 0
    result = []
    for a in archives:
        if a.ts > target:
            kept_counter += 1
            kept_because[a.name] = ("within", kept_counter)
            result.append(a)
    return result


def prune_split(
    archives: Sequence[Archive], rule: str, n: Optional[int], kept_because: Dict[str, tuple]
) -> List[Archive]:
    """The newest archive of each of the last `n` periods. Port of borg's prune_split.

    Read the two oddities in the module docstring before changing anything
    here; both of them look like bugs and are not.
    """
    last = None
    keep: List[Archive] = []
    pattern = PRUNING_PATTERNS[rule]
    if n == 0:
        return keep

    a = None
    for a in sorted(archives, key=attrgetter("ts"), reverse=True):
        period = a.ts.strftime(pattern)
        if period != last:
            last = period
            if a.name not in kept_because:
                keep.append(a)
                kept_because[a.name] = (rule, len(keep))
                if len(keep) == n:
                    break
    # The oldest archive survives a rule that could not fill its quota. Without
    # this a node with three backups and keep-daily=7 loses its oldest one.
    if a is not None and (n is None or len(keep) < n) and a.name not in kept_because:
        keep.append(a)
        kept_because[a.name] = (rule + "[oldest]", len(keep))
    return keep


def select(
    archives: Sequence[Archive], rules: RetentionRules, now: Optional[datetime] = None
) -> Tuple[List[Archive], List[Archive], Dict[str, tuple]]:
    """Split `archives` into (keep, delete, reasons).

    `reasons` maps an archive name to the rule that saved it, which is what
    makes a deletion explainable after the fact.
    """
    if now is None:
        now = datetime.now()

    kept_because: Dict[str, tuple] = {}

    # An empty policy keeps everything. Reaching this with no rules means the
    # settings row is missing or malformed, and the only safe reading of "no
    # policy" is "do not delete anything".
    if rules.is_empty():
        return list(archives), [], kept_because

    keep: List[Archive] = []
    if rules.within_hours is not None:
        keep += prune_within(archives, rules.within_hours, now, kept_because)

    for rule in PRUNING_PATTERNS:
        num = getattr(rules, rule)
        if num is not None:
            keep += prune_split(archives, rule, num, kept_because)

    keep_names = {a.name for a in keep}
    delete = [a for a in archives if a.name not in keep_names]
    return keep, delete, kept_because


def rules_from_policy(policy: Optional[dict], legacy) -> RetentionRules:
    """Translate a stored policy into borg's rules.

    Mirrors, exactly, the flags the per-node `borg prune` used to be given.
    The three policy shapes come from the settings UI:

    * **interval** — the familiar keep-daily/weekly/monthly grandfathering.
    * **count** — keep the N most recent, and nothing else. keep-last is
      borg's alias for keep-secondly, and since two backups of one node never
      share a second it means exactly "the N newest".
    * **timeframe** — keep everything within a window. Paired with
      `--keep-last 1` so a node that has not been backed up for longer than
      the window keeps its last archive rather than losing everything.

    `legacy` is the Settings row, whose flat keep_daily/keep_weekly/
    keep_monthly columns predate `retention_policy` and are still what a
    deployment that never opened the retention UI is running on.
    """
    if not policy:
        return RetentionRules(
            daily=getattr(legacy, "keep_daily", None),
            weekly=getattr(legacy, "keep_weekly", None),
            monthly=getattr(legacy, "keep_monthly", None),
        )

    p_type = policy.get("type", "interval")
    if p_type == "count":
        return RetentionRules(secondly=policy.get("keep_last", 5))
    if p_type == "timeframe":
        value = policy.get("within_value", 3)
        unit = policy.get("within_unit", "m")
        return RetentionRules(secondly=1, within_hours=parse_within(f"{value}{unit}"))
    return RetentionRules(
        daily=policy.get("keep_daily", 7),
        weekly=policy.get("keep_weekly", 4),
        monthly=policy.get("keep_monthly", 6),
    )


def borg_prune_args(rules: RetentionRules) -> List[str]:
    """The same policy as `borg prune` flags.

    Kept so the equivalent single-node borg command can be logged next to what
    this module decided — the fastest way to check a disagreement by hand.
    """
    args: List[str] = []
    if rules.within_hours is not None:
        args += ["--keep-within", f"{int(rules.within_hours)}H"]
    for rule in PRUNING_PATTERNS:
        num = getattr(rules, rule)
        if num is not None:
            flag = "--keep-last" if rule == "secondly" else f"--keep-{rule}"
            args += [flag, str(num)]
    return args
