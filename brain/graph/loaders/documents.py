"""`Document` (KIP / Page) plus its people and its page tree.

`Document.key` is the merge key, so the 24 pages that carry a `KIP-N` another page owns
keep their own identity (their key is the page id) and point at the owner with
`VARIANT_OF` — the alternative, merging them onto the KIP key, would silently overwrite
one design document with another. The 9 pages `brain canon` marked `ambiguous-kip` are
real KIPs that lost a collision; they load like any other page.

`CHILD_OF` is thin on purpose: Confluence gives ancestors as page ids, and only 9 of the
2,795 ancestor entries in this corpus name a page inside the harvested slice. The other
2,786 are the space's root pages, which were never harvested and are not invented here.
"""

from __future__ import annotations

from typing import Any

from brain.canon.models import Document
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.loaders import emit
from brain.graph.provenance import SyntheticProvenance

LABEL = "Document"
KEY = "key"
NODE: tuple[str, str] = (LABEL, KEY)


def node_rows(documents: list[Document], prov: SyntheticProvenance) -> list[dict[str, Any]]:
    rows = []
    for d in documents:
        props: dict[str, Any] = {
            "id": d.id,
            "key": d.key,
            "source": d.source,
            "kind": d.kind,
            "space": d.space,
            "title": d.title,
            "body_md": d.body_md,
            "version": d.version,
            "created": d.created,
            "updated": d.updated,
            "labels": d.labels,
            "kip_of": d.kip_of,
            "synthetic": d.synthetic,
            "raw_url": d.raw_url,
        }
        props.update(prov.props(d.key))
        rows.append({"key": d.key, "props": props})
    return rows


def load_nodes(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    rows = node_rows(corpus.documents, prov)
    ctx.write_rows(node_merge(ctx, LABEL, KEY), rows)
    return {
        "documents": len(rows),
        "stamped": sum(1 for r in rows if r["props"].get("batch_id")),
    }


def load_edges(ctx: GraphContext, corpus: Corpus) -> dict[str, Any]:
    authored: list[dict[str, Any]] = []
    child_of: list[dict[str, Any]] = []
    variant_of: list[dict[str, Any]] = []
    stats = {
        "authors_unknown": 0,
        "ancestors_total": 0,
        "ancestors_outside_corpus": 0,
        "kip_of_dangling": 0,
    }

    for d in corpus.documents:
        person = corpus.person(d.source, d.author)
        if d.author and not person:
            stats["authors_unknown"] += 1
        elif person:
            authored.append({"src": person, "dst": d.key})

        for ancestor in d.ancestors:
            stats["ancestors_total"] += 1
            parent = corpus.document_key(ancestor)
            if parent is None or parent == d.key:
                stats["ancestors_outside_corpus"] += 1
                continue
            child_of.append({"src": d.key, "dst": parent})

        if d.kip_of:
            if corpus.has_document(d.kip_of) and d.kip_of != d.key:
                variant_of.append({"src": d.key, "dst": d.kip_of})
            else:
                stats["kip_of_dangling"] += 1

    person_node = ("Person", "id")
    return {
        **stats,
        "AUTHORED": emit(ctx, person_node, "AUTHORED", NODE, authored),
        "CHILD_OF": emit(ctx, NODE, "CHILD_OF", NODE, child_of),
        "VARIANT_OF": emit(ctx, NODE, "VARIANT_OF", NODE, variant_of),
    }
