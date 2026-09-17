"""`brain eval cite-check` end to end, on three answers and a fake mini-corpus graph.

The three are the three cases the gate exists to tell apart: an answer whose citations all
resolve, an answer that invented a chunk id, and an answer that forgot the `strategy:` line.
Everything except the graph is real — the same parser, the same regex, the same report
writer the live run uses — and the graph is a dict keyed exactly like the mini fixture
(`KAFKA-100`, `KIP-5`, `a1b2c3d4`, `jira:dlee`) under the `_Eval` label namespace, so the
Cypher this asserts on is the Cypher the live run sends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.config import Settings
from brain.eval import gate as gate_mod
from brain.eval.citations import find_citations
from brain.eval.runner import run_cite_check
from brain.eval.verify import GraphVerifier
from brain.retrieve.context import RetrieveContext

PREFIX = "_Eval"
CHUNK_ID = "ab12cd34ef5601234567890123456789abcdef01"

QUESTIONS = [
    {
        "id": "cq01",
        "lang": "en",
        "type": "traceability",
        "expected_strategy": "s3",
        "question": "Which tests cover KAFKA-100?",
    },
    {
        "id": "cq02",
        "lang": "en",
        "type": "traceability",
        "expected_strategy": "s3",
        "question": "Which commits fixed the bug behind KIP-5?",
    },
    {
        "id": "cq16",
        "lang": "he",
        "type": "traceability",
        "expected_strategy": "s3",
        "question": "אילו טסטים מכסים את KAFKA-100?",
    },
]

GOOD = """---
question_id: cq01
lang: en
started_at: 2026-09-17T10:00:00+00:00
finished_at: 2026-09-17T10:00:42+00:00
---

Two tests cover the client work item [KAFKA-100]. The design behind it is [KIP-5], which
the evidence chunk states directly [chunk:ab12cd34ef56]. The client side landed in one
commit [a1b2c3d4], authored by Dana Lee [person:jira:dlee].

strategy: route, lookup, search_with_context
"""

INVALID_CHUNK = """---
question_id: cq02
lang: en
started_at: 2026-09-17T10:01:00+00:00
finished_at: 2026-09-17T10:01:30+00:00
---

One commit fixed it [c9d0e1f2]. The rollout rationale is written down [chunk:deadbeefdead].

strategy: route, search_chunks
"""

NO_STRATEGY = """---
question_id: cq16
lang: he
started_at: 2026-09-17T10:02:00+00:00
finished_at: 2026-09-17T10:02:20+00:00
---

