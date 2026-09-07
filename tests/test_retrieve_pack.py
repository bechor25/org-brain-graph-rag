"""The context budget: what survives, what is dropped, and what is never dropped."""

from __future__ import annotations

from brain.retrieve.pack import (
    BUDGET_TOKENS,
    SNIPPET_CHARS,
    clip,
    estimate_tokens,
    item_tokens,
    pack,
    total_tokens,
)
from brain.retrieve.types import Item


def item(kind: str, key: str, score: float, chars: int = 100) -> Item:
    return Item(kind=kind, key=key, score=score, snippet="x" * chars)


def test_small_result_is_untouched() -> None:
    items = [item("Chunk", f"c{i}", 1.0 - i / 10) for i in range(5)]
    packed, truncated = pack(items)
    assert not truncated
    assert [i.key for i in packed] == [i.key for i in items]


def test_packing_is_by_score_descending() -> None:
    items = [item("Chunk", "low", 0.1), item("Chunk", "high", 0.9), item("Chunk", "mid", 0.5)]
    packed, _ = pack(items)
    assert [i.key for i in packed] == ["high", "mid", "low"]


def test_budget_drops_the_tail_and_says_so() -> None:
    items = [item("Chunk", f"c{i}", 1.0 - i / 1000, chars=SNIPPET_CHARS) for i in range(200)]
    packed, truncated = pack(items)
    assert truncated
    assert len(packed) < len(items)
    assert total_tokens(packed) <= BUDGET_TOKENS
    assert packed[0].key == "c0"


def test_one_item_of_every_kind_survives_even_when_it_scores_last() -> None:
    """The rule that makes a traceability answer stay an answer."""
    noise = [item("Chunk", f"c{i}", 0.99, chars=SNIPPET_CHARS) for i in range(200)]
    rare = item("Change", "deadbeef", 0.001, chars=SNIPPET_CHARS)
    packed, truncated = pack([*noise, rare])
    assert truncated
    kinds = {i.kind for i in packed}
    assert kinds == {"Chunk", "Change"}
    assert any(i.key == "deadbeef" for i in packed)


def test_protected_set_larger_than_the_budget_is_still_kept_whole() -> None:
    huge = [item(kind, kind, 0.5, chars=20_000) for kind in ("Chunk", "WorkItem", "Document")]
    packed, truncated = pack(huge)
    assert {i.kind for i in packed} == {"Chunk", "WorkItem", "Document"}
    assert not truncated  # nothing was dropped; the budget simply lost


def test_empty_in_empty_out() -> None:
    assert pack([]) == ([], False)


def test_clip_normalises_whitespace_and_ellipsises() -> None:
    assert clip("  a\n\n b  ") == "a b"
    long = clip("y" * (SNIPPET_CHARS + 50))
    assert len(long) == SNIPPET_CHARS
    assert long.endswith("…")
    assert clip(None) == ""


def test_token_estimate_uses_the_measured_ratio() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 349) == 100
    assert item_tokens(Item(kind="Chunk", key="k")) > 0
