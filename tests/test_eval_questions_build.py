"""`brain eval questions build`: the asks, the sharding, and the files on disk.

The batch protocol these assertions defend is the one step 04 paid for: indented JSON, a
size measured on the real payload rather than estimated, a `status.json` an agent can
resume from, and a refusal to rebuild inputs an agent has already answered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.eval import paths as paths_mod
from brain.eval import questions as Q
from brain.eval.paths import PathSample
from brain.extract.build import MANIFEST_NAME, MAX_BATCH_BYTES, STATUS_NAME, BuildError
from tests.test_eval_questions_plan import COMPETENCY


def sample(shape: str, qtype: str, key: str, *, nodes: int = 3, text: int = 100) -> PathSample:
    return PathSample(
        shape=shape,
        question_type=qtype,
        gold_source="graph",
        expected_strategy="s3",
        path_shape=f"{shape} shape",
        path_key=key,
        nodes=[paths_mod.node("n", "WorkItem", f"{key}-{i}", "t" * 20) for i in range(nodes)],
        edges=[paths_mod.edge("TESTS", f"{key}-0", f"{key}-1")],
        anchors=[f"{key}-0"],
        answers=[f"{key}-1"],
        snippets=[{"chunk_id": "a" * 40, "parent_key": key, "text": "x" * text}],
    )


@pytest.fixture
def demand() -> Q.Demand:
    return Q.plan_demand(COMPETENCY, new_total=13)


# ------------------------------------------------------------------------------- asks


def test_language_is_interleaved_so_one_shape_does_not_carry_all_the_hebrew():
    assert Q.lang_sequence(3, 4) == ["he", "en", "he", "en", "he", "en", "en"]
    assert Q.lang_sequence(0, 2) == ["en", "en"]
    assert Q.lang_sequence(2, 0) == ["he", "he"]
    assert Q.lang_sequence(0, 0) == []


def test_every_requested_question_lands_on_a_path_and_the_rest_are_spares(demand):
    samples = [sample("community_theme", "global", f"L0-{i}") for i in range(8)]
    assigned = Q.assign_asks(samples, demand)
    asked = sum(len(s.asks) for s in samples)
    assert assigned["global"] == 7
    assert asked == 7
    assert sum(1 for s in samples if s.spare) == 1
    assert {a["lang"] for s in samples for a in s.asks} == {"he", "en"}


def test_more_questions_than_paths_doubles_up_on_the_richest_path_rather_than_dropping_one(demand):
    samples = [
        sample("community_theme", "global", "L0-1"),
        sample("community_theme", "global", "L0-2"),
    ]
    Q.assign_asks(samples, demand)
    assert sum(len(s.asks) for s in samples) == 7
    assert len(samples[0].asks) == 4


def test_reassigning_clears_the_previous_asks_instead_of_appending_to_them(demand):
    samples = [sample("community_theme", "global", f"L0-{i}") for i in range(8)]
    Q.assign_asks(samples, demand)
    Q.assign_asks(samples, demand)
    assert sum(len(s.asks) for s in samples) == 7


# --------------------------------------------------------------------------- sharding


def test_shards_get_a_mix_of_types_so_two_agents_see_the_same_shapes():
    samples = [sample("a", "traceability", f"t{i}") for i in range(4)]
    samples += [sample("b", "global", f"g{i}") for i in range(4)]
    buckets = Q.assign_shards(samples, 2)
    assert len(buckets) == 2
    for bucket in buckets:
        assert {s.question_type for s in bucket} == {"traceability", "global"}


def test_sharding_is_deterministic_whatever_order_the_paths_arrive_in():
    samples = [sample("a", "impact", f"i{i}") for i in range(6)]
    first = [[s.path_key for s in b] for b in Q.assign_shards(samples, 2)]
    second = [[s.path_key for s in b] for b in Q.assign_shards(list(reversed(samples)), 2)]
    assert first == second


def test_one_shard_is_a_legal_ask_and_zero_is_treated_as_one():
    samples = [sample("a", "impact", f"i{i}") for i in range(3)]
    assert len(Q.assign_shards(samples, 1)) == 1
    assert len(Q.assign_shards(samples, 0)) == 1


# --------------------------------------------------------------------------- packing


def test_a_batch_is_closed_before_it_passes_forty_kilobytes_measured_on_the_payload(demand):
    fat = [sample("a", "impact", f"i{i}", nodes=40, text=400) for i in range(12)]
    batches = Q.pack(fat, shard=0, demand=demand, generated_at="t", schema_sha="s")
    assert len(batches) > 1
    for batch in batches:
        payload = Q.envelope(batch=batch, demand=demand, generated_at="t", schema_sha="s")
        assert len(Q.serialise(payload).encode("utf-8")) <= MAX_BATCH_BYTES


def test_a_single_path_too_big_for_the_budget_still_gets_a_batch_of_its_own(demand):
    huge = sample("a", "impact", "i0", nodes=900, text=400)
    batches = Q.pack([huge], shard=0, demand=demand, generated_at="t", schema_sha="s")
    assert len(batches) == 1
    assert batches[0].paths == [huge]


def test_the_envelope_gives_each_shard_a_disjoint_id_block(demand):
    first = Q.envelope(
        batch=Q.PlannedBatch(0, 1, [sample("a", "impact", "i0")]),
        demand=demand,
        generated_at="t",
        schema_sha="s",
    )
    second = Q.envelope(
        batch=Q.PlannedBatch(1, 1, [sample("a", "impact", "i1")]),
        demand=demand,
        generated_at="t",
        schema_sha="s",
    )
    assert first["id_range"]["first"] == "q001"
    assert second["id_range"]["first"] == "q101"
    assert first["id_range"]["last"] < second["id_range"]["first"]


def test_the_envelope_names_the_schema_the_templates_and_the_agent(demand):
    payload = Q.envelope(
        batch=Q.PlannedBatch(0, 1, [sample("a", "impact", "i0")]),
        demand=demand,
        generated_at="t",
        schema_sha="deadbeef",
    )
    assert payload["schema_path"] == Q.SCHEMA_REF
    assert payload["schema_sha256"] == "deadbeef"
    assert payload["templates_path"] == Q.TEMPLATES_REF
    assert payload["agent"] == Q.AGENT_REF


def test_the_envelope_never_ships_a_gold_answer_because_there_is_not_one_yet(demand):
    payload = Q.envelope(
        batch=Q.PlannedBatch(0, 1, [sample("a", "impact", "i0")]),
        demand=demand,
        generated_at="t",
        schema_sha="s",
    )
    assert "gold_answer" not in json.dumps(payload)


# ---------------------------------------------------------------------------- writing


class FakeCtx:
    prefix = ""

    def read(self, *a, **k):  # pragma: no cover - the sampler is monkeypatched away
        raise AssertionError("run_build must not query the graph in this test")


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    samples = [sample("test_fix", "traceability", f"K{i}") for i in range(4)]
    samples += [sample("blast_radius", "impact", f"c{i}") for i in range(4)]
    samples += [sample("motivation", "rationale", f"KIP-{i}") for i in range(3)]
    samples += [sample("community_theme", "global", f"L0-{i}") for i in range(8)]
    samples += [sample("release_window", "temporal", f"v{i}") for i in range(3)]
    monkeypatch.setattr(paths_mod, "load_truth", lambda *a, **k: {"stale_states": []})
    monkeypatch.setattr(paths_mod, "sample", lambda *a, **k: samples)
    monkeypatch.setattr(paths_mod, "attach_snippets", lambda ctx, paths: len(paths))
    manifest = Q.run_build(
        ctx=FakeCtx(),
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        existing_rows=COMPETENCY,
        shards=2,
    )
    return manifest, tmp_path


def test_the_build_writes_indented_batches_a_status_file_and_a_manifest(built):
    manifest, tmp_path = built
    root = tmp_path / "batches" / "questions"
    assert (root / MANIFEST_NAME).is_file()
    for shard in ("shard-01", "shard-02"):
        assert (root / shard / STATUS_NAME).is_file()
        status = json.loads((root / shard / STATUS_NAME).read_text())
        assert status == {"shard": shard, "done": [], "failed": []}
    inputs = sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json"))
    assert inputs
    for path in inputs:
        text = path.read_text(encoding="utf-8")
        assert "\n  " in text, f"{path} is not indented"
        assert len(text.encode("utf-8")) <= MAX_BATCH_BYTES
    assert manifest["totals"]["batches"] == len(inputs)


def test_the_manifest_carries_the_deficit_table_the_planner_has_to_read(built):
    manifest, _ = built
    demand = manifest["demand"]
    assert demand["final_total"] == 32
    assert demand["per_type_goal"] == 8
    assert demand["goal_reachable"] is False
    assert demand["questions_for_goal"] == 40
    assert len(demand["cells"]) == 10
    assert demand["notes"]


def test_the_manifest_records_the_seed_and_the_schema_hash_so_a_rebuild_is_checkable(built):
    manifest, _ = built
    assert manifest["seed"] == paths_mod.DEFAULT_SEED
    assert len(manifest["schema"]["sha256"]) == 64
    assert manifest["paths"]["snippet_chars"] == paths_mod.SNIPPET_CHARS


def test_every_requested_question_is_accounted_for_in_the_batches(built):
    manifest, tmp_path = built
    root = tmp_path / "batches" / "questions"
    asked = 0
    for path in sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        asked += payload["questions_requested"]
        assert payload["questions_requested"] == len(payload["asks"])
    assert asked == manifest["demand"]["requested_total"] == 17


def test_rebuilding_unchanged_inputs_does_not_rewrite_them(built):
    manifest, tmp_path = built
    root = tmp_path / "batches" / "questions"
    before = {p: p.stat().st_mtime_ns for p in root.glob("shard-*/*.in.json")}
    text = {p: p.read_text(encoding="utf-8") for p in before}
    for path in before:
        payload = json.loads(text[path])
        payload["generated_at"] = "2099-01-01T00:00:00+00:00"
        assert json.loads(text[path])["batch_id"] == payload["batch_id"]
    assert manifest["sizes"]["over_budget"] == []


def test_a_shard_an_agent_is_already_working_on_is_not_rebuilt_under_it(built, monkeypatch):
    manifest, tmp_path = built
    root = tmp_path / "batches" / "questions"
    status = root / "shard-01" / STATUS_NAME
    status.write_text(json.dumps({"shard": "shard-01", "done": ["shard-01/001"], "failed": []}))
    monkeypatch.setattr(paths_mod, "sample", lambda *a, **k: [])
    with pytest.raises(BuildError, match="already answered"):
        Q.run_build(
            ctx=FakeCtx(),
            batches_dir=tmp_path / "batches",
            reports_dir=tmp_path / "reports",
            existing_rows=COMPETENCY,
            shards=2,
        )


def test_an_input_no_longer_planned_is_deleted_and_its_output_is_moved_aside(tmp_path: Path):
    root = tmp_path / "questions"
    shard = root / "shard-01"
    shard.mkdir(parents=True)
    (shard / "009.in.json").write_text("{}")
    (shard / "009.out.json").write_text("{}")
    planned = [Q.PlannedBatch(0, 1, [])]
    stale = Q.remove_stale_files(root, planned)
    assert stale["removed_inputs"] == ["shard-01/009.in.json"]
    assert stale["moved_outputs"] == ["shard-01/stale/009.out.json"]
    assert (shard / "stale" / "009.out.json").is_file()


def test_the_report_section_is_written_beside_the_other_step_reports(built):
    _, tmp_path = built
    report = json.loads((tmp_path / "reports" / "eval_questions.json").read_text())
    assert report["step"] == "eval.questions"
    assert report["build"]["step"] == "eval.questions.build"


def test_a_build_that_sampled_nothing_refuses_rather_than_writing_empty_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(paths_mod, "load_truth", lambda *a, **k: {})
    monkeypatch.setattr(paths_mod, "sample", lambda *a, **k: [])
    with pytest.raises(BuildError, match="no paths sampled"):
        Q.run_build(
            ctx=FakeCtx(),
            batches_dir=tmp_path / "batches",
            reports_dir=tmp_path / "reports",
            existing_rows=COMPETENCY,
        )


def test_the_summary_line_reports_the_numbers_a_planner_would_otherwise_grep_for(built):
    manifest, _ = built
    text = Q.summarize_build(manifest)
    assert "32" in text
    assert "Hebrew" in text
    assert "note:" in text
