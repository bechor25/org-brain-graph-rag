"""The graph, described in the amount of detail a query author can actually use.

Text2Cypher fails in two ways, and only one of them is the model's fault. The model's
fault is inventing a label; *our* fault is not telling it what the labels are. `get_schema`
is the fix for the second, and its whole design problem is editing: `apoc.meta.schema` on
this corpus is 64 entries deep with every sampled property of every relationship, which is
both too big for a prompt and — for relationship *counts* — wrong, because it is sampled.

So this module takes three reads and merges them:

* `apoc.meta.schema` for structure (which properties a label has, which relationship types
  leave it and where they land),
* `apoc.meta.stats` for the counts that must be exact (a label's node count, a type's
  relationship count — the numbers an aggregation question is *about*),
* a bounded sample of the values a few closed-vocabulary properties actually take
  (`WorkItem.status`, `Document.kind`, `HAS_RUN.status` …), because "STRING" does not tell
  an author that `open` is spelled nine different ways in this corpus,
* `SHOW INDEXES` plus the `IndexMeta` nodes, because a query author who does not know that
  `chunk_embedding` is a `bge-m3` vector index of 1024 dimensions cannot write a vector
  query, and one who does not know `workitem_text` exists will write a `CONTAINS` scan.

Three things are deliberately removed: `embedding` properties (1024 floats that no answer
returns), internal namespaces (`_Retr…`, `_Res…` — test and pipeline scratch, not the
organization's schema), and `LOOKUP` indexes (an implementation detail of the store).

Cached for ten minutes: the schema does not change under a running agent, and three round
trips per question is a latency budget spent on a constant.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from brain.retrieve.cypher_guard import ALLOWED_CALL_PREFIXES

#: Plan/brief: ten minutes.
SCHEMA_TTL_S = 600.0
#: `apoc.meta.schema` samples relationships; a bigger sample costs little on this graph and
#: finds the rarer (from)-[type]->(to) patterns.
DEFAULT_SAMPLE = 100

#: Properties whose *values* a query author cannot guess and cannot do without. A type
#: says `STRING`; it does not say that `WorkItem.status` is one of nine words spread over
#: Jira and ADO, so a model writes `WHERE w.status = 'open'` and gets nothing. The list is
#: deliberately short — five closed-vocabulary properties the `cypher-author` batch named
#: as the gap — because sample values are the part of a schema that grows without bound.
#: (label-or-type, property, kind) with kind in {"node", "rel"}.
SAMPLE_PROPERTIES: tuple[tuple[str, str, str], ...] = (
    ("WorkItem", "source", "node"),
    ("WorkItem", "status", "node"),
    ("WorkItem", "type", "node"),
    ("Document", "kind", "node"),
    ("HAS_RUN", "status", "rel"),
)
#: Enough to show the vocabulary, few enough to stay a prompt rather than a dump.
MAX_SAMPLE_VALUES = 10

#: Properties no schema description should carry.
HIDDEN_PROPERTIES: frozenset[str] = frozenset({"embedding"})
#: Index types that describe the store rather than the data.
HIDDEN_INDEX_TYPES: frozenset[str] = frozenset({"LOOKUP"})

#: Pipeline bookkeeping. Every extracted node and edge carries it (conventions rule 3) and
#: no question is about it — `explain_edge` reads provenance, not Text2Cypher. Dropped from
#: the *compact* rendering only: `get_schema` still describes the graph as it is.
NOISE_PROPERTIES: frozenset[str] = frozenset(
    {
        "batch_id",
        "batch_ids",
        "shard",
        "model",
        "merged_at",
        "merged_from",
        "extracted_at",
        "embed_hash",
        "resolution_batch_id",
        "resolution_batch_ids",
        "resolution_model",
        "resolution_tier",
        "resolved_at",
        "descriptions",
    }
)

_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _visible(name: str, prefix: str) -> bool:
    """Inside a test namespace, only that namespace; outside one, nothing underscored."""
    return name.startswith(prefix) if prefix else not name.startswith("_")


def _properties(raw: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    props: dict[str, str] = {}
    indexed: list[str] = []
    for name, meta in (raw or {}).items():
        if name in HIDDEN_PROPERTIES:
            continue
        props[name] = str((meta or {}).get("type", "ANY"))
        if (meta or {}).get("indexed"):
            indexed.append(name)
    return props, indexed


def reduce_schema(
    meta: dict[str, Any],
    stats: dict[str, Any],
    indexes: list[dict[str, Any]],
    index_meta: list[dict[str, Any]],
    prefix: str = "",
    samples: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """`apoc.meta.schema` + `apoc.meta.stats` + `SHOW INDEXES` → the description we ship."""
    label_counts = dict(stats.get("labels") or {})
    rel_counts = dict(stats.get("relTypesCount") or {})

    labels: list[dict[str, Any]] = []
    relationships: dict[str, dict[str, Any]] = {}
    for name, entry in sorted((meta or {}).items()):
        if not isinstance(entry, dict) or entry.get("type") != "node":
            continue
        if not _visible(name, prefix):
            continue
        props, indexed = _properties(entry.get("properties") or {})
        labels.append(
            {
                "label": name,
                "count": int(label_counts.get(name, entry.get("count", 0)) or 0),
                "properties": props,
                "indexed": sorted(indexed),
            }
        )
        for rel_type, rel in (entry.get("relationships") or {}).items():
            # Every edge is described twice — once from each endpoint. Both sides are read
            # and the patterns deduped, rather than trusting the outgoing side alone: when
            # the other endpoint's label is filtered out (or missed by the sample) the
            # `in` entry is the only place the pattern appears at all.
            outgoing = (rel or {}).get("direction") == "out"
            others = [t for t in (rel.get("labels") or []) if _visible(t, prefix)]
            slot = relationships.setdefault(
                rel_type,
                {
                    "type": rel_type,
                    "count": int(rel_counts.get(rel_type, 0) or 0),
                    "patterns": [],
                    "properties": _properties(rel.get("properties") or {})[0],
                },
            )
            slot["properties"].update(_properties(rel.get("properties") or {})[0])
            for other in others:
                pattern = {"from": name, "to": other} if outgoing else {"from": other, "to": name}
                if pattern not in slot["patterns"]:
                    slot["patterns"].append(pattern)

    kept_indexes = [
        {
            "name": row["name"],
            "type": row.get("type"),
            "labels": row.get("labelsOrTypes") or [],
            "properties": row.get("properties") or [],
            "state": row.get("state"),
        }
        for row in indexes
        if row.get("type") not in HIDDEN_INDEX_TYPES and _visible(str(row.get("name", "")), prefix)
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "node_count": int(stats.get("nodeCount", 0) or 0),
        "relationship_count": int(stats.get("relCount", 0) or 0),
        "labels": labels,
        "relationships": sorted(relationships.values(), key=lambda r: -r["count"]),
        "indexes": kept_indexes,
        "index_meta": list(index_meta),
        "sample_values": dict(samples or {}),
        "allowed_procedures": list(ALLOWED_CALL_PREFIXES),
    }


def sample_values(
    ctx: Any, wanted: tuple[tuple[str, str, str], ...] = SAMPLE_PROPERTIES
) -> dict[str, list[str]]:
    """The actual vocabulary of a handful of closed-set properties, commonest first.

    One bounded aggregation per property — five round trips on a graph where the largest
    scanned label is the work items — and the answer is cached with the rest of the schema
    for ten minutes, so a question pays for it at most once per agent session.
    """
    out: dict[str, list[str]] = {}
    for name, prop, kind in wanted:
        if kind == "rel":
            cypher = (
                f"MATCH ()-[r:`{getattr(ctx, 'prefix', '') or ''}{name}`]->() "
                f"WITH r.`{prop}` AS value WHERE value IS NOT NULL "
                "RETURN value, count(*) AS n ORDER BY n DESC, value LIMIT $limit"
            )
        else:
            cypher = (
                f"MATCH (n:`{getattr(ctx, 'prefix', '') or ''}{name}`) "
                f"WITH n.`{prop}` AS value WHERE value IS NOT NULL "
                "RETURN value, count(*) AS n ORDER BY n DESC, value LIMIT $limit"
            )
        try:
            rows = ctx.read(cypher, limit=MAX_SAMPLE_VALUES)
        except Exception:  # noqa: BLE001 - a label this database does not have is not an error
            continue
        values = [str(r["value"]) for r in rows if r.get("value") is not None]
        if values:
            out[f"{name}.{prop}"] = values
    return out


def _read_schema(ctx: Any, sample: int) -> dict[str, Any]:
    meta = ctx.read(
        "CALL apoc.meta.schema({sample: $sample}) YIELD value RETURN value", sample=sample
    )
    stats = ctx.read(
        "CALL apoc.meta.stats() YIELD nodeCount, relCount, labels, relTypesCount "
        "RETURN nodeCount, relCount, labels, relTypesCount"
    )
    indexes = ctx.read(
        "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state RETURN *"
    )
    prefix = getattr(ctx, "prefix", "") or ""
    index_meta = ctx.read(
        f"MATCH (m:`{prefix}IndexMeta`) RETURN m.name AS name, m.model AS model, m.dim AS dim "
        "ORDER BY name"
    )
    return reduce_schema(
        (meta[0]["value"] if meta else {}),
        (stats[0] if stats else {}),
        list(indexes),
        list(index_meta),
        prefix=prefix,
        samples=sample_values(ctx),
    )


def get_schema(ctx: Any, *, refresh: bool = False, sample: int = DEFAULT_SAMPLE) -> dict[str, Any]:
    """The reduced schema for this database and namespace, cached for ten minutes."""
    key = (str(getattr(ctx.settings, "neo4j_database", "")), getattr(ctx, "prefix", "") or "")
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and not refresh and now - cached[0] < SCHEMA_TTL_S:
        return cached[1]
    schema = _read_schema(ctx, sample)
    _CACHE[key] = (now, schema)
    return schema


def _without_noise(properties: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in properties.items() if k not in NOISE_PROPERTIES}


def _grouped_patterns(rel: dict[str, Any]) -> list[str]:
    """`(:Bug|WorkItem|Document)-[:HAS_CHUNK]->(:Chunk)` — one line per target label.

    Eleven parent labels own chunks, and eleven separate pattern lines say nothing the one
    alternation does not. On this graph the grouping is two thirds of the relationship
    section.
    """
    by_target: dict[str, list[str]] = {}
    for pattern in rel["patterns"]:
        by_target.setdefault(pattern["to"], []).append(pattern["from"])
    return [
        f"(:{'|'.join(sources)})-[:{rel['type']}]->(:{target})"
        for target, sources in by_target.items()
    ]


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The same facts, spelled for a prompt rather than for a parser.

    Pretty-printed, the structured form is 58 KB on this graph — most of it the braces
    around `{"from": …, "to": …}` — and the batch it has to fit in is capped at 40 KB. The
    patterns become Cypher pattern strings (which is what the author will type anyway) and
    only the vector/fulltext indexes are listed, because the per-label `indexed` list
    already says which properties carry a `RANGE` index.
    """
    return {
        "node_count": schema["node_count"],
        "relationship_count": schema["relationship_count"],
        "labels": [
            {
                "label": label["label"],
                "count": label["count"],
                "properties": _without_noise(label["properties"]),
                "indexed": label["indexed"],
            }
            for label in schema["labels"]
        ],
        "relationships": [
            {
                "type": rel["type"],
                "count": rel["count"],
                "patterns": _grouped_patterns(rel),
                "properties": _without_noise(rel["properties"]),
            }
            for rel in schema["relationships"]
        ],
        "search_indexes": [
            f"{i['name']} {i['type']} :{'|'.join(i['labels'])}({', '.join(i['properties'])})"
            for i in schema["indexes"]
            if i["type"] in ("VECTOR", "FULLTEXT")
        ],
        "index_meta": schema["index_meta"],
        "sample_values": schema.get("sample_values", {}),
        "allowed_procedures": schema["allowed_procedures"],
    }


def schema_lines(schema: dict[str, Any]) -> list[str]:
    """A compact human/prompt rendering: one line per label, one per relationship pattern."""
    lines = [f"# {schema['node_count']} nodes, {schema['relationship_count']} relationships"]
    for label in schema["labels"]:
        props = ", ".join(f"{k}: {v}" for k, v in label["properties"].items())
        lines.append(f"({label['label']} · {label['count']}) {{{props}}}")
    for rel in schema["relationships"]:
        for pattern in rel["patterns"]:
            lines.append(f"(:{pattern['from']})-[:{rel['type']}]->(:{pattern['to']})")
    for index in schema["indexes"]:
        lines.append(
            f"INDEX {index['name']} {index['type']} "
            f"{index['labels']}({', '.join(index['properties'])})"
        )
    for name, values in (schema.get("sample_values") or {}).items():
        lines.append(f"{name} IN [{', '.join(repr(v) for v in values)}]")
    return lines
