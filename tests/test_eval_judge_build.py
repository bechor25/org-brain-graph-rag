"""The blind judge batches: what the judge sees, and everything it must not.

Three properties are load-bearing and each has a test that fails loudly if it stops holding:
a batch carries no strategy name and no question id; the label → strategy map is written
outside the directory the judge reads; and the pairwise A/B order is drawn per case rather
than being "baseline first, always".
"""

from __future__ import annotations

import json

import pytest

from brain.eval import blind as blind_mod
from brain.eval import judge_batches as jb


def answer(
    qid="q001", strategy="s1", *, text="It was [KAFKA-1].", available=True, cited=("KAFKA-1",)
):
    return {
        "case_id": f"{qid}.{strategy}",
        "qid": qid,
        "strategy": strategy,
        "mode": "fixed",
        "question": f"who broke build number {int(qid[1:])}?",
        "lang": "en",
        "type": "traceability",
        "answer": text,
        "cited_keys": list(cited),
        "cited_keys_valid": list(cited),
        "cited_keys_invalid": [],
        "confidence": "high",
        "refused": False,
        "context_items": 1,
        "context_tokens": 100,
        "context_sha256": "deadbeef",
        "context_keys": list(cited),
        "context_available": available,
    }


def row(qid="q001", qtype="traceability", lang="en"):
    return {
        "id": qid,
        "type": qtype,
        "lang": lang,
        "question": f"who broke build number {int(qid[1:])}?",
        "gold_answer": "KAFKA-1 did.",
        "gold_evidence": ["KAFKA-1"],
    }


def context(key="KAFKA-1"):
    return [{"kind": "WorkItem", "key": key, "title": "t", "snippet": "s"}]


def build(tmp_path, answers, rows, **kwargs):
    contexts = {a["case_id"]: context() for a in answers if a["context_available"]}
    contexts.update({a["case_id"]: [] for a in answers if not a["context_available"]})
    return jb.run_build(
        answers=answers,
        rows=rows,
        contexts=contexts,
        batches_dir=tmp_path / "batches",
        eval_dir=tmp_path / "eval",
        reports_dir=tmp_path / "reports",
        echo=lambda _: None,
        **kwargs,
    )


def batch_payloads(tmp_path):
    root = tmp_path / "batches" / "judge"
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("shard-*/*.in.json"))
    ]


# ----------------------------------------------------------------------------- blindness


def test_no_batch_names_a_strategy_or_a_question(tmp_path):
    answers = [answer("q001", s) for s in ("s1r", "s3")] + [answer("q002", "s1r")]
    build(tmp_path, answers, [row("q001"), row("q002")])
    blind = jb.read_blind_map(tmp_path / "eval")
    for payload in batch_payloads(tmp_path):
        assert blind_mod.leaks(payload, blind.secrets) == []
    text = json.dumps(batch_payloads(tmp_path), ensure_ascii=False)
    for secret in ("q001", "q002", '"s1r"', '"s3"'):
        assert secret not in text


def test_the_blind_map_lives_outside_the_directory_the_judge_reads(tmp_path):
    manifest = build(tmp_path, [answer()], [row()])
    map_path = tmp_path / "eval" / blind_mod.BLIND_MAP_NAME
    assert map_path.is_file()
    assert manifest["blind"]["map"] == str(map_path)
    assert not list((tmp_path / "batches" / "judge").rglob(blind_mod.BLIND_MAP_NAME))
    assert blind_mod.map_is_outside(map_path, tmp_path / "batches")


def test_a_map_inside_the_batch_tree_is_refused(tmp_path):
    with pytest.raises(jb.JudgeError, match="inside the batch tree"):
        jb.run_build(
            answers=[answer()],
            rows=[row()],
            contexts={"q001.s1": context()},
            batches_dir=tmp_path / "batches",
            eval_dir=tmp_path / "batches" / "judge",
            reports_dir=tmp_path / "reports",
            echo=lambda _: None,
        )


def test_the_leak_check_fires_on_a_field_that_names_the_strategy():
    payload = {"cases": [{"case_id": "abc", "strategy": "s3"}]}
    found = blind_mod.leaks(payload, {"s3", "q001"})
    assert any("`strategy`" in f for f in found)
    assert any("'s3'" in f for f in found)


