"""Packing communities into batches an agent can actually read.

The rule step 04 paid 42% of a corpus to learn: a batch is measured on its serialised
bytes, and a line an agent's reader would truncate is a bug, not a warning. Everything here
runs against the packer, not against Neo4j.
"""

from __future__ import annotations

import json

import pytest

from brain.community.batches import (
    DEFAULT_EVIDENCE_CHARS,
    assign_shards,
    envelope,
    pack,
    serialise,
    split_copyable,
)
from brain.community.build import BuildError
from brain.community.models import BatchInput
from brain.extract.build import MAX_BATCH_BYTES, longest_line_bytes
from tests.community_helpers import community_context, evidence


def big(cid: str, *, chars: int = DEFAULT_EVIDENCE_CHARS, chunks: int = 8) -> dict:
    return community_context(
        community_id=cid,
        evidence=[evidence(chunk_id=f"{i:040x}", text="x" * chars) for i in range(chunks)],
    )


def packed(communities, **kw):
    return pack(communities, shard=0, generated_at="2026-09-07T00:00:00+00:00", **kw)


def test_a_batch_stays_under_the_forty_kilobyte_budget():
    batches = packed([big(f"L0-{i}") for i in range(20)])
    assert len(batches) > 1
    for batch in batches:
        payload = envelope(
            batch_id=batch.batch_id,
            shard="shard-01",
            index=batch.index,
            communities=batch.communities,
            generated_at="2026-09-07T00:00:00+00:00",
            schema_sha="0" * 64,
        )
        assert len(serialise(payload).encode("utf-8")) <= MAX_BATCH_BYTES


def test_no_community_is_lost_or_duplicated_by_the_packing():
    communities = [big(f"L0-{i}") for i in range(20)]
    ids = [c["community_id"] for b in packed(communities) for c in b.communities]
    assert sorted(ids) == sorted(c["community_id"] for c in communities)


def test_a_community_too_big_for_the_budget_still_gets_a_batch_of_its_own():
    """Thinning its evidence to fit would make the report weaker than the community is."""
    batches = packed([big("L0-1", chars=60_000, chunks=1)])
    assert len(batches) == 1 and len(batches[0].communities) == 1


def test_batches_are_numbered_from_one_within_their_shard():
    batches = packed([big(f"L0-{i}") for i in range(20)])
    assert [b.index for b in batches] == list(range(1, len(batches) + 1))
    assert batches[0].batch_id == "shard-01/001"


def test_the_serialised_batch_is_indented_and_has_no_untruncatable_line():
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        communities=[big("L0-1")],
        generated_at="2026-09-07T00:00:00+00:00",
        schema_sha="0" * 64,
    )
    text = serialise(payload)
    assert text.startswith("{\n  ")
    assert longest_line_bytes(text) < 5_000


def test_the_envelope_round_trips_through_the_input_model():
    payload = envelope(
        batch_id="shard-02/003",
        shard="shard-02",
        index=3,
        communities=[community_context(), community_context(community_id="L1-4", level=1)],
        generated_at="2026-09-07T00:00:00+00:00",
        schema_sha="0" * 64,
    )
    parsed = BatchInput.model_validate(json.loads(serialise(payload)))
    assert parsed.community_count == 2
    assert parsed.evidence_ids("L0-1") == {"a" * 40}
    assert parsed.evidence_ids("L9-9") == set()


def test_shards_are_balanced_by_reading_not_by_community_count():
    small = [community_context(community_id=f"L0-{i}", evidence=[]) for i in range(6)]
    heavy = big("L0-99", chars=4_000)
    buckets = assign_shards([*small, heavy], 2)
    assert sum(len(b) for b in buckets) == 7
    # the heavy one is alone with a share of the light ones, not paired with three of them
    heavy_bucket = next(b for b in buckets if any(c["community_id"] == "L0-99" for c in b))
    assert len(heavy_bucket) < 6


def test_a_shard_keeps_its_communities_in_a_stable_order():
    communities = [community_context(community_id=f"L0-{i}", evidence=[]) for i in range(8)]
    first = assign_shards(communities, 2)
    second = assign_shards(list(reversed(communities)), 2)
    assert [[c["community_id"] for c in b] for b in first] == [
        [c["community_id"] for c in b] for b in second
    ]


def test_zero_shards_is_refused():
    with pytest.raises(BuildError, match="at least 1"):
        assign_shards([community_context()], 0)


# ------------------------------------------------- the same members at two levels at once


def wanted(cid: str, level: int, member_hash: str) -> dict:
    """One row of what `collect` found: a community that has no report of its own yet."""
    return {
        "community_id": cid,
        "level": level,
        "level_name": "fine" if level == 0 else "coarse",
        "size": 33,
        "member_hash": member_hash,
        "misc": False,
        "summary": None,
    }


def stored_report(previous_id: str = "L0-594") -> dict:
    return {
        "previous_id": previous_id,
        "title": "Consumer rebalance moves to the coordinator",
        "summary": "…",
        "findings": ['{"statement": "x", "evidence_chunk_ids": ["a"]}'],
        "rank": 8.5,
        "evidence_chunk_ids": ["a"],
        "batch_id": "shard-01/003",
        "model": "opus:community-summarizer",
        "extracted_at": "2026-09-07T14:00:00+00:00",
        "embedding": [0.1, 0.2],
        "embed_hash": "e" * 40,
    }


def test_a_community_whose_members_already_carry_a_report_is_copied_not_resummarized():
    """L1-138 holds exactly the members of L0-594. Paying an agent twice for one text."""
    to_copy, to_summarize = split_copyable([wanted("L1-138", 1, "h1")], {"h1": stored_report()})

    assert to_summarize == []
    assert [c["community_id"] for c in to_copy] == ["L1-138"]
    assert to_copy[0]["source"] == "L0-594"
    assert to_copy[0]["props"]["title"] == "Consumer rebalance moves to the coordinator"
    assert to_copy[0]["props"]["copied_from"] == "L0-594"
    # the vector too: the same title and summary embed to the same place
    assert to_copy[0]["embedding"] == [0.1, 0.2]


def test_a_copied_report_keeps_the_provenance_of_the_run_that_wrote_it():
    """Conventions rule 3: the batch and model that produced this text, not this run."""
    to_copy, _ = split_copyable([wanted("L1-138", 1, "h1")], {"h1": stored_report()})
    props = to_copy[0]["props"]
    assert props["batch_id"] == "shard-01/003"
    assert props["model"] == "opus:community-summarizer"
    assert props["extracted_at"] == "2026-09-07T14:00:00+00:00"
    assert props["evidence_chunk_ids"] == ["a"]
    assert "embedding" not in props and "previous_id" not in props


def test_a_community_with_no_report_anywhere_still_goes_to_an_agent():
    to_copy, to_summarize = split_copyable([wanted("L1-9", 1, "unseen")], {"h1": stored_report()})
    assert to_copy == []
    assert [c["community_id"] for c in to_summarize] == ["L1-9"]


def test_a_report_is_never_copied_onto_the_community_it_already_belongs_to():
    """The source is the row's own id: there is nothing to copy, and it is not wanted."""
    to_copy, to_summarize = split_copyable(
        [wanted("L0-594", 0, "h1")], {"h1": stored_report("L0-594")}
    )
    assert to_copy == []
    assert [c["community_id"] for c in to_summarize] == ["L0-594"]
