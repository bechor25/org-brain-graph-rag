"""De-blinding the judgments: the four metrics, the win rates, the agreement, the crosscheck.

The fixture builds a real judge batch tree from fake answers, writes fake `.out.json` files
against the labels it produced, and merges. Doing it end to end rather than calling the
scoring functions directly is deliberate: the property most worth protecting is that a label
written by `judge build` is the one `judge merge` resolves, and a unit test of the averaging
would not notice if that stopped being true.
"""

from __future__ import annotations

import json

import pytest

from brain.eval import answers_batches as ab
from brain.eval import casebatch
from brain.eval import judge_batches as jb
from brain.eval import judge_report as jr
from tests.test_eval_judge_build import answer, build, row

QUOTE = 'the answer says "It was" and the context agrees'


def setup(tmp_path, answers, rows, **kwargs):
    """Build the batches, and also put the merged answers where `judge merge` reads them."""
    manifest = build(tmp_path, answers, rows, **kwargs)
    out = tmp_path / "eval" / ab.ANSWERS_DIRNAME / ab.MODE
    for record in answers:
        casebatch.write_pretty(out / f"{record['qid']}.{record['strategy']}.json", record)
    return manifest, jb.read_blind_map(tmp_path / "eval")


def score(label, *, faith=2, correct=2, citation=2, relevancy=2, claims=(), justification=QUOTE):
    return {
        "case_id": label,
        "faithfulness": faith,
        "correctness": correct,
        "citation_validity": citation,
        "relevancy": relevancy,
        "unsupported_claims": list(claims),
        "justification": justification,
    }


def write_judgments(tmp_path, shard, batch, *, scores=(), pairs=()):
    payload = {
        "batch_id": f"{shard}/{batch}",
        "scores": list(scores),
        "pairs": list(pairs),
    }
    path = tmp_path / "batches" / "judge" / shard / f"{batch}.out.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def answer_all(tmp_path, blind, **kwargs):
    """One judgment for every case in every batch, so the merge has a full set."""
    root = tmp_path / "batches" / "judge"
    for in_path in sorted(root.glob("shard-*/*.in.json")):
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        write_judgments(
            tmp_path,
            payload["shard"],
            in_path.name.split(".", 1)[0],
            scores=[score(c["case_id"], **kwargs) for c in payload["cases"]],
            pairs=[
                {"pair_id": c["pair_id"], "winner": "a", "justification": QUOTE}
                for c in payload["pairwise"]
            ],
        )


def merge(tmp_path, rows, **kwargs):
    return jr.run_merge(
        batches_dir=tmp_path / "batches",
        eval_dir=tmp_path / "eval",
        reports_dir=tmp_path / "reports",
        rows=rows,
        echo=lambda _: None,
        **kwargs,
    )


# ----------------------------------------------------------------------------- de-blinding


def test_a_label_is_resolved_back_to_its_strategy_and_question(tmp_path):
    answers = [answer("q001", "s1r"), answer("q001", "s3")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind)
    report = merge(tmp_path, rows)

    assert report["judgments"]["total"] == 2
    strategies = {c["strategy"] for c in report["cases"]}
    assert strategies == {"s1r", "s3"}
    assert all(c["qid"] == "q001" for c in report["cases"])
    assert report["metrics"]["by_strategy"]["s3"]["correctness"] == 2.0


def test_a_judgment_for_a_label_nobody_issued_is_rejected(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    setup(tmp_path, answers, rows, pair_with=())
    write_judgments(tmp_path, "shard-01", "001", scores=[score("deadbeef")])
    report = merge(tmp_path, rows)
    assert report["judgments"]["total"] == 0
    assert "is not a case of this batch" in report["rejected"][0]["why"][0]


def test_the_matrix_is_strategy_by_question_type(tmp_path):
    answers = [answer("q001", "s3"), answer("q002", "s3")]
    rows = [row("q001", qtype="traceability"), row("q002", qtype="impact")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, correct=1)
    report = merge(tmp_path, rows)
    matrix = report["metrics"]["matrix"]
    assert set(matrix["s3"]) == {"traceability", "impact"}
    assert matrix["s3"]["impact"]["correctness"] == 1.0
    assert matrix["s3"]["impact"]["cases"] == 1


# ----------------------------------------------------------------------------- the rules


def test_faithfulness_may_be_null_only_where_no_context_was_recorded(tmp_path):
    answers = [answer("q001", jb.AGENTIC, available=False)]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, faith=None)
    report = merge(tmp_path, rows)
    assert report["judgments"]["problem_count"] == 0
    assert report["metrics"]["by_strategy"]["agentic"]["faithfulness"] is None
    assert report["metrics"]["by_strategy"]["agentic"]["faithfulness_n"] == 0
    assert report["metrics"]["by_strategy"]["agentic"]["correctness"] == 2.0


