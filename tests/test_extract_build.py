"""Batching: the 40 KB rule, the shard rule, and the rebuild rules.

Step 04's lesson is the reason this file exists at all — a batch an agent's reader truncates
produces valid output over text nobody read, and nothing fails. So the size is asserted on
the bytes actually written, not on an estimate.
"""

from __future__ import annotations

import json

import pytest

from brain.extract.build import (
    MAX_BATCH_BYTES,
    MAX_LINE_BYTES,
    BuildError,
    assign_shards,
    build_manifest,
    envelope,
    group_by_parent,
    longest_line_bytes,
    pack,
    plan_batches,
    remove_stale_files,
    serialise,
    shard_name,
    shards_in_flight,
    write_batch_json,
    write_input_if_changed,
)
from brain.extract.select import SelectedChunk, Selection


def chunk(key: str, position: int = 0, chars: int = 500, kind: str = "Document") -> SelectedChunk:
    text = f"{key}#{position} " + "x" * max(0, chars - len(key) - 4)
    return SelectedChunk(
        chunk_id=f"{abs(hash((key, position))):040x}"[:40],
        parent_key=key,
        parent_kind=kind,
        parent_title=f"title of {key}",
        position=position,
        kip_keys_referenced=("KIP-5",) if kind == "WorkItem" else (),
        text=text,
        char_len=len(text),
        token_est=len(text) // 4,
        source="kip_section" if kind == "Document" else "issue_description",
    )


def selection(chunks) -> Selection:
    return Selection(chunks=list(chunks), stats={"chunks": len(chunks), "parents": 0})


def sizes(batches) -> list[int]:
    return [
        len(
            serialise(
                envelope(
                    batch_id=b.batch_id,
                    shard=shard_name(b.shard),
                    index=b.index,
                    chunks=b.chunks,
                    generated_at="",
                    schema_sha="",
                )
            ).encode("utf-8")
        )
        for b in batches
    ]


# --------------------------------------------------------------------------- packing


def test_a_batch_never_holds_more_chunks_than_asked_for():
    batches = pack([chunk("KIP-1", i, 200) for i in range(25)], shard=0, batch_size=20)
    assert [len(b.chunks) for b in batches] == [20, 5]


def test_a_batch_is_split_before_it_crosses_the_size_budget():
    """20 sections of 2.6 KB is 52 KB — the real corpus's common case."""
    batches = pack([chunk("KIP-1", i, 2600) for i in range(20)], shard=0, batch_size=20)
    assert max(sizes(batches)) <= MAX_BATCH_BYTES
    assert sum(len(b.chunks) for b in batches) == 20
    assert len(batches) > 1


def test_a_chunk_too_big_for_a_batch_gets_a_batch_of_its_own():
    """Truncating it would break the verbatim-quote rule, which is the provenance."""
    chunks = [
        chunk("KIP-1", 0, 200),
        chunk("KIP-1", 1, MAX_BATCH_BYTES + 5000),
        chunk("KIP-1", 2, 200),
    ]
    batches = pack(chunks, shard=0, batch_size=20)
    assert [len(b.chunks) for b in batches] == [1, 1, 1]
    assert max(sizes(batches)) > MAX_BATCH_BYTES  # reported, not silently dropped


def test_batches_are_numbered_from_one_inside_their_shard():
    batches = pack([chunk("KIP-1", i, 200) for i in range(5)], shard=2, batch_size=2)
    assert [b.batch_id for b in batches] == ["shard-03/001", "shard-03/002", "shard-03/003"]


# --------------------------------------------------------------------------- sharding


def test_a_parents_chunks_never_split_across_shards():
    """The agent naming entities in section 4 has already read sections 1-3."""
    chunks = [chunk(f"KIP-{k}", i, 900) for k in range(1, 9) for i in range(3)]
    planned = plan_batches(
        selection(chunks), shards=4, batch_size=20, generated_at="", schema_sha=""
    )
    by_parent: dict[str, set[int]] = {}
    for batch in planned:
        for c in batch.chunks:
            by_parent.setdefault(c.parent_key, set()).add(batch.shard)
    assert all(len(shards) == 1 for shards in by_parent.values()), by_parent


def test_shards_are_balanced_by_characters_not_by_parent_count():
    parents = group_by_parent(
        [chunk("KIP-1", 0, 9000), *[chunk(f"KAFKA-{n}", 0, 400, "WorkItem") for n in range(9)]]
    )
    buckets = assign_shards(parents, 3)
    loads = [sum(p.chars for p in bucket) for bucket in buckets]
    assert max(loads) - min(loads) < 9000  # the big one did not simply land on top of a share
    assert sum(len(b) for b in buckets) == len(parents)


def test_assign_shards_refuses_zero_shards():
    with pytest.raises(BuildError):
        assign_shards(group_by_parent([chunk("KIP-1")]), 0)


# ---------------------------------------------------------------------------- writing


def test_a_batch_file_is_indented_so_a_line_reader_can_page_through_it(tmp_path):
    path = tmp_path / "001.in.json"
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        chunks=[chunk("KIP-1", 0, 3000)],
        generated_at="now",
        schema_sha="s",
    )
    write_batch_json(path, payload)
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") > 10
    assert '\n  "batch_id": "shard-01/001",' in text
    assert longest_line_bytes(text) < MAX_LINE_BYTES
    assert json.loads(text)["chunk_count"] == 1


def test_a_batch_with_a_line_no_agent_could_read_is_an_error_not_a_warning(tmp_path):
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        chunks=[chunk("KIP-1", 0, MAX_LINE_BYTES + 1000)],
        generated_at="now",
        schema_sha="s",
    )
    with pytest.raises(BuildError, match="longest line"):
        write_batch_json(tmp_path / "001.in.json", payload)


