"""The stamp every step report carries: which commit measured it, and when.

Conventions, "לקחים מסקירת סגירת Plan 1": a number carried forward without the sha it was
measured at is STALE, not evidence. `brain eval report` enforces that per input — but only
for an input that *has* a top-level `sha`. A report without one lands in neither state: the
header table prints "בלי sha", which is not "fresh" and not "stale" and tells a reader
nothing except that nobody can tell. Five of the nine inputs sat there.

So the stamping is one function, called at write time by every writer, rather than a line
copied into each of them — a copy is how three of the five came to disagree about what to
do when git is absent (`""`, `None`, nothing at all).

Stdlib only, deliberately. `brain --help` imports report modules, and the three modules that
kept a private `head_sha()` all justified the copy by not wanting to drag the retrieval
stack in behind a two-line git call. Here there is nothing to drag.
"""

from __future__ import annotations

import subprocess
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: What a report stamps when git cannot answer. Deliberately a word and not `""` or `null`:
#: `brain eval report` compares the first seven characters against HEAD, so `"unknown"`
#: reads as STALE — the honest answer for numbers whose tree nobody can name — while a
#: missing key reads as "בלי sha", which is an absence pretending to be a state.
UNKNOWN = "unknown"

#: `git rev-parse` is a file read. A call that has not answered in five seconds is hung, and
#: a hung stamp must not hang the pipeline step that was only trying to write its report.
TIMEOUT_S = 5


def utc_now_iso() -> str:
    """Now, UTC, to the second — the format every report's `generated_at` uses."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def head_sha(repo: Path | None = None, *, default: str | None = "") -> str | None:
    """The full forty characters of HEAD, or `default` when git cannot answer.

    `default` is a parameter because the callers disagree and both are right: a report field
    wants `UNKNOWN` (a stamp that says it cannot prove anything), while `brain index`'s gate
    wants `None` (no sha at all, so nothing is compared). Guessing one for everybody is what
    the three private copies of this function were already doing, separately.
    """
    try:
        done = subprocess.run(  # noqa: S603 - a fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=None if repo is None else str(repo),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return default
    sha = done.stdout.strip()
    return sha if done.returncode == 0 and sha else default


def stamp_report(
    report: MutableMapping[str, Any],
    *,
    sha: str | None = None,
    generated_at: str | None = None,
) -> MutableMapping[str, Any]:
    """Stamp a step report with the commit and the time it is being written at.

    Called at write time, not build time: the file on disk is what a later reader compares
    against HEAD, so the stamp has to describe the write that produced it. `sha` is for a
    writer that already knows which commit its numbers belong to (a merge that carries a
    run's own sha); everything else takes HEAD.
    """
    report["sha"] = sha or head_sha(default=UNKNOWN) or UNKNOWN
    report["generated_at"] = generated_at or utc_now_iso()
    return report
