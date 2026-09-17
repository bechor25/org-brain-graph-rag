"""`brain chunk --stamp-slice` — bring `Chunk.slice` up to date without re-chunking.

The mirror of `--stamp-synthetic`, and it exists for the same reason, found the same way.
Plan 3 decision 6 says a graph node always carries `slice` (`base` or `incremental`),
because a property that is not written is one `brain reset --slice` cannot match on.
`brain chunk` does write it — when it writes a chunk. It does not rewrite a chunk whose
text is byte-identical (`ChunkState.is_current`), which is the right call for 13,846 nodes
and 14 MB of text and is why the second run of `brain chunk` embeds nothing.

Measured on the live graph on 2026-09-17, after the first incremental run: 71 chunks
carried `slice`, and 13,846 carried nothing at all. Re-chunking would fix it and would
re-embed the corpus for one string.

So this asks the graph the question the chunker asks its input: a chunk's slice is its
parents' slice, `base` unless **every** parent is incremental — `widest_slice` said once
more, in the graph — and a parentless chunk comes out `base`. That last rule is
conservative in the same direction as the synthetic one: an unattributable chunk survives
`brain reset --slice incremental` and can be removed later, never deleted with its
provenance.

`Entity` is deliberately not stamped. Unlike `synthetic`, an entity has no slice: which
entities an incremental rollback removes is computed from their evidence chunks, before
the sweep, by `brain reset` — one layer up, where an entity merged into a base one keeps
its base evidence and survives.

The command writes one string per node and nothing else. It is idempotent (a second run
stamps 0), takes no lock, needs no embedder and deletes nothing, so unlike `brain reset`
it is safe to run against the real graph.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.chunk import graph as chunk_graph
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso

REPORT_KEY = "slice_stamp"
#: Every run, applying or not. `REPORT_KEY` keeps the last one that changed something.
HISTORY_KEY = "slice_stamp_history"


def plan(ctx: GraphContext) -> dict[str, Any]:
    """What a stamp would change, read-only. The `--dry-run` body."""
    return {"chunks": chunk_graph.slice_drift(ctx)}


def stamp_slice(
    ctx: GraphContext, *, apply: bool = True, echo: Callable[[str], None] = print
) -> dict[str, Any]:
    """Stamp (or, with `apply=False`, only measure) `Chunk.slice`. Returns the section."""
    started = time.perf_counter()
    before = plan(ctx)
    stamped = {"chunks": chunk_graph.stamp_slice(ctx) if apply else 0}
    after = plan(ctx) if apply else before

    section = {
        "at": utc_now_iso(),
        "applied": apply,
        "duration_s": round(time.perf_counter() - started, 2),
        "before": before,
        "stamped": stamped,
        "after": after,
    }
    for line in format_stamp(section):
        echo(line)
    return section


def format_stamp(section: dict[str, Any]) -> list[str]:
    verb = "stamped" if section["applied"] else "would stamp"
    before = section["before"]["chunks"]
    after = section["after"]["chunks"]
    lines = [
        f"slice stamp — {'applied' if section['applied'] else 'DRY RUN'}",
        f"  chunks    {verb} {before['null'] + before['wrong']:>6} "
        f"({before['null']} null, {before['wrong']} wrong) of {before['chunks']}; "
        f"incremental {before['incremental_after']} → {after['incremental_after']}, "
        f"null now {after['null']}",
    ]
    if not section["applied"]:
        lines.append("slice stamp: nothing was written — drop --dry-run to apply.")
    return lines


def stamp_from_settings(
    reports_dir: Path, *, apply: bool = True, echo: Callable[[str], None] = print
) -> tuple[dict[str, Any], int]:
    """Open the graph, stamp, merge the section into `data/reports/chunk.json`.

    No embedder is opened: this reads and writes one property and never touches Ollama.
    """
    from brain.chunk.runner import HISTORY_LIMIT, REPORT_NAME, _merge_report, _read_report
    from brain.config import get_settings
    from brain.graph.client import GraphClient
    from brain.harvest.base import write_json_atomic

    s = get_settings()
    with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
        section = stamp_slice(GraphContext(client), apply=apply, echo=echo)

    path = reports_dir / REPORT_NAME
    reports_dir.mkdir(parents=True, exist_ok=True)
    # `slice_stamp` keeps the run that did work; every run lands in the history. A dry run
    # reports 0 by construction once the backfill has happened, and letting it overwrite
    # the section would erase the only record of the chunks the first run stamped.
    previous = _read_report(path)
    history = [*(previous.get(HISTORY_KEY) or []), section][-HISTORY_LIMIT:]
    kept = previous.get(REPORT_KEY)
    current = section
    if not apply and kept and kept.get("stamped", {}).get("chunks", 0):
        current = {**kept, "superseded_by_a_dry_run_at": section["at"]}
    write_json_atomic(path, _merge_report(path, {REPORT_KEY: current, HISTORY_KEY: history}))
    echo(f"report: {path}")
    # A dry run that found work left to do is a failure the way `brain reset` without
    # `--yes` is: nothing was written, and a script must not read that as "already clean".
    pending = section["before"]["chunks"]["null"] + section["before"]["chunks"]["wrong"]
    return section, (1 if (not apply and pending) else 0)
