"""What gets chunked in Phase A, and why the rest does not (brief §06 decision 1).

The corpus holds 1,391 Confluence pages; only 268 of them are named by an issue or a
commit in the harvested slice. Embedding the other 1,123 would triple the cost of this
step to index design documents nothing in the slice has ever pointed at — so the scope is
*reachability*, not availability. `--all-docs` exists for the day that changes; it is off.

The `ambiguous-kip` variants ride along with the KIP they lost a collision to. They are
real KIPs that share a number with another page, and the reason they are here is exactly
the reason they are ambiguous: a question about `KIP-568` should be able to find the
losing page's text too, or the collision silently deletes a design document.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from brain.canon.models import Change, Document, WorkItem
from brain.graph.corpus import Corpus

#: A commit earns a chunk by naming something: an issue key or a KIP. 1,957 of the 6,107
#: commits in this corpus name neither ("MINOR: fix typo"), and a chunk of those is a
#: chunk no traversal can reach from a work item.
KEYED_REF_KINDS = ("issue", "kip")
AMBIGUOUS_LABEL = "ambiguous-kip"


@dataclass
class Scope:
    documents: list[Document] = field(default_factory=list)
    workitems: list[WorkItem] = field(default_factory=list)
    commits: list[Change] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def parents(self) -> int:
        return len(self.documents) + len(self.workitems) + len(self.commits)


def referenced_kip_keys(corpus: Corpus) -> set[str]:
    """KIP keys named by a WorkItem or a Commit — the slice's own reading list."""
    keys: set[str] = set()
    for w in corpus.workitems:
        keys.update(r.key for r in w.refs if r.kind == "kip")
    for c in corpus.changes:
        if c.kind != "commit":
            continue
        keys.update(r.key for r in c.refs if r.kind == "kip")
    return keys


def has_keyed_ref(change: Change) -> bool:
    return any(r.kind in KEYED_REF_KINDS for r in change.refs)


def select(corpus: Corpus, *, all_docs: bool = False, limit: int | None = None) -> Scope:
    """The Phase A slice, with the number the scope rejected next to the number it kept.

    `limit` caps the *parent records*, not the chunks, and takes them proportionally from
    each kind — a `--limit 500` run that only sampled documents would measure the
    throughput of the longest texts in the corpus and mislead the full run it precedes.
    """
    wanted = referenced_kip_keys(corpus)
    by_key = {d.key: d for d in corpus.documents}
    reachable = [by_key[k] for k in sorted(wanted) if k in by_key]
    reachable_keys = {d.key for d in reachable}
    variants = [
        d
        for d in corpus.documents
        if d.kip_of in reachable_keys
        and AMBIGUOUS_LABEL in d.labels
        and d.key not in reachable_keys
    ]
    documents = corpus.documents if all_docs else [*reachable, *variants]

    commits = [c for c in corpus.changes if c.kind == "commit"]
    keyed = [c for c in commits if has_keyed_ref(c)]

    scope = Scope(
        documents=sorted(documents, key=lambda d: d.key),
        workitems=sorted(corpus.workitems, key=lambda w: w.key),
        commits=sorted(keyed, key=lambda c: c.id),
        stats={
            "all_docs": all_docs,
            "kip_keys_referenced": len(wanted),
            "kip_keys_outside_corpus": len(wanted - set(by_key)),
            "documents_reachable": len(reachable),
            "documents_ambiguous_variants": len(variants),
            "documents_total_in_corpus": len(corpus.documents),
            "documents_skipped_unreferenced": len(corpus.documents) - len(documents),
            "workitems": len(corpus.workitems),
            "comments": sum(len(w.comments) for w in corpus.workitems),
            "commits_total": len(commits),
            "commits_keyed": len(keyed),
            "commits_skipped_unkeyed": len(commits) - len(keyed),
        },
    )
    if limit is not None:
        _apply_limit(scope, limit)
    return scope


def _apply_limit(scope: Scope, limit: int) -> None:
    """Keep `limit` parent records, split across the three kinds by their real shares."""
    total = scope.parents()
    if total <= limit:
        scope.stats["limit"] = {"requested": limit, "applied": False, "parents": total}
        return
    share = limit / total
    docs = max(1, round(len(scope.documents) * share))
    items = max(1, round(len(scope.workitems) * share))
    commits = max(1, limit - docs - items)
    scope.documents = scope.documents[:docs]
    scope.workitems = scope.workitems[:items]
    scope.commits = scope.commits[:commits]
    scope.stats["limit"] = {
        "requested": limit,
        "applied": True,
        "documents": len(scope.documents),
        "workitems": len(scope.workitems),
        "commits": len(scope.commits),
    }


def iter_chunks(scope: Scope, kinds: set[str], stats) -> Iterator:
    """Every chunk the scope asks for, in a stable order (documents, items, commits)."""
    from brain.chunk import chunker

    if "doc" in kinds:
        for d in scope.documents:
            yield from chunker.chunk_document(d, stats)
    for w in scope.workitems:
        if "issue" in kinds and (w.description or "").strip():
            yield from chunker.chunk_workitem_description(w, stats)
        if "comment" in kinds:
            yield from chunker.chunk_comments(w, stats)
    if "commit" in kinds:
        for c in scope.commits:
            yield from chunker.chunk_commit(c, stats)
