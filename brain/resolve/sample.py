"""`brain resolve sample` — the merges nobody can grade, printed for a human.

`brain resolve eval` scores the pairs the gold covers and counts everything else as
`ungraded_merges`: 1,200 of them after tiers 1-2, almost all real Jira/git/Confluence
duplicates the synthetic truth says nothing about. They are the majority of what this step
did, and no number in the report is about them. This prints a deterministic sample of
those groups — every identity's display, source and the items it touched — so the planner
can read a few dozen and say whether they are right.

Only groups the gold cannot reach are shown. A group already covered by a gold pair has a
number attached to it and does not need eyes.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from brain.canon.io import read_jsonl
from brain.canon.models import Person
from brain.graph.context import GraphContext
from brain.resolve import graph as resolve_graph
from brain.resolve.gold import GOLD_NAME, read_gold
from brain.resolve.ledger import ResolutionLedger
from brain.resolve.models import Candidate

#: Items to show per identity. Enough to recognise a person, short enough to scan.
EVIDENCE_PER_IDENTITY = 3


def canonical_displays(canonical_dir: Path, kind: str) -> dict[str, str]:
    """id -> display, from the canonical file.

    The graph cannot answer this: the identities a merge swallowed have no node left. The
    canonical file still holds all of them, and their display forms are exactly what a
    human reviewing a merge needs to see.
    """
    path = canonical_dir / f"{kind}s.jsonl"
    if kind != "person" or not path.is_file():
        return {}
    out: dict[str, str] = {}
    for person in read_jsonl(path, Person):
        first = person.identities[0] if person.identities else None
        out[person.id] = (first.display if first and first.display else None) or person.id
    return out


def merged_groups(ledger: ResolutionLedger, kind: str) -> list[list[str]]:
    """canonical id first, then the identities merged into it."""
    return [
        [canonical, *members] for canonical, members in sorted(ledger.merged_into(kind).items())
    ]


def graded_pairs(eval_dir: Path, kind: str) -> set[tuple[str, str]]:
    return {
        (row["a"], row["b"]) for row in read_gold(eval_dir / GOLD_NAME) if row.get("kind") == kind
    }


def is_ungraded(group: Sequence[str], graded: set[tuple[str, str]]) -> bool:
    """True when no pair inside this group appears in the gold at all."""
    return not any(
        tuple(sorted(pair)) in graded for pair in itertools.combinations(sorted(group), 2)
    )


def render(
    group: Sequence[str],
    by_id: dict[str, Candidate],
    ledger: ResolutionLedger,
    displays: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    displays = displays or {}
    canonical = group[0]
    survivor = by_id.get(canonical)
    head = survivor.name if survivor else displays.get(canonical, "?")
    lines.append(f"{canonical}   ← {len(group) - 1} merged   [{head}]")
    for identity in group:
        entry = ledger.section("person").get(identity) or {}
        node = by_id.get(identity)
        display = node.name if node else displays.get(identity, "(display unknown)")
        source = identity.split(":", 1)[0]
        mark = (
            "survivor"
            if identity == canonical
            else (f"tier {entry.get('tier')} {entry.get('rule')} {entry.get('score')}")
        )
        lines.append(f"    {source:<11} {identity:<52} {display!r}   {mark}")
        if node is not None:
            for item in node.evidence[:EVIDENCE_PER_IDENTITY]:
                flag = " (synthetic)" if item.synthetic else ""
                lines.append(f"        {item.role.lower():<12} {item.key:<16} {item.title}{flag}")
    return "\n".join(lines)


def run_sample(
    ctx: GraphContext,
    *,
    canonical_dir: Path,
    eval_dir: Path,
    kind: str = "person",
    n: int = 40,
    seed: int = 7,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Print `n` merged groups the gold cannot grade. Same seed, same sample."""
    ledger = ResolutionLedger.load(canonical_dir)
    if not ledger.available:
        echo(f"sample: no resolution ledger in {canonical_dir} — nothing has been merged yet.")
        return {"groups": 0, "ungraded": 0, "shown": 0}, 0

    graded = graded_pairs(eval_dir, kind)
    all_groups = merged_groups(ledger, kind)
    ungraded = [g for g in all_groups if is_ungraded(g, graded)]
    chosen = sorted(random.Random(seed).sample(ungraded, min(n, len(ungraded))))

    # The evidence has to come from the graph: the swallowed nodes are gone, so only the
    # survivor can still say what the merged person touched.
    by_id = {c.id: c for c in resolve_graph.read_candidates(ctx, kind)}
    displays = canonical_displays(canonical_dir, kind)
    echo(
        f"{len(all_groups)} merged groups, {len(ungraded)} of them ungraded "
        f"(no pair in {eval_dir / GOLD_NAME}); showing {len(chosen)} (seed {seed})\n"
    )
    for group in chosen:
        echo(render(group, by_id, ledger, displays))
        echo("")
    return {
        "groups": len(all_groups),
        "ungraded": len(ungraded),
        "shown": len(chosen),
        "seed": seed,
        "sampled": [g[0] for g in chosen],
    }, 0
