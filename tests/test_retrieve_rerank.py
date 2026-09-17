"""The reranker, and the machine that does not have it.

`BAAI/bge-reranker-v2-m3` is 2 GB behind an optional extra, so none of these tests download
anything: the model is a stub with a `predict`, and the interesting case is the *absence*
of the real one. Plan decision 5 says a missing reranker is a warning and the original
order — never an exception — because the Plan 3 evaluation runs with and without rerank and
must survive both.
"""

from __future__ import annotations

import sys

import pytest

from brain.retrieve import rerank
from brain.retrieve.types import Item


class _StubModel:
    """Scores a pair by how many of the query's words the text repeats."""

    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = scores
        self.seen: list[tuple[str, str]] = []

    def predict(self, pairs):
        self.seen = list(pairs)
        if self.scores is not None:
            return self.scores
        return [
            float(sum(1 for w in q.lower().split() if w in t.lower())) / (len(q.split()) or 1)
            for q, t in pairs
        ]


@pytest.fixture(autouse=True)
def _fresh():
    rerank.reset()
    yield
    rerank.reset()


def _rows(*texts: str) -> list[dict]:
    return [
        {"node": {"id": f"c{i}", "text": text}, "score": 1.0 / (i + 1)}
        for i, text in enumerate(texts)
    ]


# ------------------------------------------------------------------ the missing model


def test_a_missing_library_warns_once_and_changes_nothing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    rows = _rows("first", "second")
    with pytest.warns(RuntimeWarning, match="rerank disabled"):
        assert rerank.rerank_rows("q", rows) == rows
    assert rerank.available() is False
    assert "sentence-transformers" in rerank.status()["error"]


def test_a_model_that_will_not_load_is_not_a_crash(monkeypatch) -> None:
    class _Broken:
        def __init__(self, *a, **kw):
            raise OSError("no such model")

    module = type(sys)("sentence_transformers")
    module.CrossEncoder = _Broken
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    with pytest.warns(RuntimeWarning):
        assert rerank.rerank_rows("q", _rows("a")) == _rows("a")
    assert rerank.status()["available"] is False


def test_inference_failure_falls_back_to_the_original_order(monkeypatch) -> None:
    class _Explodes:
        def predict(self, pairs):
            raise RuntimeError("cuda is on fire")

    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _Explodes())
    rows = _rows("a", "b")
    with pytest.warns(RuntimeWarning):
        assert rerank.rerank_rows("q", rows) == rows


# ------------------------------------------------------------------ the working model


def test_rows_are_reordered_and_the_fusion_score_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([0.1, 0.9]))
    rows = _rows("about disks", "about rebalance latency")
    out = rerank.rerank_rows("why is the rebalance slow", rows)
    assert [r["node"]["id"] for r in out] == ["c1", "c0"]
    assert out[0]["score"] == 0.9
    assert out[0]["rrf_score"] == rows[1]["score"], "the pre-rerank score stays recoverable"


def test_logits_are_squashed_into_the_score_range(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([-4.0, 6.0]))
    out = rerank.rerank_rows("q", _rows("a", "b"))
    assert all(0.0 <= r["score"] <= 1.0 for r in out)
    assert out[0]["node"]["id"] == "c1"


def test_probabilities_are_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([0.2, 0.8]))
    out = rerank.rerank_rows("q", _rows("a", "b"))
    assert [r["score"] for r in out] == [0.8, 0.2]


def test_top_k_trims_after_reranking(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([0.1, 0.9, 0.5]))
    out = rerank.rerank_rows("q", _rows("a", "b", "c"), top_k=2)
    assert [r["node"]["id"] for r in out] == ["c1", "c2"]


def test_the_pair_is_query_and_text(monkeypatch) -> None:
    """A cross-encoder reads the pair; passing the text alone would score nothing useful."""
    stub = _StubModel([0.5])
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: stub)
    rerank.rerank_rows("why is it slow", _rows("because of rebalances"))
    assert stub.seen == [("why is it slow", "because of rebalances")]


def test_items_are_reranked_by_title_and_snippet(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([0.2, 0.7]))
    items = [
        Item(kind="Chunk", key="a", title="A", snippet="about disks", score=0.9),
        Item(kind="Chunk", key="b", title="B", snippet="about rebalance", score=0.1),
    ]
    out = rerank.rerank_items("rebalance", items)
    assert [i.key for i in out] == ["b", "a"]
    assert out[0].props["rrf_score"] == 0.1


def test_empty_input_is_returned_untouched(monkeypatch) -> None:
    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([]))
    assert rerank.rerank_rows("q", []) == []
    assert rerank.rerank_items("q", []) == []


# ------------------------------------------------------------------ S1's hook


def test_s1_uses_this_module_when_it_is_installed(monkeypatch) -> None:
    """Task 1 left a hook in `hybrid._rerank`; this is the contract test for it."""
    from brain.retrieve import hybrid

    monkeypatch.setattr(rerank, "load_model", lambda *a, **kw: _StubModel([0.1, 0.9]))
    rows = _rows("about disks", "about rebalance latency")
    out = hybrid._rerank("why is the rebalance slow", rows)
    assert [r["node"]["id"] for r in out] == ["c1", "c0"]
