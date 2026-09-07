"""Graph row → `Item`, in one place, because `read()` throws the labels away.

`GraphClient.read()` returns `record.data()`: a node comes back as a bare property dict
with no labels and no element id. Every query in this library therefore projects
explicitly — and rather than writing that projection fifteen times (and getting the
`toString()` on a temporal property wrong in one of them), each label declares its fields
once here and `projection()` builds the map expression.

Two things the projections deliberately never return:

* `embedding` — 1024 floats per node, on every hit, for a value the answer never shows.
* the whole `Document.body_md` — a KIP body is tens of KB and the snippet is capped at
  600 characters anyway; the text an answer quotes comes from a `Chunk`, which is the
  node that carries the provenance.
"""

from __future__ import annotations

from typing import Any

from brain.retrieve.pack import clip
from brain.retrieve.types import Item, Provenance

#: label -> the properties a retrieval result may carry. Order is the map's order.
FIELDS: dict[str, tuple[str, ...]] = {
    "Chunk": (
        "id",
        "parent_key",
        "parent_kind",
        "kind",
        "heading",
        "text",
        "lang",
        "author",
        "at",
        "position",
        "synthetic",
        "orphaned",
    ),
    "WorkItem": (
        "key",
        "title",
        "type",
        "status",
        "resolution",
        "priority",
        "source",
        "labels",
        "created",
        "updated",
        "synthetic",
    ),
    "Document": ("key", "title", "kind", "space", "source", "version", "updated", "synthetic"),
    "Person": ("id", "display", "identity_keys", "resolved", "synthetic"),
    "Commit": ("sha", "message", "author_name", "at", "file_count", "pr", "synthetic"),
    "PullRequest": ("number", "title", "at", "synthetic"),
    "File": ("path",),
    "Component": ("name",),
    "Version": ("name",),
    "Sprint": ("name",),
    "Space": ("name",),
    "Area": ("name",),
    "Entity": (
        "id",
        "kind",
        "name",
        "description",
        "weak",
        "aliases",
        "synthetic",
        "evidence_chunk_ids",
        "batch_id",
        "model",
        "extracted_at",
    ),
    "Community": ("id", "level", "title", "summary", "rank"),
    "StatusChange": ("id", "field", "from", "to", "at", "by", "item_key", "synthetic"),
}

#: Properties Neo4j returns as a temporal type; `record.data()` would hand back a
#: `neo4j.time.DateTime` that `json.dumps` cannot serialise.
TEMPORAL_FIELDS: frozenset[str] = frozenset({"at", "created", "updated", "extracted_at"})

#: label -> the `Item.kind` it is reported as (spec §4.4's closed set).
ITEM_KIND: dict[str, str] = {
    "Chunk": "Chunk",
    "WorkItem": "WorkItem",
    "Document": "Document",
    "Person": "Person",
    "Commit": "Change",
    "PullRequest": "Change",
    "File": "Container",
    "Component": "Container",
    "Version": "Container",
    "Sprint": "Container",
    "Space": "Container",
    "Area": "Container",
    "Entity": "Entity",
    "Community": "Community",
    "StatusChange": "Row",
}

#: label -> the property that is the citable key.
KEY_FIELD: dict[str, str] = {
    "Chunk": "id",
    "WorkItem": "key",
    "Document": "key",
    "Person": "id",
    "Commit": "sha",
    "PullRequest": "number",
    "File": "path",
    "Component": "name",
    "Version": "name",
    "Sprint": "name",
    "Space": "name",
    "Area": "name",
    "Entity": "id",
    "Community": "id",
    "StatusChange": "id",
}


def projection(alias: str, label: str) -> str:
    """A Cypher map expression for one node: `{key: n.key, title: n.title, …}`."""
    parts = []
    for field in FIELDS[label]:
        expr = f"toString({alias}.`{field}`)" if field in TEMPORAL_FIELDS else f"{alias}.`{field}`"
        parts.append(f"`{field}`: {expr}")
    return "{" + ", ".join(parts) + "}"


