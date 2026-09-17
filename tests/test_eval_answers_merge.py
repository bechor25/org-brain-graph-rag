"""Merging mode-A answers: what is rejected, what is kept and marked, what is carried in.

The rule this file exists to pin down is the asymmetry in the contract. A batch whose
*envelope* is wrong is not trusted at all and goes to `retry/`; one bad *answer* costs that
answer; and a bad *citation* costs nothing at all — it is kept, marked invalid, and counted,
because "this retrieval's answers cite keys it never returned" is a measurement.
"""

from __future__ import annotations

import json

import pytest

from brain.eval import answers_batches as ab
from tests.test_eval_answers_batches import rows, write_runs

CHUNK = f"{1:040x}"


def build(tmp_path, qids=("q001",), strategies=("s1",), **kwargs):
    runs_dir = write_runs(tmp_path, list(qids), list(strategies), **kwargs)
    ab.run_build(
        rows=rows(list(qids)),
        strategies=tuple(strategies),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=1,
        echo=lambda _: None,
    )
    return runs_dir


def write_output(tmp_path, batch="001", *, answers, shard="shard-01", extra=None):
    payload = {"batch_id": f"{shard}/{batch}", "answers": answers}
    payload.update(extra or {})
    path = tmp_path / "batches" / "answers" / shard / f"{batch}.out.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def answer(case_id="q001.s1", *, text=None, cited=("KAFKA-1",), confidence="high", **extra):
    body = text if text is not None else "The fix landed in [KAFKA-1]."
    out = {
        "case_id": case_id,
        "answer": body,
        "cited_keys": list(cited),
        "confidence": confidence,
    }
    out.update(extra)
    return out


def merge(tmp_path, **kwargs):
    return ab.run_merge(
        batches_dir=tmp_path / "batches",
        eval_dir=tmp_path / "eval",
        reports_dir=tmp_path / "reports",
        echo=lambda _: None,
        **kwargs,
    )


# ----------------------------------------------------------------------------- the happy path


def test_an_accepted_answer_lands_in_its_own_file_with_the_context_it_answered(tmp_path):
    build(tmp_path)
    write_output(tmp_path, answers=[answer()])
    report = merge(tmp_path)

    assert report["answers"]["accepted"] == 1
    assert report["complete"] is True
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").read_text(encoding="utf-8")
    )
    assert record["qid"] == "q001"
    assert record["strategy"] == "s1"
    assert record["cited_keys_valid"] == ["KAFKA-1"]
    assert record["cited_keys_invalid"] == []
    assert record["citation_problems"] == []
    assert record["context_available"] is True
    assert "KAFKA-1" in record["context_keys"]


def test_a_refusal_is_an_answer_and_is_recorded_as_one(tmp_path):
    build(tmp_path)
    write_output(
        tmp_path,
        answers=[
            answer(
                text="The context does not name a test for this work item.",
                cited=(),
                confidence="none",
                unanswerable_reason="no item names a test",
            )
        ],
    )
    report = merge(tmp_path)
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").read_text(encoding="utf-8")
    )
    assert record["refused"] is True
    assert record["unanswerable_reason"] == "no item names a test"
    assert report["answers"]["by_strategy"]["s1"]["refused"] == 1


# ----------------------------------------------------------------------------- citations


def test_a_citation_that_is_not_in_the_context_is_kept_and_marked(tmp_path):
    build(tmp_path)
    write_output(
        tmp_path,
        answers=[answer(text="It was [KAFKA-9].", cited=("KAFKA-9",))],
    )
    report = merge(tmp_path)
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").read_text(encoding="utf-8")
    )
    assert report["answers"]["accepted"] == 1, "the answer is kept, not dropped"
    assert record["cited_keys_invalid"] == [
        {"cited": "KAFKA-9", "reason": "not in this case's context"}
    ]
    assert record["cited_keys_valid"] == []
    assert report["answers"]["by_strategy"]["s1"]["with_invalid_citations"] == 1
    assert report["answers"]["by_strategy"]["s1"]["citation_in_context_pct"] == 0.0


def test_a_truncated_chunk_citation_resolves_against_the_context(tmp_path):
    build(tmp_path)
    write_output(
        tmp_path,
        answers=[answer(text=f"See [chunk:{CHUNK[:12]}].", cited=(f"chunk:{CHUNK[:12]}",))],
    )
    merge(tmp_path)
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").read_text(encoding="utf-8")
    )
    assert record["cited_keys_valid"] == [CHUNK]
    assert record["citation_problems"] == []


def test_a_key_declared_but_never_bracketed_is_reported(tmp_path):
    build(tmp_path)
    write_output(tmp_path, answers=[answer(text="It was fixed.", cited=("KAFKA-1",))])
    merge(tmp_path)
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").read_text(encoding="utf-8")
    )
    assert any("declared but not bracketed" in p for p in record["citation_problems"])


# ----------------------------------------------------------------------------- refusals


def test_a_bad_envelope_sends_the_whole_batch_to_retry(tmp_path):
    build(tmp_path)
    path = write_output(tmp_path, answers=[answer()])
    path.write_text(json.dumps({"batch_id": "shard-01/001"}), encoding="utf-8")  # no `answers`
    report = merge(tmp_path)
    assert report["answers"]["accepted"] == 0
    assert report["batches"]["failed"][0]["where"] == "retry"
    assert not path.exists(), "the unusable output is moved, so the agent rewrites it"
    assert (tmp_path / "batches" / "answers" / "shard-01" / "retry" / "001.out.json").is_file()