שני טסטים מכסים את פריט העבודה [KAFKA-100]. אף אחד מהם לא הורץ מעולם [KAFKA-101].
"""


# --------------------------------------------------------------------------- the fake graph


class FakeClient:
    """Answers the verifier's five queries out of the mini fixture, and records them.

    It refuses a query whose labels are not namespaced, because a checker that reads
    unprefixed `Chunk` in a test is a checker reading the real 13,846-chunk graph.
    """

    WORKITEMS = {
        "KAFKA-100": "Implement new consumer group protocol (KIP-5) in clients",
        "KAFKA-101": "Rebalance storm when group coordinator restarts",
    }
    DOCUMENTS = {"KIP-5": "KIP-5: Next generation consumer group protocol"}
    PERSONS = {"jira:dlee": "Dana Lee", "jira:jrao": "Jun Rao"}
    COMMUNITIES = {"L1-7": "Consumer group protocol"}
    COMMITS = {
        "a1b2c3d4": "KAFKA-100: client side of KIP-5 (#14001)",
        "c9d0e1f2": "KAFKA-101: WIP fencing fix for coordinator restart",
    }
    CHUNKS = {CHUNK_ID: "KIP-5"}

    def __init__(self) -> None:
        self.queries: list[str] = []

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.queries.append(cypher)
        assert f"`{PREFIX}" in cypher, f"un-namespaced label in: {cypher}"
        values = params.get("values", [])
        if f"{PREFIX}WorkItem" in cypher:
            return [
                {"cited": v, "resolved": v, "title": self.WORKITEMS[v]}
                for v in values
                if v in self.WORKITEMS
            ]
        if f"{PREFIX}Document" in cypher:
            return [
                {"cited": v, "resolved": v, "title": self.DOCUMENTS[v]}
                for v in values
                if v in self.DOCUMENTS
            ]
        if f"{PREFIX}Person" in cypher and "ENDS WITH" in cypher:
            out = []
            for v in values:
                hits = [k for k in self.PERSONS if k.endswith(f":{v}")]
                if hits:
                    out.append({"cited": v, "matches": len(hits), "resolved": sorted(hits)})
            return out
        if f"{PREFIX}Person" in cypher:
            return [
                {"cited": v, "resolved": v, "title": self.PERSONS[v]}
                for v in values
                if v in self.PERSONS
            ]
        if f"{PREFIX}Community" in cypher:
            return [
                {"cited": v, "resolved": v, "title": self.COMMUNITIES[v]}
                for v in values
                if v in self.COMMUNITIES
            ]
        if f"{PREFIX}Commit" in cypher:
            out = []
            for v in values:
                hits = sorted(k for k in self.COMMITS if k.startswith(v))
                if hits:
                    out.append(
                        {
                            "cited": v,
                            "matches": len(hits),
                            "resolved": hits[:3],
                            "title": self.COMMITS[hits[0]],
                        }
                    )
            return out
        if f"{PREFIX}Chunk" in cypher:
            out = []
            for v in values:
                hits = sorted(k for k in self.CHUNKS if k.startswith(v))
                if hits:
                    out.append(
                        {
                            "cited": v,
                            "matches": len(hits),
                            "resolved": hits[:3],
                            "title": self.CHUNKS[hits[0]],
                            "orphaned": False,
                        }
                    )
            return out
        raise AssertionError(f"unexpected query: {cypher}")

    def close(self) -> None:
        pass


def fake_verify(client: FakeClient | None = None):
    ctx = RetrieveContext(client=client or FakeClient(), settings=Settings(), prefix=PREFIX)
    return GraphVerifier(ctx)


def write_answers(tmp_path: Path, *texts: str) -> Path:
    answers = tmp_path / "answers"
    answers.mkdir(exist_ok=True)
    for text, name in zip(texts, ("cq01.md", "cq02.md", "cq16.md"), strict=False):
        (answers / name).write_text(text, encoding="utf-8")
    return answers


def questions_file(tmp_path: Path) -> Path:
    path = tmp_path / "competency.jsonl"
    path.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in QUESTIONS) + "\n", encoding="utf-8"
    )
    return path


def run(tmp_path: Path, *texts: str, verify=None) -> tuple[dict[str, Any], int, Path]:
    report_path = tmp_path / "plan2_gate.json"
    report, code = run_cite_check(
        answers_dir=write_answers(tmp_path, *texts),
        questions_path=questions_file(tmp_path),
        report_path=report_path,
        verify=verify if verify is not None else fake_verify(),
        echo=lambda _: None,
    )
    return report, code, report_path


def question(report: dict[str, Any], qid: str) -> dict[str, Any]:
    return next(q for q in report["questions"] if q["id"] == qid)


# ------------------------------------------------------------------------------ the verdicts


def test_an_answer_whose_citations_all_resolve_is_fully_valid(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    row = question(report, "cq01")

    assert (row["citations"]["found"], row["citations"]["valid"]) == (5, 5)
    assert row["citations"]["invalid_list"] == []
    assert row["citations"]["by_kind"] == {
        "chunk": 1,
        "commit": 1,
        "document": 1,
        "person": 1,
        "workitem": 1,
    }


def test_an_invented_chunk_id_is_listed_as_invalid_with_a_reason(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    row = question(report, "cq02")

    assert (row["citations"]["found"], row["citations"]["valid"]) == (2, 1)
    (bad,) = row["citations"]["invalid_list"]
    assert bad["text"] == "chunk:deadbeefdead"
    assert bad["kind"] == "chunk" and "no" in bad["reason"].lower()


def test_a_truncated_chunk_citation_resolves_by_prefix(tmp_path: Path) -> None:
    """`[chunk:ab12cd34ef56]` is 12 of 40 hex characters and must still find its chunk."""
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    chunk = next(c for c in question(report, "cq01")["citations"]["items"] if c["kind"] == "chunk")
    assert chunk["valid"] and chunk["resolved_as"] == CHUNK_ID


def test_a_missing_strategy_line_is_recorded_without_failing_the_citations(
    tmp_path: Path,
) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    row = question(report, "cq16")

    assert row["tools"] == []
    assert any("strategy" in p for p in row["problems"])
    assert row["citations"]["valid"] == 2


def test_the_answers_language_is_reported_against_the_questions(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert question(report, "cq16")["lang"] == "he"
    assert question(report, "cq16")["expected_lang"] == "he"
    assert question(report, "cq16")["lang_ok"] is True


def test_every_answer_carries_its_length_and_the_files_timestamp(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    row = question(report, "cq01")

    assert row["answer_chars"] > 100
    assert row["answer_modified_at"].endswith("+00:00")
    assert row["latency_ms"] == 42000


# -------------------------------------------------------------------------------- totals


def test_the_totals_add_up_and_the_validity_rate_is_a_percentage(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    totals = report["totals"]

    assert totals["questions"] == 3
    assert totals["answered"] == 3
    assert totals["missing"] == []
    assert totals["with_at_least_one_valid_citation"] == 3
    assert totals["citations_found"] == totals["citations_valid"] + totals["citations_invalid"]
    assert totals["validity_rate"] == round(100 * totals["citations_valid"] / 9, 2)


def test_the_tool_histogram_counts_answers_not_mentions(tmp_path: Path) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert report["totals"]["tools_used"]["route"] == 2
    assert report["totals"]["tools_used"]["lookup"] == 1


# ---------------------------------------------------------------------------- the exit code


def test_a_missing_answer_fails_the_gate_and_is_named(tmp_path: Path) -> None:
    report, code, _ = run(tmp_path, GOOD, INVALID_CHUNK)

    assert code == 1
    assert report["totals"]["missing"] == ["cq16"]
    assert question(report, "cq16")["answered"] is False
    assert not gate_mod.criterion(report, "every_question_answered")["ok"]


def test_an_answer_with_no_valid_citation_at_all_fails_the_gate(tmp_path: Path) -> None:
    empty = NO_STRATEGY.replace("[KAFKA-100]", "[KAFKA-999]").replace("[KAFKA-101]", "")
    report, code, _ = run(tmp_path, GOOD, INVALID_CHUNK, empty)

    assert code == 1
    assert question(report, "cq16")["citations"]["valid"] == 0
    criterion = gate_mod.criterion(report, "every_answer_has_a_valid_citation")
    assert not criterion["ok"] and "cq16" in criterion["detail"]


def test_three_good_answers_pass_the_gate(tmp_path: Path) -> None:
    report, code, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert code == 0 and report["gate"]["ok"]


def test_a_low_validity_rate_is_reported_but_does_not_decide_the_exit_code(
    tmp_path: Path,
) -> None:
    """The brief gates on missing answers and zero-citation answers; the 90% is Plan 2's."""
    junk = GOOD.replace("[KAFKA-100]", "[KAFKA-901]").replace("[KIP-5]", "[KIP-902]")
    report, code, _ = run(tmp_path, junk, INVALID_CHUNK, NO_STRATEGY)

    rate = gate_mod.criterion(report, "citation_validity_rate_at_least_90")
    assert not rate["ok"] and rate["gate"] is False
    assert code == 0


