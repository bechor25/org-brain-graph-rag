"""The canonical files in memory, plus the lookups that decide what exists.

`brain load` never creates the far end of an edge (see `cypher.py`), so every edge has to
be resolved *before* it is written: a ref to `KAFKA-9999` is a dangling ref, not a new
node. Resolving in Python rather than letting a Cypher `MATCH` quietly drop rows is what
turns "some links did not land" into an exact number in the report.

The whole corpus is ~12k changes + 1.4k issues + 1.4k pages; holding it costs a few
hundred MB and buys exact counts. A corpus an order of magnitude larger would need the
key sets loaded first and the records streamed — the same limit `brain canon` recorded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from brain.canon.io import read_jsonl
from brain.canon.models import Change, Container, Document, Person, WorkItem
from brain.graph.mapping import container_label, pr_number

CANONICAL_FILES: dict[str, type] = {
    "workitems": WorkItem,
    "documents": Document,
    "persons": Person,
    "changes": Change,
    "containers": Container,
}


@dataclass
class Corpus:
    workitems: list[WorkItem] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    persons: list[Person] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)

    # --- derived lookups ---------------------------------------------------
    workitem_keys: set[str] = field(default_factory=set)
    document_keys: set[str] = field(default_factory=set)
    document_ids: dict[str, str] = field(default_factory=dict)  # canonical id -> key
    commit_shas: set[str] = field(default_factory=set)
    pr_numbers: set[int] = field(default_factory=set)
    person_ids: set[str] = field(default_factory=set)
    #: (source, identity key) -> Person.id. `brain canon` mints one Person per identity,
    #: but the mini fixture (and `brain resolve`, later) puts several identities on one
    #: Person, so a git email must be looked up here and not assembled as `git:<email>`.
    person_by_identity: dict[tuple[str, str], str] = field(default_factory=dict)
    #: identity key alone -> Person.id, only where exactly one person claims that key.
    #: A `@mjsax` mention on a Confluence page is the Jira person; nothing else it can be.
    person_by_bare_key: dict[str, str] = field(default_factory=dict)
    container_names: dict[str, set[str]] = field(default_factory=dict)
    unknown_container_kinds: Counter = field(default_factory=Counter)

    @property
    def commits(self) -> list[Change]:
        return [c for c in self.changes if c.kind == "commit"]

    @property
    def pull_requests(self) -> list[Change]:
        return [c for c in self.changes if c.kind == "pr"]

    def person(self, source: str, key: str | None) -> str | None:
        """Person id for an identity of a known source, e.g. (`jira`, `jrao`)."""
        if not key:
            return None
        return self.person_by_identity.get((source, key))

    def person_mentioned(self, source: str, key: str | None) -> str | None:
        """Person id for a `@name` / `[~name]` mention: same source first, then unique."""
        if not key:
            return None
        return self.person_by_identity.get((source, key)) or self.person_by_bare_key.get(key)

    def has_document(self, key: str | None) -> bool:
        return bool(key) and key in self.document_keys

    def document_key(self, ancestor: str) -> str | None:
        """Confluence ancestors are page *ids*; documents are keyed by `KIP-N`."""
        if ancestor in self.document_keys:
            return ancestor
        return self.document_ids.get(ancestor)


def _index(corpus: Corpus) -> Corpus:
    corpus.workitem_keys = {w.key for w in corpus.workitems}
    corpus.document_keys = {d.key for d in corpus.documents}
    corpus.document_ids = {d.id: d.key for d in corpus.documents}
    corpus.commit_shas = {c.id for c in corpus.changes if c.kind == "commit"}
    corpus.pr_numbers = {
        n for c in corpus.changes if c.kind == "pr" and (n := pr_number(c.id)) is not None
    }
    corpus.person_ids = {p.id for p in corpus.persons}

    claims: dict[str, set[str]] = {}
    for p in corpus.persons:
        for ident in p.identities:
            corpus.person_by_identity.setdefault((ident.source, ident.key), p.id)
            claims.setdefault(ident.key, set()).add(p.id)
    corpus.person_by_bare_key = {k: next(iter(v)) for k, v in claims.items() if len(v) == 1}

    for c in corpus.containers:
        label = container_label(c.kind)
        if label is None:
            corpus.unknown_container_kinds[c.kind] += 1
            continue
        corpus.container_names.setdefault(label, set()).add(c.name)
    return corpus


def load_corpus(canonical_dir: Path) -> Corpus:
    """Read every canonical file that exists. A missing file is an empty list, not a crash:
    `brain canon --source git` alone is a legal state of `data/canonical/`."""
    corpus = Corpus()
    for name, model in CANONICAL_FILES.items():
        path = canonical_dir / f"{name}.jsonl"
        if not path.exists():
            continue
        setattr(corpus, name, list(read_jsonl(path, model)))
    return _index(corpus)