def _title(label: str, props: dict[str, Any]) -> str:
    if label == "Chunk":
        return props.get("heading") or f"{props.get('parent_key', '')} · {props.get('kind', '')}"
    if label == "Commit":
        return clip(
            (props.get("message") or "").splitlines()[0] if props.get("message") else "", 120
        )
    if label in ("Person",):
        return props.get("display") or props.get("id") or ""
    if label == "Entity":
        return props.get("name") or ""
    if label == "StatusChange":
        return f"{props.get('field')}: {props.get('from')} → {props.get('to')}"
    return props.get("title") or props.get("name") or ""


def _snippet(label: str, props: dict[str, Any]) -> str:
    if label == "Chunk":
        return clip(props.get("text"))
    if label == "Commit":
        return clip(props.get("message"))
    if label == "Entity":
        return clip(props.get("description"))
    if label == "Community":
        return clip(props.get("summary"))
    if label == "WorkItem":
        return clip(props.get("title"))
    return clip(props.get("title") or props.get("name") or "")


#: Never echoed back into `props`: it is already the title, the snippet or the key.
_ABSORBED: dict[str, tuple[str, ...]] = {
    "Chunk": ("id", "text", "heading"),
    "Commit": ("sha", "message"),
    "Entity": ("id", "name", "description"),
    "Community": ("id", "summary", "title"),
    "WorkItem": ("key", "title"),
    "Document": ("key", "title"),
    "Person": ("id", "display"),
    "StatusChange": ("id",),
}


def to_item(label: str, props: dict[str, Any] | None, score: float = 0.0, **extra: Any) -> Item:
    """One node, projected by `projection()`, as the envelope every tool returns."""
    props = dict(props or {})
    key_field = KEY_FIELD.get(label, "id")
    key = props.get(key_field)
    absorbed = set(_ABSORBED.get(label, ()))
    rest = {k: v for k, v in props.items() if k not in absorbed and v not in (None, [], "")}
    rest.setdefault("label", label)
    rest.update(extra)
    item = Item(
        kind=ITEM_KIND.get(label, "Row"),
        key=str(key) if key is not None else "",
        title=_title(label, props),
        snippet=_snippet(label, props),
        score=float(score),
        props=rest,
    )
    if label == "Chunk":
        item.provenance = [
            Provenance(
                chunk_id=item.key, source=props.get("parent_key"), quote=item.snippet or None
            )
        ]
    elif label == "Entity":
        for chunk_id in (props.get("evidence_chunk_ids") or [])[:3]:
            item.provenance.append(
                Provenance(
                    chunk_id=chunk_id, batch_id=props.get("batch_id"), model=props.get("model")
                )
            )
    return item


#: The labels a neighbour can come back as, most specific first. `head(labels(n))` is not
#: usable: a work item carries both `WorkItem` and `Bug` and Neo4j promises no order.
_LABEL_ORDER: tuple[str, ...] = (
    "Chunk",
    "Entity",
    "Community",
    "StatusChange",
    "Person",
    "Commit",
    "PullRequest",
    "Document",
    "WorkItem",
    "Component",
    "Version",
    "Sprint",
    "Space",
    "Area",
    "File",
)


def label_case(alias: str, prefix: str = "") -> str:
    """A Cypher `CASE` that names the one label a retrieval result should be reported as."""
    arms = " ".join(f"WHEN {alias}:`{prefix}{label}` THEN '{label}'" for label in _LABEL_ORDER)
    return f"CASE {arms} ELSE head(labels({alias})) END"


def key_case(alias: str, prefix: str = "") -> str:
    """A Cypher `CASE` for the citable key of a node whose label is not known statically.

    `coalesce(n.key, n.id, n.name)` looks equivalent and is not: an `Entity` has both a
    `name` and an `id`, a `WorkItem` has both a `key` and an `id`, and coalesce would
    return `jira:KAFKA-15282` where the citation has to say `KAFKA-15282`.
    """
    arms = " ".join(
        f"WHEN {alias}:`{prefix}{label}` THEN toString({alias}.`{KEY_FIELD[label]}`)"
        for label in _LABEL_ORDER
    )
    return f"CASE {arms} ELSE toString({alias}.`name`) END"