# ------------------------------------------------------------------------- the report file


def test_the_report_is_written_where_asked_with_a_sha_and_a_timestamp(tmp_path: Path) -> None:
    report, _, path = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    on_disk = json.loads(path.read_text(encoding="utf-8"))

    assert on_disk == report
    assert on_disk["step"] == "plan2-gate"
    assert isinstance(on_disk["sha"], str)
    assert on_disk["generated_at"].endswith("+00:00")


def test_the_planner_verdict_map_starts_empty_and_says_what_goes_in_it(
    tmp_path: Path,
) -> None:
    report, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert report["planner_verdicts"] == {}
    assert set(report["planner_verdicts_note"]["values"]) == {"correct", "partial", "wrong"}


def test_a_rerun_does_not_wipe_verdicts_the_planner_already_wrote(tmp_path: Path) -> None:
    """A checker that deletes the planner's only hand-written column is a checker nobody runs."""
    report, _, path = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    report["planner_verdicts"] = {"cq01": "correct"}
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    again, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert again["planner_verdicts"] == {"cq01": "correct"}
    assert again["planner_verdicts_note"]["carried_from"] == report["generated_at"]


def test_a_verdict_for_a_question_that_no_longer_exists_is_dropped(tmp_path: Path) -> None:
    report, _, path = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)
    report["planner_verdicts"] = {"cq01": "correct", "cq99": "wrong"}
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    again, _, _ = run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY)

    assert again["planner_verdicts"] == {"cq01": "correct"}


# ------------------------------------------------------------------------------- the graph


def test_every_citation_kind_is_verified_in_one_query_per_kind(tmp_path: Path) -> None:
    """One round trip per kind, not per citation: the gate runs on 19 answers at once."""
    client = FakeClient()
    run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY, verify=fake_verify(client))

    assert len(client.queries) == 5  # workitem, document, chunk, commit, person
    assert all("UNWIND $values" in q for q in client.queries)


def test_nothing_the_verifier_sends_can_write(tmp_path: Path) -> None:
    client = FakeClient()
    run(tmp_path, GOOD, INVALID_CHUNK, NO_STRATEGY, verify=fake_verify(client))

    for query in client.queries:
        assert not any(verb in query.upper() for verb in ("CREATE", "MERGE", "DELETE", "SET "))


def test_a_person_cited_without_its_source_prefix_resolves_when_it_is_unambiguous() -> None:
    verify = fake_verify()
    (citation,) = find_citations("[person:dlee]")
    verdict = verify([citation])[citation.id]

    assert verdict.ok and verdict.resolved_as == "jira:dlee"
    assert verdict.note and "suffix" in verdict.note


def test_a_key_that_is_in_no_label_is_invalid_and_says_which_label_was_searched() -> None:
    verify = fake_verify()
    (citation,) = find_citations("[KAFKA-999]")
    verdict = verify([citation])[citation.id]

    assert not verdict.ok and "WorkItem" in verdict.reason


def test_a_chunk_prefix_too_short_to_check_never_reaches_the_graph() -> None:
    client = FakeClient()
    verify = fake_verify(client)
    (citation,) = find_citations("[chunk:ab12]")
    verdict = verify([citation])[citation.id]

    assert not verdict.ok and client.queries == []


# ----------------------------------------------------------------- the real question set


def test_the_real_competency_file_has_the_nineteen_ids_the_gate_expects() -> None:
    from brain.eval.gate import load_questions

    questions = load_questions(Path("data/eval/competency.jsonl"))

    assert [q["id"] for q in questions] == [f"cq{i:02d}" for i in range(1, 20)]
    assert sum(1 for q in questions if q["lang"] == "he") == 4
