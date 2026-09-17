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


def test_protected_set_larger_than_the_budget_is_still_kept_whole_and_says_truncated() -> None:
    """Nothing was dropped and the ceiling was still breached — the caller must be told."""
    huge = [
        Item(
            kind=kind,
            key=kind,
            score=0.5,
            props={f"p{n}": "x" * 3_000 for n in range(6)},
        )
        for kind in ("Chunk", "WorkItem", "Document", "Person", "Change", "Container")
    ]
    packed, truncated = pack(huge)
    assert {i.kind for i in packed} == {i.kind for i in huge}
    assert total_tokens(packed) > BUDGET_TOKENS
    assert truncated, "a kept set over the budget is a truncated answer, dropped items or not"


def test_a_whole_document_in_props_is_clipped_before_it_is_ever_costed() -> None:
    """The `RETURN d.body_md` case: one row, 66k tokens, `truncated=false`. Never again."""
    row = Item(kind="Row", key="row:1", props={"body_md": "x" * 60_000})
    packed, truncated = pack([row])
    assert total_tokens(packed) <= BUDGET_TOKENS
    assert truncated, "text was cut, so the answer is not the whole answer"
    assert len(packed[0].props["body_md"]) == SNIPPET_CHARS
    assert packed[0].props["body_md"].endswith("…")
    assert row.props["body_md"] == "x" * 60_000, "pack copies, it does not edit the caller's item"


def test_clipping_reaches_a_string_nested_in_a_list_or_a_map() -> None:
    packed, truncated = pack(
        [
            Item(
                kind="Row",
                key="row:2",
                snippet="y" * 5_000,
                props={"rows": [{"body": "z" * 9_000}], "n": 3, "flag": True},
            )
        ]
    )
    props = packed[0].props
    assert len(packed[0].snippet) == SNIPPET_CHARS
    assert len(props["rows"][0]["body"]) == SNIPPET_CHARS
    assert props["n"] == 3 and props["flag"] is True
    assert truncated


def test_an_answer_that_fits_is_not_marked_truncated() -> None:
    packed, truncated = pack([item("Chunk", "c1", 1.0, chars=SNIPPET_CHARS)])
    assert not truncated
    assert len(packed) == 1


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