def test_the_leak_check_does_not_fire_on_prose_that_mentions_s3():
    payload = {"cases": [{"case_id": "abc", "answer": "the S3 sink connector, see s3 docs"}]}
    assert blind_mod.leaks(payload, {"s3"}) == []


def test_labels_are_reproducible_and_distinct():
    first = [next(s) for s in [blind_mod.labels(7)] * 1 for _ in range(50)]
    second = [next(s) for s in [blind_mod.labels(7)] * 1 for _ in range(50)]
    assert first == second
    assert len(set(first)) == 50


# ----------------------------------------------------------------------------- the cases


def test_a_single_case_carries_the_gold_the_context_and_the_answer(tmp_path):
    build(tmp_path, [answer()], [row()])
    case = batch_payloads(tmp_path)[0]["cases"][0]
    assert case["kind"] == "single"
    assert case["gold_answer"] == "KAFKA-1 did."
    assert case["gold_evidence"] == ["KAFKA-1"]
    assert case["context_items"] == context()
    assert case["context_available"] is True
    assert case["answer"] == "It was [KAFKA-1]."
    assert set(case) == {
        "case_id",
        "kind",
        "question",
        "lang",
        "question_type",
        "gold_answer",
        "gold_evidence",
        "context_available",
        "context_items",
        "answer",
        "cited_keys",
    }


def test_an_agentic_case_declares_that_there_is_no_context_to_be_faithful_to(tmp_path):
    build(tmp_path, [answer("q001", jb.AGENTIC, available=False)], [row()])
    case = batch_payloads(tmp_path)[0]["cases"][0]
    assert case["context_available"] is False
    assert case["context_items"] == []


def test_pairwise_cases_pair_the_baseline_with_each_challenger(tmp_path):
    answers = [answer("q001", s) for s in ("s1r", "s2", "s3")]
    manifest = build(tmp_path, answers, [row()], pair_with=("s2", "s3"))
    blind = jb.read_blind_map(tmp_path / "eval")
    assert manifest["totals"]["pairs"] == 2
    assert {p["challenger"] for p in blind.pairs.values()} == {"s2", "s3"}
    for entry in blind.pairs.values():
        sides = {entry["a"]["strategy"], entry["b"]["strategy"]}
        assert jb.BASELINE in sides
        assert entry[entry["baseline_side"]]["strategy"] == jb.BASELINE


def test_the_pairwise_order_is_not_always_the_same_side(tmp_path):
    answers = [answer(f"q{i:03d}", s) for i in range(12) for s in ("s1r", "s3")]
    rows = [row(f"q{i:03d}") for i in range(12)]
    build(tmp_path, answers, rows, pair_with=("s3",))
    sides = [e["baseline_side"] for e in jb.read_blind_map(tmp_path / "eval").pairs.values()]
    assert set(sides) == {"a", "b"}, "a fixed order would let a judge learn the position"


def test_a_question_with_no_baseline_answer_gets_no_pair_and_says_why(tmp_path):
    manifest = build(tmp_path, [answer("q001", "s3")], [row()], pair_with=("s3",))
    assert manifest["totals"]["pairs"] == 0
    assert any("no s1r answer" in s["why"] for s in manifest["skipped"])


def test_pairwise_cases_carry_no_context_unless_asked(tmp_path):
    answers = [answer("q001", s) for s in ("s1r", "s3")]
    build(tmp_path, answers, [row()], pair_with=("s3",))
    pair = [c for p in batch_payloads(tmp_path) for c in p["pairwise"]][0]
    assert set(pair["a"]) == {"answer", "cited_keys"}

    build(tmp_path, answers, [row()], pair_with=("s3",), pair_context=True, force=True)
    pair = [c for p in batch_payloads(tmp_path) for c in p["pairwise"]][0]
    assert pair["a"]["context_items"] == context()


# ----------------------------------------------------------------------------- the shards


