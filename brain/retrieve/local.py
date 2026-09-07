"""S3 — entity-anchored local search. The strategy the rationale questions need.

"Why was the incremental rebalance protocol chosen in KIP-848?" has no good vector answer.
The sentence that states the decision does not resemble the question; the sentence that
*rejects* the alternatives resembles it even less. What answers it is a two-hop walk from
the document: `KIP-848 -[:DECIDES]-> Decision -[:MOTIVATED_BY]-> Problem` and
`KIP-848 -[:REJECTS]-> Alternative`, every edge carrying the chunk it was extracted from
and the verbatim quote.

Anchoring has two modes and the cheap one runs first. If the question names a key, the
anchor is that node — exact, free, and the reason the Hebrew and English forms of the same
question return the same subgraph. Only when nothing is named does the question get
embedded and matched against `entity_embedding`.

**Plan decision 8** is why `MENTIONS` is in the relation set at all. 81% of extracted
`Decision`s are `weak = true` — no `MOTIVATED_BY`, no `REJECTS` — because in this corpus
the reasons hang off the `Feature`, not off the decision. Walking only the "proper"
rationale edges would answer 19% of the rationale questions. Walking `MENTIONS` too brings
back the chunk that says why, with its quote.

Ranking is degree × edge weight × (1 + cos(question, entity)), where *degree is inside the
anchored neighbourhood*, not in the graph. Global degree would rank `Technology|kafka` first
for every question ever asked; local degree ranks the node the anchors actually agree on.

The third factor arrived after a measurement (planner decision, Plan 2 Task 3): with only
the first two, these two questions returned byte-identical lists —

    "Why was the design in KIP-848 chosen?"
    "Which commits fixed the bug behind KIP-848 rollout issues?"

— because the key picked the neighbourhood and nothing after that read the question. A
retrieval whose output does not depend on what was asked cannot be ranked, evaluated or
trusted. So the *subgraph* is still chosen by the anchor and the edges, which is what keeps
the Hebrew and English forms of one question on the same nodes, and the question's embedding
decides only the order inside it. The cosine spread over real `bge-m3` vectors is roughly
[1.3, 1.7], much narrower than the edge-weight spread ([0.35, 1.0] per hop and summed over
degree), so this breaks ties between comparable nodes rather than overturning the graph.

`vector.similarity.cosine` is computed *in the database*, not by shipping 1,024 floats per
neighbour back to Python. A node with no embedding — every `WorkItem`, `Document`, `Person`
and `Component` — gets the neutral factor 1.0 rather than a zero, because "no vector" is an
absence of evidence, not evidence of irrelevance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import ENTITY_INDEX, RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.evidence import as_provenance, own_chunks
from brain.retrieve.keys import find_keys
from brain.retrieve.nodes import key_case, label_case, to_item
from brain.retrieve.types import EmbedModelMismatch, Item, Provenance, Result
from brain.retrieve.vector import search_entity_vectors

#: The rationale/impact edges (spec §4.1 S3) and what each is worth in the ranking.
#: `MENTIONS` is the weakest on purpose: co-occurrence in one chunk is evidence, not a
#: claim, and it is also by far the most common edge (9,390 of them).
RELATION_WEIGHTS: dict[str, float] = {
    "DECIDES": 1.0,
    "MOTIVATED_BY": 1.0,
    "REJECTS": 1.0,
    "IMPLEMENTS": 0.9,
    "TESTS": 0.9,
    "RESOLVES": 0.9,
    "DEPENDS_ON": 0.8,
    "INTRODUCES_RISK": 0.8,
    "MENTIONS": 0.35,
}
#: How much a second hop is worth relative to the first.
HOP_DECAY = 0.55
#: Neighbours collected per anchor before ranking.
PER_ANCHOR = 250
#: Entities used as anchors when the question names no key.
ENTITY_ANCHORS = 6
#: Labels a local-search result may be.
RESULT_LABELS = ("Entity", "WorkItem", "Document", "Component", "Person")
#: What a node with no embedding is worth in the semantic factor — the neighbourhood's own
#: average, which is what `1.0` means once the factor is re-centred (see `semantic_factor`).
NEUTRAL_FACTOR = 1.0
#: Neo4j's normalised cosine for two orthogonal vectors, i.e. the plain `cos = 0` zero point.
NEUTRAL_SIMILARITY = 0.5
#: A question may demote a node the graph found, but not erase it.
MIN_FACTOR = 0.05


def _anchor_from_keys(ctx: RetrieveContext, question: str) -> tuple[list[dict[str, Any]], str]:
    """Every node the question names by key, resolved in one query. Empty when it names none."""
    keys = find_keys(question)
    values = [*keys.documents, *keys.workitems, *keys.shas, *keys.persons, *keys.entities]
    names = list(keys.quoted)
    if not values and not names:
        return [], ""
    cypher = (
        f"MATCH (a) WHERE ({' OR '.join(f'a:{ctx.label(x)}' for x in RESULT_LABELS)})\n"
        "  AND (a.key IN $values OR a.id IN $values OR a.sha IN $values\n"
        "       OR toLower(a.name) IN $names)\n"
        f"RETURN {key_case('a', ctx.prefix)} AS key, {label_case('a', ctx.prefix)} AS label,\n"
        "  coalesce(a.title, a.name, a.display) AS title, a.kind AS kind, a.status AS status,\n"
        "  a.type AS type, a.description AS description, a.weak AS weak,\n"
        "  coalesce(a.synthetic, false) AS synthetic"
    )
    rows = ctx.read(cypher, values=values, names=[n.lower() for n in names])
    return rows, cypher


def _question_vector(ctx: RetrieveContext, question: str) -> list[float] | None:
    """The question's embedding, or `None` when the embedder is unreachable.

    A model *mismatch* stays fatal — cosine over two unrelated spaces is not a worse ranking,
    it is a meaningless one — but Ollama being down should cost S3 its ordering, not its
    answer: the anchor and the edges still know what the neighbourhood is. Which happened is
    visible per item as `props.semantic == 1.0` on every result.
    """
    try:
        return ctx.embed_query(question, ENTITY_INDEX)
    except EmbedModelMismatch:
        raise
    except Exception:  # noqa: BLE001 - a dead embedder degrades the rank, not the answer
        return None


def _anchor_from_vector(
    ctx: RetrieveContext,
    question: str,
    kinds: list[str] | None,
    k: int,
    vector: list[float] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    vector = vector if vector is not None else ctx.embed_query(question, ENTITY_INDEX)
    rows = search_entity_vectors(ctx, vector, k=k, kinds=kinds)
    out = [
        {
            "key": r["node"]["id"],
            "label": "Entity",
            "title": r["node"].get("name"),
            "kind": r["node"].get("kind"),
            "description": r["node"].get("description"),
            "weak": r["node"].get("weak"),
            "synthetic": r["node"].get("synthetic"),
            "score": float(r["score"]),
            # The index already measured this anchor against the question; reusing it is the
            # same number `vector.similarity.cosine` would return for it.
            "sim": float(r["score"]),
        }
        for r in rows
    ]
    return out, rows[0]["cypher"] if rows else ""


def _expand(
    ctx: RetrieveContext,
    anchors: list[str],
    depth: int,
    kinds: list[str] | None,
    vector: list[float] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if not anchors:
        return [], ""
    depth = max(1, min(int(depth), 2))
    rels = "|".join(f"`{r}`" for r in RELATION_WEIGHTS)
    result_pred = " OR ".join(f"n:{ctx.label(x)}" for x in RESULT_LABELS)
    kind_pred = f" AND (NOT n:{ctx.label('Entity')} OR n.kind IN $kinds)" if kinds else ""
    # Cosine in the database. The alternative — returning `n.embedding` and comparing in
    # Python — ships 1,024 floats for each of up to 250 neighbours per anchor to rank them.
    similarity = (
        "CASE WHEN $vector IS NOT NULL AND n.`embedding` IS NOT NULL\n"
        "    THEN vector.similarity.cosine(n.`embedding`, $vector) END AS sim"
    )
    cypher = (
        f"MATCH (a) WHERE {key_case('a', ctx.prefix)} IN $anchors\n"
        f"  AND ({' OR '.join(f'a:{ctx.label(x)}' for x in RESULT_LABELS)})\n"
        "CALL (a) {\n"
        f"  MATCH (a)-[rels:{rels}*1..{depth}]-(n)\n"
        f"  WHERE ({result_pred}){kind_pred}\n"
        "  RETURN n, rels ORDER BY size(rels) LIMIT $per_anchor\n"
        "}\n"
        f"RETURN {key_case('a', ctx.prefix)} AS anchor, {key_case('n', ctx.prefix)} AS key,\n"
        f"  {label_case('n', ctx.prefix)} AS label, size(rels) AS hops,\n"
        "  [r IN rels | type(r)] AS path,\n"
        "  coalesce(n.title, n.name, n.display) AS title, n.kind AS kind, n.status AS status,\n"
        "  n.type AS type, n.description AS description, n.weak AS weak,\n"
        "  coalesce(n.synthetic, false) AS synthetic,\n"
        "  coalesce(n.evidence_chunk_ids, []) AS evidence_chunk_ids,\n"
        "  n.batch_id AS batch_id, n.model AS model,\n"
        f"  {similarity}"
    )
    rows = ctx.read(cypher, anchors=anchors, per_anchor=PER_ANCHOR, kinds=kinds, vector=vector)
    return rows, cypher


def _evidence(ctx: RetrieveContext, keys: list[str], per_key: int = 2):
    """Up to `per_key` quoted chunks per result — the `MENTIONS.quote` the extractor kept."""
    if not keys:
        return {}, ""
    cypher = (
        "UNWIND $keys AS k\n"
        f"MATCH (t) WHERE ({' OR '.join(f't:{ctx.label(x)}' for x in RESULT_LABELS)})\n"
        f"  AND {key_case('t', ctx.prefix)} = k\n"
        "CALL (t) {\n"
        f"  MATCH (c:{ctx.label('Chunk')})-[m:MENTIONS]->(t)\n"
        "  WHERE coalesce(c.orphaned, false) = false AND m.quote IS NOT NULL\n"
        "  RETURN c, m ORDER BY size(coalesce(m.quote, '')) DESC LIMIT $per_key\n"
        "}\n"
        "RETURN k AS key, c.id AS chunk_id, c.parent_key AS parent_key, m.quote AS quote,\n"
        "  m.batch_id AS batch_id, m.model AS model"
    )
    rows = ctx.read(cypher, keys=keys, per_key=per_key)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["key"], []).append(row)
    return out, cypher


def _path_weight(path: list[str]) -> float:
    weight = 1.0
    for hop, rel in enumerate(path):
        weight *= RELATION_WEIGHTS.get(rel, 0.5) * (HOP_DECAY**hop)
    return weight


def semantic_factor(similarity: float | None, center: float = NEUTRAL_SIMILARITY) -> float:
    """`1 + cos(question, node)`, re-centred on this neighbourhood's own average cosine.

    `vector.similarity.cosine` returns `(1 + cos) / 2` in `[0, 1]` — verified against
    2026.06.0, which answers 1.0, 0.5 and 0.0 for identical, orthogonal and opposite vectors
    — so `1 + cos` is `2 × similarity`, and `center` is where that product is worth exactly
    1.0. At the default `center = 0.5` this *is* the plan's formula: `1 + cos`, neutral at
    `cos = 0`.

    `rank_neighbourhood` passes the neighbourhood's mean instead, and that is a deliberate
    deviation with two measurements behind it:

    * `bge-m3` cosines live in a narrow band well above zero (0.45–0.60 raw for a related
      pair here), so the literal formula multiplies every embedded node by ~1.5 and every
      node that *has* no embedding — each `WorkItem`, `Document`, `Person`, `Component` — by
      exactly 1.0. That is not a ranking signal, it is a 50% bonus for being an `Entity`,
      and it pushed the anchor document itself out of its own top-5.
    * A Hebrew question sits systematically lower on that band than its English twin, so the
      absolute level moves with the language while the *order* barely does. Keeping the
      literal zero point broke the cross-lingual criterion S3 exists to satisfy (identical
      anchors for all four translated pairs); re-centring restores it, because a constant
      subtracted from every node of one question changes no comparison inside it.

    The floor keeps a distant node demoted rather than annihilated: the graph found it
    through a real edge, and the question is not entitled to overrule that entirely.
    """
    if similarity is None:
        return NEUTRAL_FACTOR
    return max(MIN_FACTOR, 1.0 + 2.0 * (float(similarity) - center))


def rank_neighbourhood(
    anchors: list[dict[str, Any]], rows: list[dict[str, Any]], k: int
) -> list[tuple[str, dict[str, Any]]]:
    """degree × edge weight × (1 + cos), highest first. Pure: the graph is already read.

    Both halves of the product are kept on the entry (`degree_score`, `semantic`) because a
    ranking whose numbers cannot be read back is a ranking nobody can debug — and Plan 3 has
    to explain, per question type, *why* one strategy beat another.
    """
    scored: dict[str, dict[str, Any]] = {}
    anchor_keys = {a["key"] for a in anchors}
    for anchor in anchors:
        scored[anchor["key"]] = {
            "row": anchor,
            "degree_score": 1.0 + float(anchor.get("score") or 0.0),
            "paths": [],
        }
    for row in rows:
        key = row["key"]
        if key is None or key in anchor_keys:
            continue
        entry = scored.setdefault(key, {"row": row, "degree_score": 0.0, "paths": []})
        entry["degree_score"] += _path_weight(row["path"])
        entry["paths"].append({"anchor": row["anchor"], "path": row["path"], "hops": row["hops"]})

    center = _center([e["row"].get("sim") for e in scored.values()])
    for entry in scored.values():
        entry["semantic"] = semantic_factor(entry["row"].get("sim"), center)
        entry["score"] = entry["degree_score"] * entry["semantic"]
        entry["path_count"] = len(entry["paths"])
    # The key breaks ties, so two runs over the same graph return the same order even though
    # Neo4j promises no row order.
    return sorted(scored.items(), key=lambda kv: (-kv[1]["score"], kv[0]))[:k]


def _center(similarities: list[float | None]) -> float:
    """Where `1 + cos` is worth 1.0: this neighbourhood's mean cosine, or the plain zero."""
    values = [float(s) for s in similarities if s is not None]
    return sum(values) / len(values) if values else NEUTRAL_SIMILARITY


