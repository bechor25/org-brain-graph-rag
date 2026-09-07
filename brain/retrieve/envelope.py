"""Start a `Result`, stop the clock, spend the budget, write the trace.

Every strategy ends the same three ways — measure how long it took, trim to ~4k tokens,
append one JSONL line — and a strategy that forgets one of them is a strategy that cannot
be compared with the others. Putting it here means the MCP tools in Task 3 inherit the
same behaviour by calling the same functions, which is plan decision 1: no logic that
exists only in the server.

`latency_ms` is wall clock and includes the Ollama round trip, because that is what the
asking agent waits for. A latency number that excludes the embedding call would flatter
every vector strategy against the deterministic ones.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from brain.retrieve.log import log_call
from brain.retrieve.pack import pack, total_tokens
from brain.retrieve.types import Item, Provenance, Result


class Timer:
    """Wall-clock milliseconds around one retrieval, embedding call included."""

    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def finish(
    strategy: str,
    items: list[Item],
    *,
    question: str,
    timer: Timer,
    cypher_used: list[str] | None = None,
    route: dict[str, Any] | None = None,
    mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
) -> Result:
    """Pack to the budget, stamp the latency, log the call, return the envelope."""
    for item in items:
        item.provenance = dedupe_provenance(item.provenance)
    packed, truncated = pack(items)
    result = Result(
        strategy=strategy,
        items=packed,
        cypher_used=_unique(cypher_used or []),
        latency_ms=timer.ms,
        truncated=truncated,
        route=route,
    )
    if log:
        log_call(
            question,
            result,
            mode=mode,
            tokens_out=total_tokens(packed),
            path=log_path,
        )
    return result


def _unique(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        if v:
            seen.setdefault(v, None)
    return list(seen)


def dedupe_provenance(entries: list[Provenance]) -> list[Provenance]:
    """One entry per chunk, the quoted one winning.

    S3 gets the same chunk twice — once from `Entity.evidence_chunk_ids` (which knows the
    id but not the words) and once from `MENTIONS.quote` (which knows both). Two entries
    for one chunk is not more evidence, it is the same evidence twice, and it costs the
    token budget the second time.
    """
    best: dict[str, Provenance] = {}
    order: list[str] = []
    for entry in entries:
        key = entry.chunk_id or f"__{len(order)}"
        if key not in best:
            best[key] = entry
            order.append(key)
            continue
        current = best[key]
        merged = current.model_copy(
            update={
                "quote": current.quote or entry.quote,
                "batch_id": current.batch_id or entry.batch_id,
                "model": current.model or entry.model,
                "source": current.source or entry.source,
            }
        )
        best[key] = merged
    return [best[k] for k in order]
