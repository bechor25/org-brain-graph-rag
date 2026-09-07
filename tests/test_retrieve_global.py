"""S5's two pure decisions: which level is in scope, and which of a duplicate pair survives.

The 22 cross-level duplicate pairs (`data/reports/communities.json`) are one member set that
Leiden reported at two levels because the coarse level merged nothing into it. They carry the
same members, so they carry near-identical embeddings, so a top-k over `community_embedding`
returns both — two report cards for one theme, paid for twice out of a 4k-token budget.

Everything here runs without a database: the ranking and the dedupe are functions over rows.
"""

from __future__ import annotations

import pytest

from brain.retrieve.global_search import (
    FINDING_CHARS,
    LEVELS,
    MAX_FINDINGS,
    dedupe_by_member_hash,
    fit_to_share,
    level_filter,
    trim_findings,
)
from brain.retrieve.pack import BUDGET_TOKENS, item_tokens, pack, total_tokens
from brain.retrieve.types import Item, Provenance


def _row(cid: str, level: int, member_hash: str, score: float) -> dict:
    return {"id": cid, "level": level, "member_hash": member_hash, "score": score}


# ------------------------------------------------------------------------------- levels


def test_level_names_map_to_the_numbers_the_graph_stores() -> None:
    assert LEVELS == {"fine": 0, "coarse": 1}
    assert level_filter("fine") == [0]
    assert level_filter("coarse") == [1]
    assert level_filter("any") is None


def test_an_unknown_level_is_refused_rather_than_silently_widened() -> None:
    with pytest.raises(ValueError, match="level"):
        level_filter("medium")


# -------------------------------------------------------------------------------- dedupe


def test_a_duplicate_pair_returns_once_and_the_fine_one_wins() -> None:
    rows = [
        _row("L1-351", 1, "f7b7", 0.91),
        _row("L0-280", 0, "f7b7", 0.90),
        _row("L0-8", 0, "9b5f", 0.80),
    ]
    kept, dropped = dedupe_by_member_hash(rows)
    assert [r["id"] for r in kept] == ["L0-280", "L0-8"]
    assert dropped == {"L0-280": ["L1-351"]}


def test_the_survivor_inherits_the_better_score_of_the_pair() -> None:
    """Otherwise keeping the fine report would push the theme down the ranking."""
    kept, _ = dedupe_by_member_hash(
        [_row("L1-351", 1, "f7b7", 0.91), _row("L0-280", 0, "f7b7", 0.5)]
    )
    assert kept[0]["id"] == "L0-280"
    assert kept[0]["score"] == pytest.approx(0.91)


def test_distinct_member_sets_are_never_merged() -> None:
    rows = [_row("L0-1", 0, "aaa", 0.9), _row("L1-2", 1, "bbb", 0.8)]
    kept, dropped = dedupe_by_member_hash(rows)
    assert [r["id"] for r in kept] == ["L0-1", "L1-2"]
    assert dropped == {}


def test_a_community_with_no_member_hash_is_kept_as_itself() -> None:
    """A hash is written by `brain communities`; a null must not collapse unrelated rows."""
    rows = [_row("L0-1", 0, None, 0.9), _row("L1-2", 1, None, 0.8)]
    kept, _ = dedupe_by_member_hash(rows)
    assert [r["id"] for r in kept] == ["L0-1", "L1-2"]


def test_output_stays_in_score_order_after_a_swap() -> None:
    rows = [
        _row("L1-9", 1, "same", 0.99),
        _row("L0-2", 0, "other", 0.95),
        _row("L0-9", 0, "same", 0.10),
    ]
    kept, _ = dedupe_by_member_hash(rows)
    assert [r["id"] for r in kept] == ["L0-9", "L0-2"]
    assert [r["score"] for r in kept] == [pytest.approx(0.99), pytest.approx(0.95)]


# ----------------------------------------------------------------------------- budgeting


def _community(cid: str, findings: int, statement_chars: int = 200) -> Item:
    return Item(
        kind="Community",
        key=cid,
        title="A theme",
        snippet="s" * 360,
        score=0.7,
        props={
            "findings": [
                {"statement": "f" * statement_chars, "evidence_chunk_ids": [f"{n:040x}"]}
                for n in range(findings)
            ],
            "finding_count": findings,
        },
        provenance=[Provenance(chunk_id=f"{n:040x}", source=cid) for n in range(5)],
    )


def test_trim_findings_clips_and_caps_but_keeps_the_evidence_id() -> None:
    raw = [{"statement": "x" * 900, "evidence_chunk_ids": ["a", "b", "c"]} for _ in range(9)]
    trimmed = trim_findings(raw)
    assert len(trimmed) == MAX_FINDINGS
    assert len(trimmed[0]["statement"]) <= FINDING_CHARS
    assert trimmed[0]["evidence_chunk_ids"] == ["a"]


def test_five_reports_fit_the_four_thousand_token_ceiling() -> None:
    """The behaviour the ceiling is for: k themes come back, not the two that fit untrimmed."""
    share = BUDGET_TOKENS // 5
    items = [fit_to_share(_community(f"L0-{n}", 8), share) for n in range(5)]
    assert all(item_tokens(i) <= share for i in items)
    assert total_tokens(items) <= BUDGET_TOKENS
    _, truncated = pack(items)
    assert truncated is False


def test_shedding_stops_before_the_item_stops_being_evidence() -> None:
    """One finding and one citation survive any budget; below that there is nothing to cite."""
    item = fit_to_share(_community("L0-1", 8), share_tokens=1)
    assert len(item.props["findings"]) == 1
    assert len(item.provenance) == 1
    assert item.props["finding_count"] == 8


def test_a_report_that_already_fits_is_left_alone() -> None:
    before = _community("L0-1", 3)
    after = fit_to_share(_community("L0-1", 3), BUDGET_TOKENS)
    assert after.props["findings"] == before.props["findings"]
    assert len(after.provenance) == len(before.provenance)