def test_twenty_percent_of_the_cases_are_judged_twice(tmp_path):
    answers = [answer(f"q{i:03d}", "s1") for i in range(10)]
    rows = [row(f"q{i:03d}") for i in range(10)]
    manifest = build(tmp_path, answers, rows, shards=2, overlap=0.2, pair_with=())
    assert manifest["sharding"]["overlap_cases"] == 2
    assert manifest["totals"]["judgments_expected"] == 12
    blind = jb.read_blind_map(tmp_path / "eval")
    doubled = [e for e in blind.cases.values() if e.get("overlap")]
    assert len(doubled) == 2
    assert all(len(e["shards"]) == 2 for e in doubled)


def test_an_overlapped_case_keeps_its_label_in_both_shards(tmp_path):
    answers = [answer(f"q{i:03d}", "s1") for i in range(10)]
    rows = [row(f"q{i:03d}") for i in range(10)]
    build(tmp_path, answers, rows, shards=2, overlap=0.2, pair_with=())
    seen: dict[str, set[str]] = {}
    for payload in batch_payloads(tmp_path):
        for case in payload["cases"]:
            seen.setdefault(case["case_id"], set()).add(payload["shard"])
    twice = {label for label, shards in seen.items() if len(shards) == 2}
    assert len(twice) == 2, "the same label must reach both judges, or agreement is unmeasurable"


def test_a_batch_never_carries_two_cases_of_one_question(tmp_path):
    answers = [answer("q001", s) for s in ("s1", "s1r", "s2", "s3")]
    build(tmp_path, answers, [row()], shards=1, pair_with=("s2", "s3"))
    blind = jb.read_blind_map(tmp_path / "eval")
    by_label = {**blind.cases, **blind.pairs}
    for payload in batch_payloads(tmp_path):
        labels = [c["case_id"] for c in payload["cases"]] + [
            c["pair_id"] for c in payload["pairwise"]
        ]
        qids = [by_label[label]["qid"] for label in labels]
        assert len(qids) == len(set(qids)), payload["batch_id"]


def test_every_batch_is_inside_the_budget(tmp_path):
    big = "x " * 6000
    answers = [answer(f"q{i:03d}", "s1", text=f"[KAFKA-1] {big}") for i in range(6)]
    rows = [row(f"q{i:03d}") for i in range(6)]
    manifest = build(tmp_path, answers, rows, pair_with=())
    assert manifest["sizes"]["over_budget"] == []


def test_the_build_is_reproducible_under_one_seed(tmp_path):
    answers = [answer(f"q{i:03d}", s) for i in range(4) for s in ("s1r", "s3")]
    rows = [row(f"q{i:03d}") for i in range(4)]
    first = build(tmp_path, answers, rows, pair_with=("s3",))
    second = build(tmp_path, answers, rows, pair_with=("s3",), force=True)
    assert [b["sha256"] for b in first["batches"]] == [b["sha256"] for b in second["batches"]]


def test_a_rebuild_over_a_working_judge_is_refused(tmp_path):
    build(tmp_path, [answer()], [row()])
    shard = tmp_path / "batches" / "judge" / "shard-01"
    (shard / "001.out.json").write_text('{"batch_id": "shard-01/001"}', encoding="utf-8")
    with pytest.raises(jb.JudgeError, match="refusing to rebuild"):
        build(tmp_path, [answer()], [row()])


def test_an_answer_with_no_question_row_is_skipped_with_a_reason(tmp_path):
    manifest = build(tmp_path, [answer("q001"), answer("q404")], [row("q001")])
    assert manifest["totals"]["singles"] == 1
    assert "no question row" in manifest["skipped"][0]["why"]


def test_contexts_for_reports_an_answer_whose_run_file_moved(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "q001.s1.json").write_text(
        json.dumps(
            {
                "qid": "q001",
                "strategy": "s1",
                "status": "ok",
                "result": {"items": [{"kind": "WorkItem", "key": "KAFKA-1", "snippet": "new"}]},
            }
        ),
        encoding="utf-8",
    )
    contexts, drift = jb.contexts_for([answer()], runs)
    assert contexts == {}
    assert drift[0]["why"] == "the run file now packs another context"