def test_a_null_metric_on_a_case_that_had_a_context_is_a_recorded_problem(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, correct=None)
    report = merge(tmp_path, rows)
    assert report["judgments"]["problem_count"] == 1
    assert "correctness is null" in report["judgments"]["with_problems"][0]["problems"][0]


def test_faithfulness_scored_where_there_was_no_context_is_a_recorded_problem(tmp_path):
    answers = [answer("q001", jb.AGENTIC, available=False)]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, faith=2)
    report = merge(tmp_path, rows)
    assert any(
        "cannot have seen what it measured" in p
        for p in report["judgments"]["with_problems"][0]["problems"]
    )


def test_a_justification_without_a_quotation_is_reported(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, justification="It is fine, the answer matches the gold answer.")
    report = merge(tmp_path, rows)
    assert "no verbatim quotation" in report["judgments"]["with_problems"][0]["problems"][0]


def test_faithfulness_below_two_needs_a_quoted_unsupported_claim(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, faith=1)
    report = merge(tmp_path, rows)
    assert "no unsupported claim quoted" in report["judgments"]["with_problems"][0]["problems"][0]


@pytest.mark.parametrize(
    "text,expected",
    [
        ('it says "hello" plainly', True),
        ("it says “hello” plainly", True),
        ("it says hello plainly", False),
        ("", False),
    ],
)
def test_has_quote(text, expected):
    assert jr.has_quote(text) is expected


# ----------------------------------------------------------------------------- pairwise


def test_the_winning_side_is_resolved_to_the_strategy_behind_it(tmp_path):
    answers = [answer("q001", "s1r"), answer("q001", "s3")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=("s3",))
    answer_all(tmp_path, blind)  # every pair's winner is side `a`
    report = merge(tmp_path, rows)

    pair_id, entry = next(iter(blind.pairs.items()))
    expected = entry["a"]["strategy"]
    verdict = report["all_pairs"][0]
    assert verdict["winner"] == expected
    table = report["pairwise"]["by_challenger"]["s3"]
    assert table["cases"] == 1
    assert (table["wins"], table["losses"]) == ((1, 0) if expected == "s3" else (0, 1))


def test_win_rates_count_a_tie_as_a_non_win(tmp_path):
    answers = [answer(f"q{i:03d}", s) for i in range(4) for s in ("s1r", "s3")]
    rows = [row(f"q{i:03d}") for i in range(4)]
    _, blind = setup(tmp_path, answers, rows, pair_with=("s3",), overlap=0)
    root = tmp_path / "batches" / "judge"
    for in_path in sorted(root.glob("shard-*/*.in.json")):
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        write_judgments(
            tmp_path,
            payload["shard"],
            in_path.name.split(".", 1)[0],
            scores=[score(c["case_id"]) for c in payload["cases"]],
            pairs=[
                {
                    "pair_id": c["pair_id"],
                    "winner": "tie",
                    "both_wrong": True,
                    "justification": QUOTE,
                }
                for c in payload["pairwise"]
            ],
        )
    report = merge(tmp_path, rows)
    table = report["pairwise"]["by_challenger"]["s3"]
    assert table["wins"] == 0
    assert table["ties"] == table["cases"] == 4
    assert table["both_wrong"] == 4
    assert table["win_rate"] == 0.0
    assert table["win_rate_excluding_ties"] is None


# ----------------------------------------------------------------------------- agreement


