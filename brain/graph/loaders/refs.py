"""`REFERENCES{via, kinds}` and `MENTIONS_PERSON` — the traceability that is only prose.

`via` is the number this POC is built to expose: 865 of the 22,595 refs in this corpus
were stated formally by Jira, and 21,730 exist only because somebody typed a key into a
sentence. An organisation's graph is mostly the second kind, and a pipeline that only
reads formal links sees about 4% of it.

Nothing here creates a node. A ref to `KAFKA-9999` — a real issue, just outside the
harvested 2023–2025 slice — is counted under `dangling_refs` by kind and dropped, because
a `WorkItem` with a key and no title would be indistinguishable from a real one later.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from brain.canon.models import Ref
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.loaders import emit
from brain.graph.mapping import REFERENCE_KINDS, merge_via, pr_number

PERSON: tuple[str, str] = ("Person", "id")
TARGETS: dict[str, tuple[str, str]] = {
    "issue": ("WorkItem", "key"),
    "kip": ("Document", "key"),
    "pr": ("PullRequest", "number"),
}
SOURCES: dict[str, tuple[str, str]] = {
    "WorkItem": ("WorkItem", "key"),
    "Document": ("Document", "key"),
    "Commit": ("Commit", "sha"),
    "PullRequest": ("PullRequest", "number"),
}


def resolve_target(corpus: Corpus, ref: Ref) -> tuple[str, str | int] | None:
    """The (label, key) the ref points at, or None when nothing in the graph matches."""
    if ref.kind == "issue":
        return ("issue", ref.key) if ref.key in corpus.workitem_keys else None
    if ref.kind == "kip":
        return ("kip", ref.key) if corpus.has_document(ref.key) else None
    if ref.kind == "pr":
        number = pr_number(ref.key)
        return ("pr", number) if number is not None and number in corpus.pr_numbers else None
    return None


def iter_ref_records(corpus: Corpus):
    """(source system, node label, node key, refs) for every record that carries refs."""
    for w in corpus.workitems:
        yield w.source, "WorkItem", w.key, w.refs
    for d in corpus.documents:
        yield d.source, "Document", d.key, d.refs
    for c in corpus.changes:
        if c.kind == "commit":
            yield "git", "Commit", c.id, c.refs
        else:
            number = pr_number(c.id)
            if number is not None:
                yield "git", "PullRequest", number, c.refs


def build_rows(corpus: Corpus) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict]:
    """Reference and mention rows grouped by (source label, target kind)."""
    grouped: dict[tuple[str, str], dict[tuple[Any, Any], dict[str, Any]]] = {}
    mentions: dict[str, dict[tuple[Any, str], dict[str, Any]]] = {}
    total: Counter = Counter()
    dangling: Counter = Counter()
    by_via: Counter = Counter()

    for source, src_label, src_key, refs in iter_ref_records(corpus):
        for ref in refs:
            total[ref.kind] += 1
            if ref.kind == "url":
                continue
            if ref.kind == "user":
                person = corpus.person_mentioned(source, ref.key)
                if person is None:
                    dangling["user"] += 1
                    continue
                mentions.setdefault(src_label, {})[(src_key, person)] = {
                    "src": src_key,
                    "dst": person,
                }
                continue
            if ref.kind not in REFERENCE_KINDS:
                dangling[ref.kind] += 1
                continue
            target = resolve_target(corpus, ref)
            if target is None:
                dangling[ref.kind] += 1
                continue
            kind, dst = target
            bucket = grouped.setdefault((src_label, kind), {})
            row = bucket.get((src_key, dst))
            if row is None:
                bucket[(src_key, dst)] = {
                    "src": src_key,
                    "dst": dst,
                    "props": {"via": ref.via, "kinds": [ref.kind]},
                }
            else:
                props = row["props"]
                props["via"] = merge_via(props["via"], ref.via)
                if ref.kind not in props["kinds"]:
                    props["kinds"] = sorted({*props["kinds"], ref.kind})
            by_via[ref.via] += 1

    rows = {k: list(v.values()) for k, v in grouped.items()}
    mention_rows = {k: list(v.values()) for k, v in mentions.items()}
    stats = {
        "refs_by_kind": dict(total.most_common()),
        "dangling_refs": dict(dangling.most_common()),
        "references_by_via": dict(by_via.most_common()),
    }
    return {**rows, **{("__mentions__", k): v for k, v in mention_rows.items()}}, stats


def load_edges(ctx: GraphContext, corpus: Corpus) -> dict[str, Any]:
    grouped, stats = build_rows(corpus)
    references = 0
    mentions = 0
    via_counts: Counter = Counter()
    for (src_label, kind), rows in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        if src_label == "__mentions__":
            mentions += emit(ctx, SOURCES[kind], "MENTIONS_PERSON", PERSON, rows)
            continue
        references += emit(
            ctx, SOURCES[src_label], "REFERENCES", TARGETS[kind], rows, set_props=True
        )
        for row in rows:
            via_counts[row["props"]["via"]] += 1
    return {
        **stats,
        "edges": {"REFERENCES": references, "MENTIONS_PERSON": mentions},
        "references_edges_by_via": dict(via_counts.most_common()),
    }
