"""`lookup(key)` — the tool that needs no strategy at all.

When a question names `KAFKA-15123` the honest first move is to fetch it, not to search
for it. This is also the cheapest correctness check the whole system has: if `lookup` on a
key an answer cited returns nothing, the citation was invented.

Neighbours are capped twice — per relationship type and in total — because "the
neighbourhood" of a well-connected component is most of the graph, and a tool that
sometimes returns four rows and sometimes four thousand cannot live inside a token budget.
Chunks are excluded from the neighbour list and sampled into `provenance` instead: a KIP
has 113 of them and they are evidence, not structure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.keys import key_kind
from brain.retrieve.nodes import key_case, label_case, to_item
from brain.retrieve.types import Item, Provenance, Result, RetrieveError

#: Spec §4.3: "node + neighbourhood (up to 50 neighbours, by type)".
MAX_NEIGHBOURS = 50
PER_TYPE = 12
EVIDENCE_CHUNKS = 3

#: The edge properties a neighbour entry may carry. `properties(r)` is the obvious thing to
#: return and costs 8,542 tokens for one KIP: a `DECIDES` edge carries `evidence_chunk_ids`,
#: `batch_id`, `model`, `extracted_at`, `shard` and `merged_at`, and fifty of those bury the
#: answer. Full edge provenance is what `explain_edge` is for.
EDGE_PROPS: tuple[str, ...] = ("via", "type", "status", "reason", "tier", "score")

#: Labels a key can name, and the property it is keyed on.
LOOKUP_LABELS: tuple[tuple[str, str], ...] = (
    ("WorkItem", "key"),
    ("Document", "key"),
    ("Person", "id"),
    ("Commit", "sha"),
    ("Entity", "id"),
    ("Component", "name"),
    ("Version", "name"),
    ("Sprint", "name"),
    ("Chunk", "id"),
)


def resolve_key(ctx: RetrieveContext, key: str) -> tuple[str, dict[str, Any], str]:
    """Find the one node this key names. Raises when nothing — or nothing unambiguous — does.

    The hint from `key_kind()` is tried first and the rest only as a fallback, so the common
    case is one indexed lookup rather than nine label scans.
    """
    hint = key_kind(key)
    order = [pair for pair in LOOKUP_LABELS if pair[0] == hint]
    order += [pair for pair in LOOKUP_LABELS if pair[0] != hint]
    cyphers: list[str] = []
    for label, prop in order:
        cypher = (
            f"MATCH (n:{ctx.label(label)} {{`{prop}`: $key}})\n"
            f"RETURN {label_case('n', ctx.prefix)} AS label, {key_case('n', ctx.prefix)} AS key,\n"
            "  coalesce(n.title, n.name, n.display, n.message) AS title, n.kind AS kind,\n"
            "  n.status AS status, n.type AS type, n.description AS description,\n"
            "  n.resolution AS resolution, n.source AS source, n.weak AS weak,\n"
            "  toString(n.created) AS created, toString(n.at) AS at,\n"
            "  coalesce(n.evidence_chunk_ids, []) AS evidence_chunk_ids,\n"
            "  n.batch_id AS batch_id, n.model AS model,\n"
            "  coalesce(n.synthetic, false) AS synthetic LIMIT 1"
        )
        cyphers.append(cypher)
        rows = ctx.read(cypher, key=key)
        if rows:
            return label, rows[0], cypher
    raise RetrieveError(f"no node in the graph has the key {key!r}")


def neighbourhood(ctx: RetrieveContext, label: str, key: str) -> tuple[list[dict[str, Any]], str]:
    """Up to `PER_TYPE` neighbours per relationship type, `MAX_NEIGHBOURS` in total."""
    cypher = (
        f"MATCH (n:{ctx.label(label)}) WHERE {key_case('n', ctx.prefix)} = $key\n"
        f"MATCH (n)-[r]-(m) WHERE NOT m:{ctx.label('Chunk')}\n"
        "WITH type(r) AS rel,\n"
        "  CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction,\n"
        f"  {label_case('m', ctx.prefix)} AS label, {key_case('m', ctx.prefix)} AS key,\n"
        "  coalesce(m.title, m.name, m.display, m.message) AS title,\n"
        "  m.status AS status, m.kind AS kind,\n"
        f"  {{{', '.join(f'`{prop}`: r.`{prop}`' for prop in EDGE_PROPS)}}} AS edge\n"
        "WITH rel, direction, collect(DISTINCT {label: label, key: key,\n"
        "  title: left(title, 120), status: status, kind: kind,\n"
        "  edge: [k IN keys(edge) WHERE edge[k] IS NOT NULL | k + '=' + toString(edge[k])]\n"
        "})[..$per_type] AS neighbours\n"
        "RETURN rel, direction, neighbours, size(neighbours) AS n ORDER BY rel, direction"
    )
    return ctx.read(cypher, key=key, per_type=PER_TYPE), cypher


def evidence_chunks(ctx: RetrieveContext, label: str, key: str) -> tuple[list[dict[str, Any]], str]:
    """A few chunks that are *about* this node — its own text, or a chunk that mentions it."""
    cypher = (
        f"MATCH (n:{ctx.label(label)}) WHERE {key_case('n', ctx.prefix)} = $key\n"
        f"OPTIONAL MATCH (n)-[:HAS_CHUNK]->(own:{ctx.label('Chunk')})\n"
        "  WHERE coalesce(own.orphaned, false) = false\n"
        f"OPTIONAL MATCH (said:{ctx.label('Chunk')})-[m:MENTIONS]->(n)\n"
        "  WHERE coalesce(said.orphaned, false) = false\n"
        "WITH collect(DISTINCT {id: own.id, text: own.text, quote: null})[..$k] AS own_chunks,\n"
        "  collect(DISTINCT {id: said.id, text: said.text, quote: m.quote})[..$k] AS said_chunks\n"
        "RETURN own_chunks + said_chunks AS chunks"
    )
    rows = ctx.read(cypher, key=key, k=EVIDENCE_CHUNKS)
    chunks = [c for c in (rows[0]["chunks"] if rows else []) if c.get("id")]
    return chunks[: EVIDENCE_CHUNKS * 2], cypher


def _compact(neighbour: dict[str, Any]) -> dict[str, Any]:
    """Drop the nulls. Fifty `"kind": null` entries are 200 tokens of nothing."""
    return {k: v for k, v in neighbour.items() if v not in (None, [], "")}


def lookup(
    ctx: RetrieveContext,
    key: str,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """The node this key names, its capped neighbourhood, and a few evidence chunks."""
    timer = Timer()
    label, row, resolve_cypher = resolve_key(ctx, key)
    groups, neighbour_cypher = neighbourhood(ctx, label, row["key"])
    chunks, chunk_cypher = evidence_chunks(ctx, label, row["key"])

    node = {k: v for k, v in row.items() if k not in ("label",)}
    if label == "Commit":
        node["sha"], node["message"] = row["key"], row["title"]
    elif label == "Entity":
        node["id"], node["name"] = row["key"], row["title"]
    elif label == "Person":
        node["id"], node["display"] = row["key"], row["title"]
    elif label in ("Component", "Version", "Sprint", "Space", "Area"):
        node["name"] = row["key"]
    else:
        node["key"] = row["key"]

    item = to_item(label, node, 1.0)
    total = 0
    neighbours: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        if total >= MAX_NEIGHBOURS:
            break
        room = MAX_NEIGHBOURS - total
        picked = [_compact(n) for n in group["neighbours"][:room]]
        neighbours.setdefault(f"{group['rel']}:{group['direction']}", []).extend(picked)
        total += len(picked)
    item.props["neighbors"] = neighbours
    item.props["neighbor_count"] = total
    item.props["neighbor_types"] = sorted({g["rel"] for g in groups})
    item.provenance.extend(
        Provenance(chunk_id=c["id"], quote=(c.get("quote") or c.get("text") or "")[:280])
        for c in chunks
    )

    items: list[Item] = [item]
    items.extend(
        to_item("Chunk", {"id": c["id"], "text": c.get("text"), "parent_key": row["key"]}, 0.5)
        for c in chunks[:EVIDENCE_CHUNKS]
    )
    return finish(
        "lookup",
        items,
        question=f"lookup({key})",
        timer=timer,
        cypher_used=[resolve_cypher, neighbour_cypher, chunk_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
