"""What a citation resolves to, and what "it is not in the graph" is allowed to mean.

Two things the closing review of Plan 3 Task 3 found, both here:

* a bare forty-hex token was only ever asked about as a `Commit.sha`, so ten `StatusChange`
  ids and one un-truncated `Chunk.id` were reported as "not in the graph" while sitting in
  it. A forty-hex id is ambiguous by construction — three labels key on one — so the check
  asks all three, in order, instead of guessing one.
* the graph moved between the retrieval and the check (the incremental slice re-partitioned
  the communities), so three community ids that were really returned are really gone. That
  is `in_graph_now: false` and `in_retrieval_snapshot: true`, which is a different sentence
  from "the answer invented a citation".
"""

from __future__ import annotations

import json
from typing import Any

from brain.config import Settings
from brain.eval import snapshot as snap
from brain.eval.citations import classify, find_citations
from brain.eval.verify import GraphVerifier
from brain.retrieve.context import RetrieveContext

PREFIX = "_Eval"
COMMIT = "a" * 40
CHUNK = "b" * 40
STATUS = "c" * 40
NOWHERE = "d" * 40


class FakeClient:
    """Three labels that all key on a forty-hex id, and nothing else."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        values = params.get("values", [])
        for label, known in (("Commit", COMMIT), ("Chunk", CHUNK), ("StatusChange", STATUS)):
            if f"{PREFIX}{label}`" in cypher:
                self.asked.append(label)
                return [
                    {
                        "cited": v,
                        "resolved": [known],
                        "matches": 1,
                        "title": f"{label} title",
                        **({"orphaned": False} if label == "Chunk" else {}),
                    }
                    for v in values
                    if known.startswith(v)
                ]
        raise AssertionError(f"unexpected query: {cypher}")

    def close(self) -> None:  # pragma: no cover - the context closes it
        pass


def verifier() -> tuple[GraphVerifier, FakeClient]:
    client = FakeClient()
    ctx = RetrieveContext(client=client, settings=Settings(), prefix=PREFIX)
    return GraphVerifier(ctx), client


# ------------------------------------------------------------------- the forty-hex chain


def test_a_bare_forty_hex_commit_still_resolves_as_a_commit():
    verify, client = verifier()
    verdict = verify(find_citations(f"It was [{COMMIT}]."))[f"commit:{COMMIT}"]
    assert verdict.ok is True
    assert client.asked == ["Commit"], "a commit must not cost three round trips"


def test_a_bare_forty_hex_chunk_id_is_tried_as_a_chunk_after_the_commit_misses():
    verify, client = verifier()
    verdict = verify(find_citations(f"It was [{CHUNK}]."))[f"commit:{CHUNK}"]
    assert verdict.ok is True
    assert verdict.resolved_as == CHUNK
    assert "Chunk" in (verdict.note or "")
    assert client.asked == ["Commit", "Chunk"]


def test_a_bare_forty_hex_status_change_id_is_tried_last():
    verify, client = verifier()
    verdict = verify(find_citations(f"It was [{STATUS}]."))[f"commit:{STATUS}"]
    assert verdict.ok is True
    assert verdict.resolved_as == STATUS
    assert "StatusChange" in (verdict.note or "")
    assert client.asked == ["Commit", "Chunk", "StatusChange"]


def test_a_forty_hex_that_is_none_of_the_three_names_all_three_in_its_reason():
    verify, _ = verifier()
    verdict = verify(find_citations(f"It was [{NOWHERE}]."))[f"commit:{NOWHERE}"]
    assert verdict.ok is False
    for label in ("Commit", "Chunk", "StatusChange"):
        assert label in (verdict.reason or "")


def test_an_abbreviated_sha_is_not_tried_as_a_chunk_or_a_status_change():
    """Seven hex characters is a git abbreviation; over 14k chunks it is a coincidence."""
    verify, client = verifier()
    verdict = verify(find_citations("It was [deadbee]."))["commit:deadbee"]
    assert verdict.ok is False
    assert client.asked == ["Commit"]


def test_status_change_is_a_citation_kind_of_its_own():
    citation = classify(f"statuschange:{STATUS}")
    assert citation is not None
    assert (citation.kind, citation.value) == ("statuschange", STATUS)
    verify, client = verifier()
    assert verify([citation])[citation.id].ok is True
    assert client.asked == ["StatusChange"]


# --------------------------------------------------------------------- the snapshot half


def answer(case_id: str, *, keys=(), available: bool = True) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "context_keys": list(keys),
        "context_available": available,
    }


def log(tmp_path, *entries) -> Any:
    path = tmp_path / "retrieval.jsonl"
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8"
    )
    return path


def test_a_citation_in_the_answers_own_context_is_in_the_snapshot():
    index = snap.RetrievalSnapshot.build([answer("q001.s5", keys=["L0-1436", "KAFKA-1"])])
    assert index.contains("q001.s5", "L0-1436") is True
    assert index.contains("q001.s5", "L9-9") is False
    assert index.source("q001.s5") == snap.CONTEXT


def test_a_truncated_chunk_id_still_matches_the_context_it_came_from():
    index = snap.RetrievalSnapshot.build([answer("q001.s1", keys=[CHUNK])])
    assert index.contains("q001.s1", CHUNK[:12]) is True


def test_an_agentic_citation_is_looked_for_in_the_mcp_trace(tmp_path):
    path = log(
        tmp_path,
        {"mode": "mcp", "hit_ids": ["Community:L0-1436", f"Chunk:{CHUNK}"]},
        {"mode": "eval", "hit_ids": ["Community:L9-9"]},
    )
    index = snap.RetrievalSnapshot.build(
        [answer("q001.agentic", keys=[], available=False)], log_path=path
    )
    assert index.contains("q001.agentic", "L0-1436") is True
    assert index.contains("q001.agentic", CHUNK[:12]) is True
    assert index.contains("q001.agentic", "L9-9") is False, "only `mcp` lines are mode B's"
    assert index.source("q001.agentic") == snap.MCP_TRACE


def test_a_missing_log_is_an_empty_snapshot_not_an_error(tmp_path):
    index = snap.RetrievalSnapshot.build(
        [answer("q001.agentic", available=False)], log_path=tmp_path / "nope.jsonl"
    )
    assert index.contains("q001.agentic", "L0-1436") is False
    assert index.mcp_lines == 0
