from __future__ import annotations

import json
from pathlib import Path

from brain.common.jsonschema_mini import unsupported_keywords
from brain.resolve.build import SCHEMA_PATH, envelope, pair_id, to_batch_pair, write_batch_json
from brain.resolve.decisions import (
    MAX_RETRIES,
    QUARANTINE_DIR,
    RETRY_DIR,
    RETRY_FIELD,
    Batch,
    discover,
    handle_failure,
    parse_batch,
)
from brain.resolve.models import make_pair
from tests.resolve_helpers import person

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def write_pair_batch(root: Path, shard: str = "shard-01", index: int = 1) -> tuple[Path, str]:
    a = person("jira:a", "Alice A", evidence=[("COMMENTED", "KAFKA-1", "t")])
    b = person("git:b@example.org", "Alice A", evidence=[("AUTHORED", "KAFKA-2", "u")])
    by_id = {c.id: c for c in (a, b)}
    pair = make_pair(
        a.id,
        b.id,
        kind="person",
        block="person",
        tier=2,
        rule="embedding_grey",
        score=0.85,
        reason="cosine",
    )
    payload = envelope(
        batch_id=f"{shard}/{index:03d}",
        shard=shard,
        index=index,
        kind="person",
        pairs=[to_batch_pair(pair, by_id)],
        generated_at="2026-09-06T00:00:00+00:00",
        schema_sha="x",
    )
    path = root / shard / f"{index:03d}.in.json"
    write_batch_json(path, payload)
    return path, pair_id("person", a.id, b.id)


def batch_for(out_path: Path) -> Batch:
    return Batch(
        batch_id=f"{out_path.parent.name}/{int(out_path.name.split('.')[0]):03d}",
        shard=out_path.parent.name,
        index=int(out_path.name.split(".")[0]),
        path=out_path,
    )


def write_out(root: Path, body: dict, shard: str = "shard-01", index: int = 1) -> Path:
    path = root / shard / f"{index:03d}.out.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def test_the_schema_uses_nothing_the_validator_does_not_implement():
    assert unsupported_keywords(SCHEMA) == set()


def test_a_well_formed_batch_parses(tmp_path):
    _, pid = write_pair_batch(tmp_path)
    out = write_out(
        tmp_path,
        {
            "batch_id": "shard-01/001",
            "decisions": [
                {"pair_id": pid, "verdict": "same", "reason": "both touch the same KAFKA issue"}
            ],
        },
    )
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    assert batch.ok and batch.errors == []


def test_a_verdict_outside_the_closed_set_is_a_schema_error(tmp_path):
    _, pid = write_pair_batch(tmp_path)
    out = write_out(
        tmp_path,
        {
            "batch_id": "shard-01/001",
            "decisions": [{"pair_id": pid, "verdict": "maybe", "reason": "not one of the three"}],
        },
    )
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    assert any("maybe" in e for e in batch.errors)


def test_a_pair_id_from_another_batch_is_rejected(tmp_path):
    write_pair_batch(tmp_path)
    out = write_out(
        tmp_path,
        {
            "batch_id": "shard-01/001",
            "decisions": [{"pair_id": "0" * 16, "verdict": "same", "reason": "invented pair id"}],
        },
    )
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    assert any("not in this batch" in e for e in batch.errors)


def test_a_pair_left_unanswered_is_rejected(tmp_path):
    write_pair_batch(tmp_path)
    out = write_out(tmp_path, {"batch_id": "shard-01/001", "decisions": []})
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    # minItems in the schema catches the empty list; the message says the batch is short
    assert batch.errors


def test_a_batch_answering_a_pair_twice_is_rejected(tmp_path):
    _, pid = write_pair_batch(tmp_path)
    out = write_out(
        tmp_path,
        {
            "batch_id": "shard-01/001",
            "decisions": [
                {"pair_id": pid, "verdict": "same", "reason": "the first answer given"},
                {"pair_id": pid, "verdict": "different", "reason": "and now the opposite"},
            ],
        },
    )
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    assert any("answered twice" in e for e in batch.errors)


def test_an_output_with_no_input_beside_it_cannot_be_checked(tmp_path):
    out = write_out(
        tmp_path,
        {
            "batch_id": "shard-01/001",
            "decisions": [{"pair_id": "a" * 16, "verdict": "unsure", "reason": "no input here"}],
        },
    )
    batch = batch_for(out)
    parse_batch(batch, SCHEMA)
    assert any("no input beside it" in e for e in batch.errors)


def test_a_failing_batch_goes_to_retry_twice_then_quarantine(tmp_path):
    body = {
        "batch_id": "shard-01/001",
        "decisions": [{"pair_id": "z" * 16, "verdict": "same", "reason": "nothing matches this"}],
    }
    for attempt in range(1, MAX_RETRIES + 2):
        out = write_out(tmp_path, body)
        batch = batch_for(out)
        batch.errors = ["broken"]
        if attempt > 1:
            source = tmp_path / "shard-01" / (RETRY_DIR if attempt <= MAX_RETRIES else RETRY_DIR)
            prior = json.loads((source / "001.out.json").read_text(encoding="utf-8"))
            out.write_text(json.dumps(prior), encoding="utf-8")
        record = handle_failure(batch)
        assert record["attempts"] == attempt
        assert not out.exists()
    assert (tmp_path / "shard-01" / QUARANTINE_DIR / "001.out.json").is_file()
    quarantined = json.loads(
        (tmp_path / "shard-01" / QUARANTINE_DIR / "001.out.json").read_text(encoding="utf-8")
    )
    assert quarantined[RETRY_FIELD] == MAX_RETRIES + 1
    assert quarantined["_resolve_errors"] == ["broken"]


def test_discover_finds_outputs_in_shard_then_batch_order(tmp_path):
    for shard, index in (("shard-02", 1), ("shard-01", 2), ("shard-01", 1)):
        write_out(tmp_path, {"batch_id": f"{shard}/{index:03d}", "decisions": []}, shard, index)
    assert [b.batch_id for b in discover(tmp_path)] == [
        "shard-01/001",
        "shard-01/002",
        "shard-02/001",
    ]
