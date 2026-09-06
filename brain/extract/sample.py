"""`brain extract sample` — 50 mentions a human can judge in ten minutes.

Precision of a schema-guided extraction is not something the pipeline can measure for
itself: every quote is verbatim by construction (merge rejects the ones that are not), so
the only open question is whether the quote actually *supports* the entity that cites it.
That is a judgement, and this command is the sheet it is made on — the quote, the entity it
was extracted as, and enough of the surrounding chunk to see whether the reading holds.
Acceptance is >= 85% supported (brief 07 decision 7).

The sample is seeded and drawn in Python rather than by `rand()` in Cypher, so "the 50 I
looked at" is a set someone else can look at again.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.extract import names as names_mod
from brain.extract.graph import CHUNK_LABEL, DERIVED_RELATION_TYPE, NODE_KEYS
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic

DEFAULT_N = 50
DEFAULT_SEED = 7
#: Characters of chunk text either side of the quote. Enough to see the sentence it is in.
WINDOW = 220


def _labels(ctx: GraphContext) -> str:
    return " OR ".join(f"e:{ctx.label(label)}" for label in NODE_KEYS)


def all_mentions(ctx: GraphContext) -> list[dict[str, Any]]:
    """Every mention as (chunk id, target label, target key). Ids only — no text yet."""
    return ctx.read(
        f"MATCH (c:{ctx.label(CHUNK_LABEL)})-[r:{DERIVED_RELATION_TYPE}]->(e) "
        f"WHERE {_labels(ctx)} "
        "RETURN c.id AS chunk_id, labels(e) AS labels, "
        "coalesce(e.id, e.key, e.name) AS target ORDER BY chunk_id, target"
    )


def fetch(ctx: GraphContext, picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not picks:
        return []
    return ctx.read(
        f"UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(CHUNK_LABEL)} {{id: row.chunk_id}})"
        f"-[r:{DERIVED_RELATION_TYPE}]->(e)\n"
        "WHERE coalesce(e.id, e.key, e.name) = row.target\n"
        "RETURN c.id AS chunk_id, c.parent_key AS parent_key, c.parent_kind AS parent_kind, "
        "c.kind AS chunk_kind, c.text AS text, r.quote AS quote, r.batch_id AS batch_id, "
        "r.model AS model, labels(e) AS labels, coalesce(e.id, e.key, e.name) AS target, "
        "e.kind AS entity_kind, e.name AS entity_name, e.description AS description",
        rows=[{"chunk_id": p["chunk_id"], "target": p["target"]} for p in picks],
    )


def surround(text: str, quote: str, window: int = WINDOW) -> str:
    """The quote in its sentence, marked with `[[ ]]`. Whitespace-normalised, like the check."""
    body = names_mod.normalise_quote(text)
    needle = names_mod.normalise_quote(quote)
    at = body.find(needle)
    if at < 0:
        return body[: window * 2] + ("…" if len(body) > window * 2 else "")
    start = max(0, at - window)
    end = min(len(body), at + len(needle) + window)
    return (
        ("…" if start else "")
        + body[start:at]
        + "[["
        + body[at : at + len(needle)]
        + "]]"
        + body[at + len(needle) : end]
        + ("…" if end < len(body) else "")
    )


def run_sample(
    *,
    ctx: GraphContext,
    reports_dir: Path,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    population = all_mentions(ctx)
    if not population:
        echo("extract sample: no MENTIONS edges in the graph — run `brain extract merge` first.")
        return {"step": "extract.sample", "population": 0, "sample": []}, 1

    picks = random.Random(seed).sample(population, min(n, len(population)))
    rows = fetch(ctx, picks)
    ordered = sorted(rows, key=lambda r: (r["parent_key"], r["chunk_id"], r["target"]))

    for i, row in enumerate(ordered, 1):
        target = row["entity_name"] or row["target"]
        kind = row["entity_kind"] or (row["labels"] or ["?"])[0]
        echo(f"\n{i:>3}. {kind}: {target}")
        echo(
            f"     {row['parent_kind']} {row['parent_key']} · chunk {row['chunk_id'][:12]} "
            f"· {row['chunk_kind']} · {row['batch_id']}"
        )
        if row["description"]:
            echo(f"     said to be: {row['description']}")
        echo(f"     quote: {row['quote']}")
        echo(f"     text:  {surround(row['text'], row['quote'])}")

    report = {
        "step": "extract.sample",
        "generated_at": utc_now_iso(),
        "seed": seed,
        "requested": n,
        "population": len(population),
        "sample": [
            {
                "chunk_id": r["chunk_id"],
                "parent_key": r["parent_key"],
                "parent_kind": r["parent_kind"],
                "target": r["target"],
                "labels": r["labels"],
                "entity_kind": r["entity_kind"],
                "entity_name": r["entity_name"],
                "description": r["description"],
                "quote": r["quote"],
                "batch_id": r["batch_id"],
                "context": surround(r["text"], r["quote"]),
                "supported": None,
            }
            for r in ordered
        ],
        "note": (
            "Set `supported` to true/false by hand. Acceptance is >= 85% supported "
            "(step brief 07 decision 7). `context` marks the quote with [[ ]]."
        ),
    }
    echo(
        f"\nextract sample: {len(ordered)} of {len(population)} mentions, seed {seed}. "
        "Judge each one: does the quote support the entity?"
    )
    if write_report:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / "extract_sample.json"
        write_json_atomic(path, report)
        echo(f"sheet: {path}")
    return report, 0


def load_sample(reports_dir: Path) -> dict[str, Any] | None:
    path = reports_dir / "extract_sample.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