def test_the_third_bad_attempt_is_quarantined(tmp_path):
    build(tmp_path)
    for _ in range(3):
        path = write_output(tmp_path, answers=[answer()])
        previous = tmp_path / "batches" / "answers" / "shard-01" / "retry" / "001.out.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("answers")
        if previous.is_file():
            payload[ab.RETRY_FIELD] = json.loads(previous.read_text(encoding="utf-8"))[
                ab.RETRY_FIELD
            ]
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = merge(tmp_path)
    assert report["batches"]["failed"][0]["where"] == "quarantine"


def test_one_bad_answer_costs_that_answer_and_not_the_batch(tmp_path):
    build(tmp_path, qids=("q001", "q002"))
    # Two questions, one strategy: both cases fit one batch, which is what makes this the
    # right place to prove that a bad answer does not take its neighbour down with it.
    write_output(
        tmp_path, answers=[answer(confidence="certain"), answer("q002.s1")]
    )  # `certain` is not in the enum
    report = merge(tmp_path)
    assert report["answers"]["accepted"] == 1
    assert report["answers"]["rejected"] == 1
    assert "not one of" in report["rejected"][0]["why"][0]
    assert (tmp_path / "eval" / "answers" / "fixed" / "q002.s1.json").is_file()
    assert not (tmp_path / "eval" / "answers" / "fixed" / "q001.s1.json").exists()


def test_an_answer_to_a_case_that_is_not_in_the_batch_is_rejected(tmp_path):
    build(tmp_path)
    write_output(tmp_path, answers=[answer(), answer("q999.s9")])
    report = merge(tmp_path)
    assert report["answers"]["accepted"] == 1
    assert "is not a case of this batch" in report["rejected"][0]["why"][0]


def test_an_unanswered_case_is_named(tmp_path):
    build(tmp_path, qids=("q001", "q002"))
    write_output(tmp_path, answers=[answer()])
    report = merge(tmp_path)
    assert report["answers"]["unanswered_cases"] == ["q002.s1"]
    assert report["complete"] is False, "a case nobody answered is not a complete merge"


# ----------------------------------------------------------------------------- mode B


def test_a_plan2_answer_is_carried_in_as_an_agentic_case(tmp_path):
    build(tmp_path)
    write_output(tmp_path, answers=[answer()])
    plan2 = tmp_path / "plan2"
    plan2.mkdir()
    (plan2 / "cq01.md").write_text(
        "---\nquestion_id: cq01\nlang: en\nstarted_at: 2026-09-17T10:00:00+00:00\n"
        "finished_at: 2026-09-17T10:00:42+00:00\n---\n\n"
        "Three tests cover it [KAFKA-14649].\n\nstrategy: route, lookup\n",
        encoding="utf-8",
    )
    report = merge(tmp_path, plan2_dir=plan2)

    assert report["agentic"]["answers"] == 1
    record = json.loads(
        (tmp_path / "eval" / "answers" / "fixed" / "cq01.agentic.json").read_text(encoding="utf-8")
    )
    assert record["strategy"] == ab.AGENTIC
    assert record["context_available"] is False, "mode B recorded no context to be faithful to"
    assert record["cited_keys"] == ["KAFKA-14649"]
    assert record["tools"] == ("route", "lookup") or record["tools"] == ["route", "lookup"]


def test_read_merged_returns_both_modes(tmp_path):
    build(tmp_path)
    write_output(tmp_path, answers=[answer()])
    plan2 = tmp_path / "plan2"
    plan2.mkdir()
    (plan2 / "cq01.md").write_text(
        "---\nquestion_id: cq01\nlang: en\nstarted_at: 2026-09-17T10:00:00+00:00\n"
        "finished_at: 2026-09-17T10:00:42+00:00\n---\n\nX [KAFKA-1].\n\nstrategy: lookup\n",
        encoding="utf-8",
    )
    merge(tmp_path, plan2_dir=plan2)
    strategies = {a["strategy"] for a in ab.read_merged(tmp_path / "eval")}
    assert strategies == {"s1", ab.AGENTIC}


# ----------------------------------------------------------------------------- drift


def test_an_answer_written_against_a_context_the_run_no_longer_holds_is_reported(tmp_path):
    runs_dir = build(tmp_path)
    write_output(tmp_path, answers=[answer()])
    record = json.loads((runs_dir / "q001.s1.json").read_text(encoding="utf-8"))
    record["result"]["items"][0]["snippet"] = "something else entirely"
    (runs_dir / "q001.s1.json").write_text(json.dumps(record), encoding="utf-8")

    report = merge(tmp_path, runs_dir=runs_dir)
    assert report["context_drift"][0]["case_id"] == "q001.s1"
    assert "different context" in report["context_drift"][0]["why"]


def test_no_drift_when_nothing_moved(tmp_path):
    runs_dir = build(tmp_path)
    write_output(tmp_path, answers=[answer()])
    assert merge(tmp_path, runs_dir=runs_dir)["context_drift"] == []


@pytest.mark.parametrize(
    "case_id,expected", [("q001.s1", ("q001", "s1")), ("cq01.agentic", ("cq01", "agentic"))]
)
def test_split_case_id(case_id, expected):
    assert ab.split_case_id(case_id) == expected