def local_search(
    ctx: RetrieveContext,
    query: str,
    kinds: list[str] | None = None,
    depth: int = 2,
    k: int = 10,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Anchor on named keys (or on the nearest entities), walk ≤`depth`, rank, quote."""
    timer = Timer()
    cyphers: list[str] = []

    anchors, cypher = _anchor_from_keys(ctx, query)
    anchored_by = "keys"
    if cypher:
        cyphers.append(cypher)
    # The question is embedded even when the anchor was free, because the third ranking
    # factor needs it. A key-anchored search used to skip Ollama entirely; the round trip is
    # ~70 ms and it is what makes two questions about one key different answers.
    vector = _question_vector(ctx, query)
    if not anchors:
        anchored_by = "entity-vector"
        anchors, cypher = _anchor_from_vector(ctx, query, kinds, ENTITY_ANCHORS, vector)
        if cypher:
            cyphers.append(cypher)
    if not anchors:
        return finish(
            "s3",
            [],
            question=query,
            timer=timer,
            cypher_used=cyphers,
            route=route,
            mode=log_mode,
            log_path=log_path,
            log=log,
        )

    anchor_keys = [a["key"] for a in anchors]
    rows, cypher = _expand(ctx, anchor_keys, depth, kinds, vector)
    if cypher:
        cyphers.append(cypher)

    top = rank_neighbourhood(anchors, rows, k)
    evidence, cypher = _evidence(ctx, [key for key, _ in top])
    if cypher:
        cyphers.append(cypher)

    items: list[Item] = []
    for key, entry in top:
        row = entry["row"]
        label = row["label"]
        node = _node_for(label, key, row)
        item = to_item(label, node, round(entry["score"], 4))
        if entry["paths"]:
            item.props["paths"] = [
                {"from": p["anchor"], "via": "→".join(p["path"])} for p in entry["paths"][:4]
            ]
            item.props["path_count"] = len(entry["paths"])
        else:
            item.props["anchor"] = True
        item.props["anchored_by"] = anchored_by
        # The two factors behind `score`, so a reader (and Plan 3) can see whether this item
        # is here because the graph agrees or because the question does.
        item.props["degree_score"] = round(entry["degree_score"], 4)
        item.props["semantic"] = round(entry["semantic"], 4)
        for ev in evidence.get(key, []):
            item.provenance.append(
                Provenance(
                    chunk_id=ev["chunk_id"],
                    quote=ev["quote"],
                    batch_id=ev.get("batch_id"),
                    model=ev.get("model"),
                    source=ev.get("parent_key"),
                )
            )
        items.append(item)

    # An `XT-` test node has no `MENTIONS` quote — nothing in the corpus writes prose about
    # a synthetic test — so the answer would carry no checkable chunk at all. Fall back to
    # the node's own text, which is evidence of a weaker kind but is still evidence.
    unsupported = [i.key for i in items if not any(p.chunk_id for p in i.provenance)]
    if unsupported:
        fallback, fallback_cypher = own_chunks(ctx, unsupported)
        cyphers.append(fallback_cypher)
        for item in items:
            if not any(p.chunk_id for p in item.provenance):
                item.provenance.extend(as_provenance(fallback.get(item.key, []), item.key))

    return finish(
        "s3",
        items,
        question=query,
        timer=timer,
        cypher_used=cyphers,
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def _node_for(label: str, key: str, row: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {
        "kind": row.get("kind"),
        "status": row.get("status"),
        "type": row.get("type"),
        "weak": row.get("weak"),
        "synthetic": row.get("synthetic"),
        "evidence_chunk_ids": row.get("evidence_chunk_ids") or [],
        "batch_id": row.get("batch_id"),
        "model": row.get("model"),
    }
    if label == "Entity":
        node.update({"id": key, "name": row.get("title"), "description": row.get("description")})
    elif label == "Person":
        node.update({"id": key, "display": row.get("title")})
    elif label in ("Component", "Version", "Sprint", "Space", "Area"):
        node.update({"name": key})
    else:
        node.update({"key": key, "title": row.get("title")})
    return {k: v for k, v in node.items() if v is not None}
