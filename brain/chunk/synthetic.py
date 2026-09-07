"""`brain chunk --stamp-synthetic` — bring `Chunk.synthetic` / `Entity.synthetic` up to date.

ADR-0005 §5 makes `synthetic` the switch `brain reset --synthetic` deletes on, and the
flag has to travel the whole way down the chain: a canonical record carries it, the chunks
of that record inherit it, and an entity extracted only from those chunks inherits it in
turn. The chunker and the extract merge both do that *when they write* — but the corpus
was chunked and extracted before the property existed, so 13,846 live chunks carried
`null` and every entity carried `false`. `brain reset --synthetic` then had nothing to
match on and fell back to "the parent is gone", which finds the chunks and leaves the
entities behind: 1,882 orphans in the review.

Re-chunking would fix it and would re-embed the corpus for one boolean. This asks the
graph the same question the writers ask their inputs:

* a chunk is synthetic when it has parents and **every** parent is synthetic;
* an entity is synthetic when it has surviving evidence and **every** evidence chunk is.

Both directions are conservative on purpose — an unattributable node comes out *real*, so
the failure mode is a node that survives a reset and can be deleted later, never one that
is deleted with its provenance.

The command writes one boolean per node and nothing else. It is idempotent (a second run
stamps 0), it takes no lock and deletes nothing, so unlike `brain reset` it is safe to run
against the real graph — which is the point: the backfill is what makes the reset correct.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.chunk import graph as chunk_graph
from brain.extract import graph as extract_graph
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso

REPORT_KEY = "synthetic_stamp"
#: Every run, applying or not. `REPORT_KEY` keeps the last one that changed something.
HISTORY_KEY = "synthetic_stamp_history"


def plan(ctx: GraphContext) -> dict[str, Any]:
    """What a stamp would change, read-only. The `--dry-run` body."""
    return {
        "chunks": chunk_graph.synthetic_drift(ctx),
        "entities": extract_graph.synthetic_entity_drift(ctx),
    }


def stamp_synthetic(
    ctx: GraphContext, *, apply: bool = True, echo: Callable[[str], None] = print
) -> dict[str, Any]:
    """Stamp (or, with `apply=False`, only measure) both labels. Returns the report section.

    Chunks first and entities second, always: the entity rule reads `Chunk.synthetic`, so
    stamping entities against un-stamped chunks would write `false` everywhere again —
    which is precisely how the graph got into this state.
    """
    started = time.perf_counter()
    before = plan(ctx)
    stamped: dict[str, int] = {"chunks": 0, "entities": 0}
    if apply:
        stamped["chunks"] = chunk_graph.stamp_synthetic(ctx)
        stamped["entities"] = extract_graph.stamp_entity_synthetic(ctx)
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
    lines = [f"synthetic stamp — {'applied' if section['applied'] else 'DRY RUN'}"]
    for label, key in (("chunks", "chunks"), ("entities", "entities")):
        before = section["before"][label]
        after = section["after"][label]
        lines.append(
            f"  {label:<9} {verb} {before['null'] + before['wrong']:>6} "
            f"({before['null']} null, {before['wrong']} wrong) of {before[key]}; "
            f"synthetic {before['synthetic_after']} → {after['synthetic_after']}, "
            f"null now {after['null']}"
        )
    if not section["applied"]:
        lines.append("synthetic stamp: nothing was written — drop --dry-run to apply.")
    return lines


def stamp_from_settings(
    reports_dir: Path, *, apply: bool = True, echo: Callable[[str], None] = print
) -> tuple[dict[str, Any], int]:
    """Open the graph, stamp, merge the section into `data/reports/chunk.json`.

    No embedder is opened: this step reads and writes one property and never touches
    Ollama, so it must work on a machine where the model is not pulled.
    """
    from brain.chunk.runner import HISTORY_LIMIT, REPORT_NAME, _merge_report, _read_report
    from brain.config import get_settings
    from brain.graph.client import GraphClient
    from brain.harvest.base import write_json_atomic

    s = get_settings()
    with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
        section = stamp_synthetic(GraphContext(client), apply=apply, echo=echo)

    path = reports_dir / REPORT_NAME
    reports_dir.mkdir(parents=True, exist_ok=True)
    # `synthetic_stamp` keeps the run that did work; every run lands in the history. A dry
    # run reports 0 by construction once the backfill has happened, and letting it
    # overwrite the section would erase the only record of the 13,846 chunks the first
    # run stamped — the number the whole fix is judged on.
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
    pending = sum(
        section["before"][label]["null"] + section["before"][label]["wrong"]
        for label in ("chunks", "entities")
    )
    return section, (1 if (not apply and pending) else 0)
