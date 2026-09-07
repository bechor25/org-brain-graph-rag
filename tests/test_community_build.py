"""What a rebuild does with the reports the last run paid an agent to write.

The rule is one line: a community gets back the report written for *its* member set at
*its* level, and nothing else. It is worth a file of its own because getting it wrong is
invisible — the graph still holds 186 reports, every one of them still has a title, a
summary and a batch id, and 44 of them are quietly attributed to a batch that never wrote
them. Nothing fails; the provenance just stops being true.
"""

from __future__ import annotations

from typing import Any

from brain.community.build import carried_rows
from brain.community.graph import COPIED_FROM, reports_by_member, reports_by_member_level
from brain.community.partition import Community, Member

HASH_A = "a" * 40


def community(cid: int, level: int, *, size: int = 6, min_size: int = 5) -> Community:
    """`size` members with keys that do not depend on the level: same hash at both."""
    return Community(
        level=level,
        community_id=cid,
        members=[Member(label="Entity", key=f"k{i}") for i in range(size)],
        min_size=min_size,
    )


def stored(previous_id: str, level: int, member_hash: str, **kw: Any) -> dict[str, Any]:
    """One row of `read_reports`: a report as the graph holds it."""
    base: dict[str, Any] = {
        "member_hash": member_hash,
        "level": level,
        "previous_id": previous_id,
        "title": f"Report of {previous_id}",
        "summary": f"What {previous_id} is about.",
        "findings": ['{"statement": "x", "evidence_chunk_ids": ["a"]}'],
        "finding_statements": ["x"],
        "rank": 7.0,
        "rank_reason": "because",
        "reported_at": "2026-09-07T15:00:00+00:00",
        COPIED_FROM: None,
        "evidence_chunk_ids": ["a"],
        "batch_id": f"shard-01/{previous_id[-3:]}",
        "model": "opus:community-summarizer",
        "extracted_at": "2026-09-07T14:00:00+00:00",
        "embedding": [0.1, 0.2],
        "embed_hash": "e" * 40,
    }
    return {**base, **kw}


def carry(communities, rows):
    return carried_rows(communities, reports_by_member_level(rows), reports_by_member(rows))


def test_a_community_gets_back_the_report_written_for_it():
    fine = community(594, 0)
    rows = [stored("L0-594", 0, fine.member_hash)]

    carried, ids = carry([fine], rows)

    assert ids == ["L0-594"]
    assert carried[0]["props"]["batch_id"] == "shard-01/594"
    assert COPIED_FROM not in carried[0]["props"], "its own report is not a copy"


def test_two_levels_holding_the_same_members_each_keep_their_own_report():
    """22 member sets on the live graph carry a report at both levels. Both survive."""
    fine, coarse = community(594, 0), community(138, 1)
    assert fine.member_hash == coarse.member_hash
    rows = [stored("L0-594", 0, fine.member_hash), stored("L1-138", 1, coarse.member_hash)]

    carried, ids = carry([fine, coarse], rows)

    assert ids == ["L0-594", "L1-138"]
    by_id = {r["id"]: r["props"] for r in carried}
    assert by_id["L0-594"]["title"] == "Report of L0-594"
    assert by_id["L1-138"]["title"] == "Report of L1-138"
    # the halves that a member-hash-only carry silently swapped
    assert by_id["L0-594"]["batch_id"] == "shard-01/594"
    assert by_id["L1-138"]["batch_id"] == "shard-01/138"
    assert by_id["L1-138"]["extracted_at"] == "2026-09-07T14:00:00+00:00"
    assert all(COPIED_FROM not in p for p in by_id.values())


def test_a_level_with_no_report_of_its_own_borrows_one_and_says_so():
    """The batches copy rule, applied by the rebuild: the same text, marked as a copy."""
    fine, coarse = community(594, 0), community(138, 1)
    rows = [stored("L0-594", 0, fine.member_hash)]

    carried, ids = carry([fine, coarse], rows)

    assert ids == ["L0-594", "L1-138"]
    borrowed = next(r for r in carried if r["id"] == "L1-138")
    assert borrowed["props"]["title"] == "Report of L0-594"
    assert borrowed["props"][COPIED_FROM] == "L0-594"
    assert borrowed["embedding"] == [0.1, 0.2], "the same text embeds to the same place"


def test_a_report_that_was_already_a_copy_stays_one():
    coarse = community(138, 1)
    rows = [stored("L1-138", 1, coarse.member_hash, **{COPIED_FROM: "L0-594"})]

    carried, _ = carry([coarse], rows)

    assert carried[0]["props"][COPIED_FROM] == "L0-594"


def test_a_community_whose_members_changed_gets_nothing_back():
    rows = [stored("L0-594", 0, HASH_A)]
    carried, ids = carry([community(594, 0)], rows)
    assert (carried, ids) == ([], [])


def test_a_misc_community_is_never_given_a_report():
    """It is not summarised, so a report on it would be a report nobody asked for."""
    tiny = community(9, 0, size=2, min_size=5)
    rows = [stored("L0-9", 0, tiny.member_hash)]
    assert carry([tiny], rows) == ([], [])


def test_the_lookup_by_member_set_alone_is_stable_between_runs():
    """Which of two equally valid reports gets copied is arbitrary — but never random."""
    rows = [stored("L1-138", 1, HASH_A), stored("L0-594", 0, HASH_A)]
    assert reports_by_member(rows)[HASH_A]["previous_id"] == "L0-594"
    assert reports_by_member(list(reversed(rows)))[HASH_A]["previous_id"] == "L0-594"