def test_a_rebuild_that_changes_nothing_leaves_the_file_alone(tmp_path):
    path = tmp_path / "001.in.json"
    payload = envelope(
        batch_id="shard-01/001",
        shard="shard-01",
        index=1,
        chunks=[chunk("KIP-1")],
        generated_at="first",
        schema_sha="s",
    )
    assert write_input_if_changed(path, payload) is True
    assert write_input_if_changed(path, {**payload, "generated_at": "second"}) is False
    assert json.loads(path.read_text())["generated_at"] == "first"
    changed = {**payload, "generated_at": "third", "chunk_count": 2}
    assert write_input_if_changed(path, changed) is True


def test_a_smaller_rebuild_removes_the_inputs_the_bigger_one_left(tmp_path):
    """Without this an agent dutifully answers a batch that no longer exists."""
    shard = tmp_path / "shard-01"
    shard.mkdir()
    for n in (1, 2, 3):
        (shard / f"{n:03d}.in.json").write_text("{}", encoding="utf-8")
    planned = pack([chunk("KIP-1", 0, 200)], shard=0, batch_size=20)
    stale = remove_stale_files(tmp_path, planned)
    assert stale["removed_inputs"] == ["shard-01/002.in.json", "shard-01/003.in.json"]


def test_an_orphaned_output_is_moved_aside_not_deleted_and_not_left(tmp_path):
    """Left where it is, merge reads `009.out.json` as an answer to whatever `009.in.json`
    now contains. Deleted, an agent's work is gone. `stale/` is neither."""
    shard = tmp_path / "shard-01"
    shard.mkdir()
    (shard / "001.in.json").write_text("{}", encoding="utf-8")
    (shard / "001.out.json").write_text('{"keep": true}', encoding="utf-8")
    (shard / "009.out.json").write_text('{"orphan": true}', encoding="utf-8")
    planned = pack([chunk("KIP-1", 0, 200)], shard=0, batch_size=20)
    stale = remove_stale_files(tmp_path, planned)
    assert stale["moved_outputs"] == ["shard-01/stale/009.out.json"]
    assert not (shard / "009.out.json").exists()
    assert json.loads((shard / "stale" / "009.out.json").read_text()) == {"orphan": True}
    assert (shard / "001.out.json").exists()  # still a planned batch: untouched


def test_a_shard_with_finished_batches_is_reported_as_in_flight(tmp_path):
    shard = tmp_path / "shard-02"
    shard.mkdir()
    (shard / "status.json").write_text(
        json.dumps({"shard": "shard-02", "done": ["001", "002"], "failed": []}), encoding="utf-8"
    )
    assert shards_in_flight(tmp_path) == {"shard-02": "2 done in status.json"}
    (shard / "status.json").write_text(json.dumps({"done": []}), encoding="utf-8")
    assert shards_in_flight(tmp_path) == {}


def test_a_shard_with_outputs_and_no_status_file_is_in_flight_too(tmp_path):
    """The hole in checking `status.json` alone: an agent whose status file was deleted, or
    which has not written one yet, has done work that a reshard would repoint."""
    shard = tmp_path / "shard-03"
    shard.mkdir()
    (shard / "004.out.json").write_text("{}", encoding="utf-8")
    assert shards_in_flight(tmp_path) == {"shard-03": "1 .out.json on disk"}


# --------------------------------------------------------------------------- manifest


def test_the_manifest_records_what_a_planner_has_to_decide_on(tmp_path):
    from pathlib import Path

    chunks = [chunk(f"KIP-{k}", i, 800) for k in range(1, 5) for i in range(2)]
    planned = plan_batches(
        selection(chunks), shards=2, batch_size=3, generated_at="", schema_sha="abc"
    )
    written = [
        {
            "id": b.batch_id,
            "chunks": len(b.chunks),
            "kip_sections": len(b.chunks),
            "issue_descriptions": 0,
            "chars": sum(c.char_len for c in b.chunks),
            "token_est": sum(c.token_est for c in b.chunks),
            "bytes": 1000,
            "max_line_bytes": 100,
        }
        for b in planned
    ]
    manifest = build_manifest(
        written,
        selection=selection(chunks),
        planned=planned,
        shards=2,
        batch_size=3,
        canonical_dir=Path("data/canonical"),
        schema_path=Path("brain/extract/schema.json"),
        schema_sha="abc",
        generated_at="now",
        stale={"removed_inputs": [], "moved_outputs": []},
        prefix="",
        duration_ms=1,
    )
    assert manifest["totals"]["batches"] == len(planned)
    assert manifest["totals"]["chunks"] == len(chunks)
    assert manifest["schema"]["sha256"] == "abc"
    assert manifest["sharding"]["max_batch_bytes"] == MAX_BATCH_BYTES
    assert set(manifest["sharding"]["batches_per_shard"]) == {"shard-01", "shard-02"}
    assert manifest["sizes"]["over_budget"] == []
    assert manifest["totals"]["batches_per_agent"] == round(len(planned) / 2, 1)


def test_build_and_merge_write_into_one_step_report(tmp_path):
    """Conventions: one `data/reports/<step>.json` per step. Build and merge run days apart
    with agents in between, so the second one must not erase the first one's half."""
    from brain.extract.build import write_section

    write_section(tmp_path, "build", {"totals": {"batches": 12}})
    write_section(tmp_path, "merge", {"planned": {"entities": 40}})
    report = json.loads((tmp_path / "extract.json").read_text(encoding="utf-8"))
    assert report["step"] == "extract"
    assert report["build"]["totals"]["batches"] == 12
    assert report["merge"]["planned"]["entities"] == 40
    assert report["generated_at"]
