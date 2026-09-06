"""The Phase A rules, with the graph replaced by canned rows.

The rules themselves are two Cypher queries, so what is testable without a database is:
the KIP set comes from `brain.chunk.scope` (not from `Document.kind = 'KIP'`), the context
each chunk carries, and the stats the manifest reports. The queries are exercised for real
in `tests/live/test_extract_live.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.extract import select as select_mod
from brain.extract.select import ISSUE_TYPES, MAX_KIP_KEYS, kip_document_keys, select

MINI = Path("data/fixtures/mini")


class FakeContext:
    """Answers the two selection queries and records the parameters they were given."""

    prefix = ""

    def __init__(self, doc_rows=(), issue_rows=()):
        self.doc_rows = list(doc_rows)
        self.issue_rows = list(issue_rows)
        self.calls: list[dict] = []

    def label(self, name: str) -> str:
        return f"`{name}`"

    def read(self, cypher: str, **params):
        self.calls.append(params)
        return self.doc_rows if "$doc_keys" in cypher else self.issue_rows


def doc_row(**kw):
    base = {
        "chunk_id": "a" * 40,
        "parent_key": "KIP-5",
        "parent_kind": "Document",
        "parent_title": "KIP-5: Next generation consumer group protocol",
        "position": 0,
        "kip_keys": [],
        "text": "# Motivation\nlong rebalances",
        "char_len": 29,
        "token_est": 7,
    }
    return {**base, **kw}


def issue_row(**kw):
    base = {
        "chunk_id": "b" * 40,
        "parent_key": "KAFKA-101",
        "parent_kind": "WorkItem",
        "parent_title": "Rebalance storm when group coordinator restarts",
        "position": 0,
        "kip_keys": ["KIP-5"],
        "text": "After KAFKA-100 landed, restarting the coordinator triggers rebalances.",
        "char_len": 400,
        "token_est": 100,
        "issue_type": "Bug",
    }
    return {**base, **kw}


def test_the_kip_set_is_the_one_brain_chunk_chose():
    """`Document.kind = 'KIP'` would miss the `ambiguous-kip` variant, which is stored as a
    Page. Asking `brain.chunk.scope` is how the two steps cannot drift apart."""
    keys, stats = kip_document_keys(MINI)
    assert keys == ["KIP-5"]
    assert stats["documents_reachable"] == 1
    assert stats["documents_in_scope"] == len(keys)


def test_selection_passes_the_scope_keys_and_the_floor_to_the_queries():
    ctx = FakeContext([doc_row()], [issue_row()])
    result = select(ctx, canonical_dir=MINI, min_chars=300)
    assert ctx.calls[0]["doc_keys"] == ["KIP-5"]
    assert ctx.calls[0]["kind"] == "section"
    assert ctx.calls[1]["kind"] == "description"
    assert ctx.calls[1]["min_chars"] == 300
    assert ctx.calls[1]["types"] == list(ISSUE_TYPES)
    assert [c.source for c in result.chunks] == ["kip_section", "issue_description"]


def test_every_chunk_carries_exactly_the_context_the_brief_asks_for():
    ctx = FakeContext([], [issue_row()])
    context = select(ctx, canonical_dir=MINI).chunks[0].context()
    assert set(context) == {
        "chunk_id",
        "parent_key",
        "parent_kind",
        "parent_title",
        "position",
        "kip_keys_referenced",
        "text",
    }
    assert context["kip_keys_referenced"] == ["KIP-5"]


def test_kip_keys_are_sorted_naturally_and_capped():
    """A KIP index page references dozens; the list is context, not a reading list."""
    keys = [f"KIP-{n}" for n in (1000, 9, 848, 100)]
    ctx = FakeContext([], [issue_row(kip_keys=keys)])
    assert select(ctx, canonical_dir=MINI).chunks[0].kip_keys_referenced == (
        "KIP-9",
        "KIP-100",
        "KIP-848",
        "KIP-1000",
    )
    many = FakeContext([], [issue_row(kip_keys=[f"KIP-{n}" for n in range(1, 40)])])
    assert len(select(many, canonical_dir=MINI).chunks[0].kip_keys_referenced) == MAX_KIP_KEYS


def test_the_stats_say_what_was_selected_and_by_which_rule():
    ctx = FakeContext([doc_row()], [issue_row(), issue_row(issue_type="Improvement")])
    stats = select(ctx, canonical_dir=MINI).stats
    assert stats["kip_sections"] == 1
    assert stats["issue_descriptions"] == 2
    assert stats["issue_descriptions_by_type"] == {"Bug": 1, "Improvement": 1}
    assert stats["chunks"] == 3
    assert stats["token_est"] == 207
    assert "No comments, no commits, no synthetic" in stats["rule"]
    assert stats["documents_in_scope_without_chunks"] == 0


def test_a_scoped_document_with_no_chunks_is_reported_not_hidden():
    ctx = FakeContext([], [])
    stats = select(ctx, canonical_dir=MINI).stats
    assert stats["documents_in_scope_without_chunks"] == 1


@pytest.mark.parametrize("kind", ["section", "description"])
def test_the_queries_name_the_chunk_kinds_phase_a_reads(kind):
    assert kind in (select_mod.DOC_CHUNK_KIND, select_mod.ISSUE_CHUNK_KIND)
