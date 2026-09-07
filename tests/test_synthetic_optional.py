"""The synthetic layer is a plugin (ADR-0005 §5): every later step runs without it.

"Opt-in" is easy to believe and easy to lose — one `import brain.synth` in a loader, one
`synthetic_merged.json` a step needs to exist, and `brain load` stops working on a corpus
that was only ever harvested. These tests are the guard, run on the mini fixture with the
synthetic records stripped out — which is exactly what `brain reset --synthetic` leaves.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from brain.chunk.chunker import ChunkStats
from brain.chunk.scope import iter_chunks, select
from brain.graph.corpus import load_corpus
from brain.graph.loaders import containers, persons, workitems
from brain.graph.provenance import NOT_AVAILABLE, SyntheticProvenance
from brain.reset import strip_synthetic_records

MINI = Path("data/fixtures/mini")

#: Packages that run *after* canon. None of them may import the synthetic layer.
DOWNSTREAM = ("brain/graph", "brain/chunk", "brain/extract", "brain/resolve")


@pytest.fixture
def real_only(tmp_path) -> Path:
    """The mini corpus with every `synthetic: true` record removed, and no ledger."""
    canonical = tmp_path / "canonical"
    shutil.copytree(MINI, canonical)
    removed = strip_synthetic_records(canonical, apply=True)
    assert removed["workitems"] == 4, "the fixture must have had a synthetic layer to strip"
    return canonical


def test_no_downstream_module_imports_the_synthetic_layer():
    """A structural guarantee, not a runtime one: the coupling cannot come back unnoticed."""
    offenders: list[str] = []
    for package in DOWNSTREAM:
        for path in Path(package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "brain.synth"
                ):
                    offenders.append(f"{path}: from {node.module}")
                if isinstance(node, ast.Import):
                    offenders += [
                        f"{path}: import {a.name}"
                        for a in node.names
                        if a.name.startswith("brain.synth")
                    ]
    assert offenders == []


def test_load_reads_a_corpus_that_never_had_a_synthetic_layer(real_only):
    corpus = load_corpus(real_only)
    assert corpus.workitems and all(not w.synthetic for w in corpus.workitems)
    prov = SyntheticProvenance.load(real_only)
    assert prov.available is False
    assert prov.status() == NOT_AVAILABLE
    assert prov.props("jira:KAFKA-1") == {}


def test_every_node_loader_builds_its_rows_without_a_ledger(real_only):
    corpus = load_corpus(real_only)
    prov = SyntheticProvenance.load(real_only)
    items, _types, _unknown = workitems.node_rows(corpus.workitems, prov)
    people = persons.node_rows(corpus.persons, prov)
    boxes, _skipped, _unknown_kinds = containers.node_rows(corpus.containers, prov)
    assert items and people and boxes
    # Nothing carries provenance, because nothing here was written by an LLM.
    flat = [r for rows in (*items.values(), people, *boxes.values()) for r in rows]
    assert flat and all("batch_id" not in r["props"] for r in flat)


def test_chunk_scopes_and_chunks_a_corpus_with_no_synthetic_layer(real_only):
    corpus = load_corpus(real_only)
    scope = select(corpus, all_docs=True)
    chunks = list(iter_chunks(scope, {"doc", "issue", "comment", "commit"}, ChunkStats()))
    assert chunks, "the real half of the fixture must still produce chunks"
    assert all(chunk.id for chunk in chunks)


def test_stripping_the_layer_twice_changes_nothing(real_only):
    """`brain reset --synthetic` is idempotent — the second run has nothing left to find."""
    before = {p.name: p.read_bytes() for p in sorted(real_only.glob("*.jsonl"))}
    assert set(strip_synthetic_records(real_only, apply=True).values()) == {0}
    assert {p.name: p.read_bytes() for p in sorted(real_only.glob("*.jsonl"))} == before
