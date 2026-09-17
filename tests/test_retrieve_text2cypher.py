"""S4's two modes, and the line between them.

Mode B is the one the spec cares about: the asking agent writes the Cypher and `run_cypher`
is the whole surface. Mode A exists because the Plan 3 evaluation has to run S4 on every
question without dispatching an agent per question, so the question is matched against the
example bank and the closest example's query is re-pointed at this question's anchors.

That is a retrieval over examples, not generation, and these tests hold it to that: the
example it chose and how close it was are recorded on the result, a question nothing in the
bank is about is a refusal rather than a wrong answer, and a `--cypher` that writes never
reaches the driver. The embedder is a bag of words — the real one is `bge-m3` over HTTP,
and what is being asserted here is the selection logic, not the model.
"""

from __future__ import annotations

import math
import zlib

import pytest

from brain.retrieve import text2cypher as t2c
from brain.retrieve.cypher_guard import GuardError
from brain.retrieve.types import RetrieveError


class _Ctx:
    """A graph that plans, runs and embeds — every dependency `text2cypher` has."""

    prefix = ""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = [{"key": "KAFKA-15538", "status": "Resolved"}] if rows is None else rows
        self.ran: list[str] = []
        self.embedded: list[str] = []

    # --- graph
    def label(self, name: str) -> str:
        return f"`{name}`"

    @property
    def client(self):
        return self

    def explain(self, query: object, **params: object) -> dict:
        return {"operatorType": "ProduceResults@neo4j", "children": []}

    def read(self, query: object, **params: object):
        text = str(getattr(query, "text", query))
        if "Component" in text and "RETURN c.name AS name" in text:
            return [{"name": "streams"}, {"name": "clients"}]
        self.ran.append(text)
        return list(self.rows)

    # --- embeddings
    def embed_query(self, text: str) -> list[float]:
        # `crc32`, not `hash`: string hashing is salted per process, and a bucket
        # collision that only happens on some runs is a flaky test, not a measurement.
        self.embedded.append(text)
        vector = [0.0] * 64
        for word in text.lower().replace("?", " ").replace("`", " ").split():
            vector[zlib.crc32(word.encode("utf-8")) % 64] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


@pytest.fixture(autouse=True)
def _fresh():
    t2c.clear_cache()
    yield
    t2c.clear_cache()


# ------------------------------------------------------------------ mode B: given Cypher


def test_a_given_query_runs_through_the_guard() -> None:
    ctx = _Ctx()
    result = t2c.text2cypher(
        ctx,
        "a few work items",
        cypher="MATCH (w:WorkItem) RETURN w.key AS key",
        log=False,
    )
    assert result.strategy == "s4"
    assert result.route["mode"] == "given-cypher"
    assert result.cypher_used[0].rstrip().endswith("LIMIT 100")
    assert [i.kind for i in result.items] == ["Row"]
    assert not ctx.embedded, "mode B never needs the embedder: the caller wrote the query"


def test_a_given_write_query_never_reaches_the_driver() -> None:
    ctx = _Ctx()
    with pytest.raises(GuardError) as exc:
        t2c.text2cypher(ctx, "delete everything", cypher="MATCH (n) DETACH DELETE n", log=False)
    assert exc.value.reason == "write-verb"
    assert ctx.ran == []


# ------------------------------------------------------------------ mode A: the bank


def test_the_closest_example_is_chosen_and_repointed() -> None:
    ctx = _Ctx()
    result = t2c.text2cypher(
        ctx,
        "What was the status of KAFKA-14648 on 2024-03-01?",
        question_type="temporal",
        log=False,
    )
    assert result.route["mode"] == "example-bank"
    assert result.route["example_id"] == "seed-temporal-01"
    assert result.route["similarity"] >= t2c.MIN_SIMILARITY
    assert result.route["params"] == {"key": "KAFKA-14648", "date": "2024-03-01"}


def test_the_chosen_example_is_recorded_so_plan_3_can_discount_it() -> None:
    """ "S4 was right" and "the bank held this question" are different claims."""
    ctx = _Ctx()
    example, similarity = t2c.select_example(
        ctx, "Who owns component `streams` (most assignments and commits)?", "traceability"
    )
    assert example["id"] == "seed-traceability-01"
    assert similarity > 0.9, "the bank holds this question verbatim, and the score says so"


def test_a_question_nothing_in_the_bank_is_about_is_refused() -> None:
    ctx = _Ctx()
    with pytest.raises(RetrieveError, match="no example is close enough"):
        t2c.text2cypher(ctx, "מה מזג האוויר בתל אביב", question_type="temporal", log=False)
    assert ctx.ran == []


def test_an_unknown_question_type_is_refused_before_the_embedder() -> None:
    ctx = _Ctx()
    with pytest.raises(RetrieveError, match="question_type"):
        t2c.text2cypher(ctx, "themes?", question_type="global", log=False)
    assert ctx.embedded == []


def test_an_empty_bank_names_the_command_that_fills_it(monkeypatch) -> None:
    monkeypatch.setattr(t2c, "cypher_examples", lambda *a, **kw: [])
    with pytest.raises(RetrieveError, match="cypher-examples merge"):
        t2c.text2cypher(_Ctx(), "anything", question_type="impact", log=False)


def test_explicit_params_win_over_the_bound_ones() -> None:
    ctx = _Ctx()
    result = t2c.text2cypher(
        ctx,
        "What was the status of KAFKA-14648 on 2024-03-01?",
        question_type="temporal",
        params={"key": "KAFKA-1"},
        log=False,
    )
    assert result.route["params"]["key"] == "KAFKA-1"


def test_a_question_is_embedded_once_per_process() -> None:
    """The bank does not change under a running agent; re-embedding it per call is latency."""
    ctx = _Ctx()
    question = "What was the status of KAFKA-14648 on 2024-03-01?"
    t2c.text2cypher(ctx, question, question_type="temporal", log=False)
    first = len(ctx.embedded)
    t2c.text2cypher(ctx, question, question_type="temporal", log=False)
    assert len(ctx.embedded) == first


def test_the_search_is_restricted_to_the_named_type() -> None:
    ctx = _Ctx()
    example, _ = t2c.select_example(ctx, "What was the status of KAFKA-14648?", "rationale")
    assert example["type"] == "rationale"
