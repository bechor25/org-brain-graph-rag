from __future__ import annotations

import json

import pytest

from brain.resolve.build import (
    MAX_BATCH_BYTES,
    MAX_PAIRS,
    BuildError,
    assign_shards,
    envelope,
    pack,
    pair_id,
    serialise,
    shards_in_flight,
    to_batch_pair,
    write_batch_json,
    write_input_if_changed,
)
from brain.resolve.models import BatchInput, Evidence, make_pair
from tests.resolve_helpers import person


def candidates(n: int, *, evidence: int = 6):
    return [
        person(
            f"jira:p{i:04d}",
            f"Person Number {i}",
            evidence=[
                ("COMMENTED", f"KAFKA-{i}{j}", f"a reasonably long issue title number {i}-{j}")
                for j in range(evidence)
            ],
        )
        for i in range(n)
    ]


def grey_pairs(cands):
    return [
        make_pair(
            cands[i].id,
            cands[i + 1].id,
            kind="person",
            block="person",
            tier=2,
            rule="embedding_grey",
            score=0.85 + i / 10000,
            reason="cosine",
        )
        for i in range(len(cands) - 1)
    ]


def test_pair_id_is_stable_and_order_independent():
    assert pair_id("person", "a", "b") == pair_id("person", "b", "a")
    assert len(pair_id("person", "a", "b")) == 16
    assert pair_id("person", "a", "b") != pair_id("entity", "a", "b")


def test_a_side_carries_at_most_three_evidence_items():
    cands = candidates(2)
    by_id = {c.id: c for c in cands}
    pair = grey_pairs(cands)[0]
    batch_pair = to_batch_pair(pair, by_id)

    assert len(batch_pair.a.evidence) == 3
    assert isinstance(batch_pair.a.evidence[0], Evidence)
    assert batch_pair.a.identities == [cands[0].id]


def test_batches_hold_at_most_25_pairs():
    cands = candidates(60)
    by_id = {c.id: c for c in cands}
    pairs = [to_batch_pair(p, by_id) for p in grey_pairs(cands)]
    batches = pack(pairs, shard=0, kind="person")

    assert all(len(b.pairs) <= MAX_PAIRS for b in batches)
    assert sum(len(b.pairs) for b in batches) == len(pairs)


def test_a_batch_never_crosses_the_forty_kilobyte_budget():
    cands = candidates(60, evidence=3)
    by_id = {c.id: c for c in cands}
    # Long titles, so the byte budget bites before the pair count does.
    for c in cands:
        c.evidence = [Evidence(role="COMMENTED", key=e.key, title="x" * 900) for e in c.evidence]
    pairs = [to_batch_pair(p, by_id) for p in grey_pairs(cands)]
    batches = pack(pairs, shard=0, kind="person")

    for batch in batches:
        payload = envelope(
            batch_id=batch.batch_id,
            shard="shard-01",
            index=batch.index,
            kind="person",
            pairs=batch.pairs,
            generated_at="",
            schema_sha="",
        )
        assert len(serialise(payload).encode("utf-8")) <= MAX_BATCH_BYTES or len(batch.pairs) == 1
    assert max(len(b.pairs) for b in batches) < MAX_PAIRS


def test_the_envelope_is_the_model_the_agent_parses():
    cands = candidates(2)
    by_id = {c.id: c for c in cands}
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        kind="person",
        pairs=[to_batch_pair(grey_pairs(cands)[0], by_id)],
        generated_at="2026-09-06T00:00:00+00:00",
        schema_sha="deadbeef",
    )
    parsed = BatchInput.model_validate(payload)
    assert parsed.pair_count == 1
    # `band` is what is in the batch, not the tier's nominal window: the guard puts pairs
    # above 0.92 in here, and a label that said otherwise would teach the agent to distrust
    # the similarity it is given.
    assert parsed.band == [parsed.pairs[0].similarity] * 2
    assert "refused" in parsed.band_note
    assert parsed.schema_path == "brain/resolve/schema.json"


def test_the_band_of_a_batch_spans_its_own_pairs_even_above_the_window():
    cands = candidates(3)
    by_id = {c.id: c for c in cands}
    pairs = [to_batch_pair(p, by_id) for p in grey_pairs(cands)]
    pairs[0].similarity = 1.0
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        kind="person",
        pairs=pairs,
        generated_at="",
        schema_sha="x",
    )
    assert payload["band"] == [min(p.similarity for p in pairs), 1.0]


def test_shards_are_balanced_and_each_gets_the_hard_pairs():
    cands = candidates(21)
    by_id = {c.id: c for c in cands}
    pairs = [to_batch_pair(p, by_id) for p in grey_pairs(cands)]
    buckets = assign_shards(pairs, 2)

    assert abs(len(buckets[0]) - len(buckets[1])) <= 1
    assert sum(len(b) for b in buckets) == len(pairs)
    assert buckets[0][0].similarity >= buckets[1][0].similarity


def test_zero_shards_is_refused():
    with pytest.raises(BuildError):
        assign_shards([], 0)


def test_the_file_is_indented_and_rewritten_only_when_the_content_changes(tmp_path):
    path = tmp_path / "001.in.json"
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        kind="person",
        pairs=[],
        generated_at="2026-09-06T00:00:00+00:00",
        schema_sha="x",
    )
    assert write_input_if_changed(path, payload) is True
    assert path.read_text(encoding="utf-8").startswith("{\n  ")

    later = {**payload, "generated_at": "2026-09-07T00:00:00+00:00"}
    assert write_input_if_changed(path, later) is False
    assert write_input_if_changed(path, {**payload, "pair_count": 1}) is True


def test_a_line_an_agents_reader_would_truncate_is_refused(tmp_path):
    with pytest.raises(BuildError, match="longest line"):
        write_batch_json(tmp_path / "001.in.json", {"blob": "y" * 50_000})


def test_a_shard_with_finished_batches_is_reported_as_in_flight(tmp_path):
    shard = tmp_path / "shard-01"
    shard.mkdir()
    (shard / "status.json").write_text(json.dumps({"done": ["shard-01/001"]}), encoding="utf-8")
    (tmp_path / "shard-02").mkdir()
    (tmp_path / "shard-02" / "status.json").write_text(json.dumps({"done": []}), encoding="utf-8")

    assert shards_in_flight(tmp_path) == {"shard-01": 1}
