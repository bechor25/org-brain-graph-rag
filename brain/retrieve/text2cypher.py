"""S4 — Text2Cypher, in the two modes the plan asks for.

**Mode B** (the real one, spec §4.3): the asking agent reads `get_schema` and
`cypher_examples`, writes the query itself, and calls `run_cypher`. Nothing in this file is
involved; the guard is the whole surface.

**Mode A** (evaluation, and `brain ask --strategy s4` without a `--cypher`): there is no
model in the loop — the evaluation harness in Plan 3 has to run S4 on every question
without dispatching an agent per question — so the question is matched against the example
bank and the closest example's query is re-pointed at this question's anchors.

That is a *retrieval* over examples, not generation, and it is honest about it: the result
records `route.example_id` and `route.similarity`, so a Plan 3 table can separate "S4 was
right" from "the bank happened to contain this question". Matching is done with the same
`bge-m3` embedder as every other strategy, which is what lets the four Hebrew competency
questions select their English twin's query — a lexical match cannot do that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.cypher_guard import DEFAULT_LIMIT, DEFAULT_TIMEOUT_S, run_cypher
from brain.retrieve.examples import QUESTION_TYPES, bind_params, cypher_examples
from brain.retrieve.types import Result, RetrieveError

#: Below this cosine similarity the closest example is not about the same question, and
#: re-pointing its parameters would answer something nobody asked. Deliberately low: the
#: bank is small, and a *related* aggregation is still a better answer than none — the
#: score is reported so the evaluation can decide what it was worth.
MIN_SIMILARITY = 0.4

_VECTORS: dict[str, list[float]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embed(ctx: Any, text: str) -> list[float]:
    """Embed once per process per string — the bank does not change under a running agent."""
    if text not in _VECTORS:
        _VECTORS[text] = ctx.embed_query(text)
    return _VECTORS[text]


def clear_cache() -> None:
    _VECTORS.clear()


def select_example(
    ctx: Any,
    question: str,
    question_type: str | None = None,
    *,
    path: Path | None = None,
) -> tuple[dict[str, Any] | None, float]:
    """The bank example closest to this question, and how close it is."""
    candidates = cypher_examples(question_type, path=path)
    if not candidates:
        return None, 0.0
    vector = _embed(ctx, question)
    scored = [
        (example, _cosine(vector, _embed(ctx, example["question"]))) for example in candidates
    ]
    scored.sort(key=lambda pair: -pair[1])
    return scored[0]


def text2cypher(
    ctx: Any,
    question: str,
    *,
    cypher: str | None = None,
    params: dict[str, Any] | None = None,
    question_type: str | None = None,
    limit: int = DEFAULT_LIMIT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    examples_path: Path | None = None,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Run `cypher` if the caller wrote one; otherwise answer from the example bank."""
    trace: dict[str, Any] = dict(route or {})
    if cypher:
        trace["mode"] = "given-cypher"
        return run_cypher(
            ctx,
            cypher,
            params,
            timeout_s=timeout_s,
            limit=limit,
            question=question or cypher,
            log_mode=log_mode,
            log_path=log_path,
            log=log,
            route=trace,
        )

    if question_type is not None and question_type not in QUESTION_TYPES:
        raise RetrieveError(f"question_type must be one of {QUESTION_TYPES}, got {question_type!r}")
    example, similarity = select_example(ctx, question, question_type, path=examples_path)
    if example is None:
        raise RetrieveError("the Cypher example bank is empty — run `brain cypher-examples merge`")
    if similarity < MIN_SIMILARITY:
        raise RetrieveError(
            f"no example is close enough to this question (best {example['id']} at "
            f"{similarity:.2f} < {MIN_SIMILARITY}); write the Cypher and pass --cypher"
        )
    bound = dict(bind_params(ctx, question, example))
    bound.update(params or {})
    trace.update(
        {
            "mode": "example-bank",
            "example_id": example["id"],
            "example_type": example["type"],
            "similarity": round(similarity, 4),
            "params": bound,
        }
    )
    return run_cypher(
        ctx,
        example["cypher"],
        bound,
        timeout_s=timeout_s,
        limit=limit,
        question=question,
        log_mode=log_mode,
        log_path=log_path,
        log=log,
        route=trace,
    )