def test_the_overlapping_cases_are_compared_between_the_two_judges(tmp_path):
    answers = [answer(f"q{i:03d}", "s1") for i in range(10)]
    rows = [row(f"q{i:03d}") for i in range(10)]
    _, blind = setup(tmp_path, answers, rows, shards=2, overlap=0.2, pair_with=())
    doubled = {label for label, entry in blind.cases.items() if entry.get("overlap")}

    root = tmp_path / "batches" / "judge"
    for in_path in sorted(root.glob("shard-*/*.in.json")):
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        scores = []
        for case in payload["cases"]:
            # The second judge is one point harsher on every case it shares.
            harsher = case["case_id"] in doubled and payload["shard"] == "shard-02"
            scores.append(score(case["case_id"], correct=1 if harsher else 2))
        write_judgments(tmp_path, payload["shard"], in_path.name.split(".", 1)[0], scores=scores)

    report = merge(tmp_path, rows)
    agree = report["agreement"]
    assert agree["overlap_cases"] == 2
    assert agree["by_metric"]["correctness"]["exact_pct"] == 0.0
    assert agree["by_metric"]["correctness"]["within_1_pct"] == 100.0
    assert agree["by_metric"]["relevancy"]["exact_pct"] == 100.0
    # A case judged twice still counts once, with the two scores averaged.
    doubled_rows = [c for c in report["cases"] if c["label"] in doubled]
    assert all(c["judgments"] == 2 for c in doubled_rows)
    assert all(c["correctness"] == 1.5 for c in doubled_rows)
    assert len(report["cases"]) == 10


# ----------------------------------------------------------------------- the code crosscheck


def test_the_code_check_says_a_citation_outside_the_context_is_invalid():
    record = answer("q001", "s1", text="It was [KAFKA-9].", cited=("KAFKA-9",))
    record["context_keys"] = ["KAFKA-1"]
    out = jr.code_citation_check([record])["q001.s1"]
    assert out["code_valid"] is False
    assert out["not_in_context"] == ["KAFKA-9"]


def test_the_code_check_calls_a_refusal_with_no_citations_valid():
    record = answer("q001", "s1", text="Not in context.", cited=())
    record["refused"] = True
    assert jr.code_citation_check([record])["q001.s1"]["code_valid"] is True


def test_the_code_check_uses_the_graph_verifier_when_one_is_given():
    record = answer("q001", "s1")

    class Verdict:
        ok = False

    out = jr.code_citation_check([record], verify=lambda cites: {c.id: Verdict() for c in cites})
    assert out["q001.s1"]["not_in_graph"] == ["KAFKA-1"]
    assert out["q001.s1"]["graph_checked"] is True


def test_judge_and_code_disagreement_is_reported_not_resolved(tmp_path):
    answers = [answer("q001", "s1", text="It was [KAFKA-9].", cited=("KAFKA-9",))]
    answers[0]["context_keys"] = ["KAFKA-1"]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, citation=2)  # the judge says the citations are fine
    report = merge(tmp_path, rows)
    cross = report["citation_crosscheck"]
    assert cross["compared"] == 1
    assert cross["agreed"] == 0
    assert cross["disagreements"][0]["case_id"] == "q001.s1"
    assert cross["disagreements"][0]["code"] == "invalid"


def test_a_judge_score_of_one_is_compatible_with_either_code_verdict(tmp_path):
    answers = [answer("q001", "s1", text="It was [KAFKA-9].", cited=("KAFKA-9",))]
    answers[0]["context_keys"] = ["KAFKA-1"]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind, citation=1)
    assert merge(tmp_path, rows)["citation_crosscheck"]["disagreements"] == []


# ----------------------------------------------------------------------------- the report


def test_the_report_is_written_with_a_sha_and_a_timestamp(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind)
    merge(tmp_path, rows, sha="c0ffee")
    written = json.loads((tmp_path / "reports" / ab.REPORT_NAME).read_text(encoding="utf-8"))
    assert written["judge_merge"]["sha"] == "c0ffee"
    assert written["judge_merge"]["generated_at"]
    assert written["sections"]["metrics"]["sha"] == "c0ffee"
    assert set(written) >= {
        "judge_merge",
        "metrics",
        "pairwise",
        "agreement",
        "citation_crosscheck",
        "judge_build",
    }


