"""What the retrieval really returned, so "not in the graph" can stop meaning two things.

The measurement window of Plan 3 was not frozen: the incremental slice ran between the
retrieval sweep (09:35) and the citation check (10:13), and `brain communities build` is a
global re-partition — 645 community `member_hash` values changed and 231 communities ceased
to exist. Three community ids that the retrieval demonstrably returned were therefore absent
from the graph the checker asked, and were reported as invented citations.

Conventions, "לקחים מסקירת סגירת Plan 1": *a check that ran against a different graph
reports `in_retrieval_snapshot` beside `in_graph_now`, not "not in the graph"*. This module
is the snapshot half of that sentence, and it has exactly two sources, because mode A and
mode B recorded different things:

* **mode A** packed a context and stored its ids in the answer file (`context_keys`). That
  list *is* the snapshot for that case — it is what the answering model could see.
* **mode B** chose its own tools and packed nothing, so the only record of what it was
  shown is `data/logs/retrieval.jsonl`. Only the `mcp` lines are the agent's: the `eval`
  lines are the fixed-strategy sweep and the `python`/`cli` lines are development.

Nothing here talks to the graph, and a missing log is an empty snapshot rather than an
error — a citation nobody can place is reported as unplaceable, which is the honest verdict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Where an id was found, for the report's "how do you know" column.
CONTEXT = "context"
MCP_TRACE = "mcp trace"
NOWHERE = "none"
#: `mode` of a trace line written by the MCP server — mode B's own calls, and only those.
AGENTIC_LOG_MODE = "mcp"


def _tails(hit_id: str) -> list[str]:
    """`"Community:L0-1436"` -> both spellings, because a citation writes only the second."""
    text = str(hit_id).strip()
    if not text:
        return []
    head, sep, tail = text.partition(":")
    return [text, tail] if sep and head and head[:1].isupper() else [text]


def mcp_hit_ids(log_path: Path | str | None) -> tuple[set[str], int]:
    """Every id any `mcp` call returned, and how many such lines were read.

    A malformed line is skipped rather than raised on: this is a trace file that several
    processes append to, and one truncated write must not make the whole snapshot unusable.
    """
    if log_path is None:
        return set(), 0
    path = Path(log_path)
    if not path.is_file():
        return set(), 0
    found: set[str] = set()
    lines = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping) or record.get("mode") != AGENTIC_LOG_MODE:
                continue
            lines += 1
            for hit in record.get("hit_ids") or []:
                found.update(_tails(str(hit)))
    return found, lines


@dataclass
class RetrievalSnapshot:
    """The ids each answer was actually shown, keyed by case.

    `contains` is deliberately the same match rule the answers merge uses
    (`cited_key_matches`): a truncated chunk prefix cites the chunk it is a prefix of, and
    a check that said otherwise would call a habit a hallucination.
    """

    by_case: dict[str, set[str]] = field(default_factory=dict)
    agentic: set[str] = field(default_factory=set)
    log_path: str = ""
    mcp_lines: int = 0

    @classmethod
    def build(
        cls,
        answers: Sequence[Mapping[str, Any]],
        *,
        log_path: Path | str | None = None,
    ) -> RetrievalSnapshot:
        by_case: dict[str, set[str]] = {}
        needs_log = False
        for answer in answers:
            case_id = str(answer.get("case_id") or "")
            if not case_id:
                continue
            if answer.get("context_available"):
                by_case[case_id] = {str(k) for k in (answer.get("context_keys") or [])}
            else:
                by_case[case_id] = set()
                needs_log = True
        agentic, lines = mcp_hit_ids(log_path if needs_log else None)
        return cls(
            by_case=by_case,
            agentic=agentic,
            log_path=str(log_path or ""),
            mcp_lines=lines,
        )

    def keys_for(self, case_id: str) -> set[str]:
        """The snapshot of one case: its own context, or mode B's whole MCP trace."""
        own = self.by_case.get(str(case_id))
        return own if own else self.agentic

    def source(self, case_id: str) -> str:
        if self.by_case.get(str(case_id)):
            return CONTEXT
        return MCP_TRACE if self.agentic else NOWHERE

    def contains(self, case_id: str, value: str) -> bool:
        from brain.eval.answers_batches import cited_key_matches

        keys = self.keys_for(case_id)
        return bool(keys) and cited_key_matches(str(value), keys) is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": len(self.by_case),
            "cases_with_a_context": sum(1 for v in self.by_case.values() if v),
            "mcp_lines": self.mcp_lines,
            "mcp_ids": len(self.agentic),
            "log": self.log_path,
            "note": (
                "Mode A's snapshot is the answer's own `context_keys`; mode B recorded no "
                "context, so its snapshot is every id an `mcp` trace line returned."
            ),
        }


def build(
    answers: Iterable[Mapping[str, Any]], *, log_path: Path | str | None = None
) -> RetrievalSnapshot:
    return RetrievalSnapshot.build(list(answers), log_path=log_path)


__all__ = [
    "AGENTIC_LOG_MODE",
    "CONTEXT",
    "MCP_TRACE",
    "NOWHERE",
    "RetrievalSnapshot",
    "build",
    "mcp_hit_ids",
]