def test_a_case_nobody_judged_keeps_the_exit_code_at_one(tmp_path):
    answers = [answer("q001", "s1"), answer("q002", "s1")]
    rows = [row("q001"), row("q002")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    root = tmp_path / "batches" / "judge"
    first = sorted(root.glob("shard-*/*.in.json"))[0]
    payload = json.loads(first.read_text(encoding="utf-8"))
    write_judgments(
        tmp_path,
        payload["shard"],
        first.name.split(".", 1)[0],
        scores=[score(payload["cases"][0]["case_id"])],
    )
    report = merge(tmp_path, rows)
    assert len(report["judgments"]["unjudged_labels"]) == 1
    assert jr.exit_code(report) == 1


def test_merge_without_any_merged_answer_refuses(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    build(tmp_path, answers, rows, pair_with=())
    with pytest.raises(jr.JudgeError, match="no merged answers"):
        merge(tmp_path, rows)


# ----------------------------------------------------------------------------- the sample


@pytest.mark.parametrize(
    "value,expected", [("10%", 0.1), ("0.1", 0.1), ("10", 0.1), ("100%", 1.0), ("1", 0.01)]
)
def test_parse_fraction(value, expected):
    assert jr.parse_fraction(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["0", "-5", "200%", "abc"])
def test_parse_fraction_refuses_nonsense(value):
    with pytest.raises(ValueError):
        jr.parse_fraction(value)


def test_the_sample_is_stratified_across_the_strategies(tmp_path):
    rows = [
        {"strategy": s, "qid": f"q{i:03d}", "label": f"l{i}{s}"}
        for s in ("s1", "s3")
        for i in range(10)
    ]
    picked = jr.pick_sample(rows, fraction=0.2)
    assert len(picked) == 4
    assert {p["strategy"] for p in picked} == {"s1", "s3"}
    assert jr.pick_sample(rows, fraction=0.2) == picked, "the pick is seeded"


def test_the_sample_document_shows_the_answer_the_scores_and_the_justification(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind)
    merge(tmp_path, rows, sha="c0ffee")

    path, count = jr.run_sample(
        reports_dir=tmp_path / "reports",
        eval_dir=tmp_path / "eval",
        docs_dir=tmp_path / "docs",
        rows=rows,
        fraction=1.0,
        sha="c0ffee",
    )
    text = path.read_text(encoding="utf-8")
    assert count == 1
    assert path.name == jr.SAMPLE_DOC
    assert "`q001.s1`" in text
    assert "It was [KAFKA-1]." in text
    assert "KAFKA-1 did." in text  # the gold answer
    assert QUOTE in text  # the judge's justification
    assert "| faithfulness | 2.00 |" in text


def test_sampling_before_a_merge_says_so(tmp_path):
    with pytest.raises(jr.JudgeError, match="run `brain eval judge merge` first"):
        jr.run_sample(
            reports_dir=tmp_path / "reports",
            eval_dir=tmp_path / "eval",
            docs_dir=tmp_path / "docs",
            rows=[row()],
        )


def test_a_batch_that_omits_pairs_is_still_merged(tmp_path):
    """A missing empty array must not quarantine a batch full of good single scores."""
    answers = [answer("q001", "s1r"), answer("q001", "s3")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=("s3",))
    root = tmp_path / "batches" / "judge"
    for in_path in sorted(root.glob("shard-*/*.in.json")):
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        path = write_judgments(
            tmp_path,
            payload["shard"],
            in_path.name.split(".", 1)[0],
            scores=[score(c["case_id"]) for c in payload["cases"]],
        )
        written = json.loads(path.read_text(encoding="utf-8"))
        written.pop("pairs")
        path.write_text(json.dumps(written), encoding="utf-8")

    report = merge(tmp_path, rows)
    assert report["batches"]["failed"] == []
    assert report["judgments"]["total"] == 2
    assert len(report["judgments"]["unjudged_labels"]) == 1, "the pairwise case is named, not lost"


def test_a_pairs_field_that_is_not_an_array_is_still_an_envelope_error(tmp_path):
    answers = [answer("q001", "s1")]
    rows = [row("q001")]
    _, blind = setup(tmp_path, answers, rows, pair_with=())
    answer_all(tmp_path, blind)
    path = next((tmp_path / "batches" / "judge").glob("shard-*/*.out.json"))
    written = json.loads(path.read_text(encoding="utf-8"))
    written["pairs"] = "none"
    path.write_text(json.dumps(written), encoding="utf-8")
    report = merge(tmp_path, rows)
    assert report["batches"]["failed"][0]["where"] == "retry"
