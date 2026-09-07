"""`data/reports/retrieve.json` — the acceptance evidence for Plan 2 Task 1, measured.

Four questions the report answers with numbers rather than adjectives:

1. **Does every competency question come back with checkable evidence?** Per question:
   the strategy the router suggested, the strategy that ran, latency, item count, anchor
   keys, and how many `provenance[].chunk_id` values actually exist in the graph. A
   citation to a chunk id that is not there is the failure mode this whole POC is built to
   avoid, so it is checked against the graph, not trusted.
2. **Is the corpus really cross-lingual?** The four Hebrew questions and their English
   twins are run through S1 and S3 and their anchor keys compared. bge-m3 being
   multilingual is a claim; a Jaccard number over the same corpus is a measurement.
3. **Is it fast enough to be agentic?** p50 (and p90, which is where a tool feels slow) per
   strategy, embedding round trip included.
4. **Which vector syntax does this server actually take?** Plan decision 4 asked for the
   check to be run and recorded rather than assumed. It is run here, live, both ways.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.retrieve import competency
from brain.retrieve.context import CHUNK_FULLTEXT, CHUNK_INDEX, ENTITY_INDEX, RetrieveContext
from brain.retrieve.hybrid import search_chunks
from brain.retrieve.local import local_search
from brain.retrieve.runner import ask
from brain.retrieve.types import Result

DEFAULT_PATH = Path("data/reports/retrieve.json")
#: Latency is a distribution, not a number. Three runs per question per strategy is enough
#: for a p50 that is not the first-call warm-up.
REPEATS = 3
#: The four types the acceptance criterion names.
EVIDENCE_TYPES: frozenset[str] = frozenset({"traceability", "impact", "rationale", "temporal"})


def anchor_keys(result: Result, limit: int = 5) -> list[str]:
    """What this answer is *about*, in rank order — a chunk stands for its parent."""
    out: list[str] = []
    for item in result.items:
        if item.kind == "Chunk":
            source = next((p.source for p in item.provenance if p.source), None)
            key = source or item.props.get("parent_key") or item.key
        else:
            key = item.key
        if key and key not in out:
            out.append(str(key))
    return out[:limit]


def existing_chunk_ids(ctx: RetrieveContext, ids: list[str]) -> set[str]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return set()
    rows = ctx.read(f"MATCH (c:{ctx.label('Chunk')}) WHERE c.id IN $ids RETURN c.id AS id", ids=ids)
    return {r["id"] for r in rows}


def provenance_audit(ctx: RetrieveContext, result: Result) -> dict[str, Any]:
    """How much of this answer a reader could check, and how much of that checks out."""
    entries = [p for item in result.items for p in item.provenance]
    ids = [p.chunk_id for p in entries if p.chunk_id]
    valid = existing_chunk_ids(ctx, ids)
    items_with_valid = sum(
        1 for i in result.items if any(p.chunk_id in valid for p in i.provenance if p.chunk_id)
    )
    return {
        "provenance_entries": len(entries),
        "with_chunk_id": len(ids),
        "distinct_chunk_ids": len(set(ids)),
        "valid_chunk_ids": len(valid),
        "invalid_chunk_ids": sorted(set(ids) - valid)[:5],
        "items_with_valid_provenance": items_with_valid,
        "with_quote": sum(1 for p in entries if p.quote),
    }


def run_question(ctx: RetrieveContext, row: dict[str, Any]) -> dict[str, Any]:
    result = ask(ctx, row["question"], k=10, log_mode="eval")
    trace = result.route or {}
    audit = provenance_audit(ctx, result)
    return {
        "id": row["id"],
        "type": row["type"],
        "lang": row["lang"],
        "question": row["question"],
        "expected_strategy": row["expected_strategy"],
        "route_strategy": trace.get("strategy"),
        "route_reason": trace.get("reason"),
        "route_rule": trace.get("rule"),
        "executed_strategy": result.strategy,
        "tool": trace.get("tool"),
        "fallback_from": trace.get("fallback_from"),
        "latency_ms": result.latency_ms,
        "items": len(result.items),
        "kinds": sorted({i.kind for i in result.items}),
        "anchor_keys": anchor_keys(result),
        "truncated": result.truncated,
        "cypher_statements": len(result.cypher_used),
        **audit,
        "passes_acceptance": (
            row["type"] not in EVIDENCE_TYPES or audit["items_with_valid_provenance"] >= 1
        ),
    }


def cross_lingual(ctx: RetrieveContext, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same question, two languages, two strategies: do the anchors agree?"""
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("pair"):
            pairs.setdefault(row["pair"], {})[row["lang"]] = row
    out: list[dict[str, Any]] = []
    for pair, langs in sorted(pairs.items()):
        if "en" not in langs or "he" not in langs:
            continue
        entry: dict[str, Any] = {
            "pair": pair,
            "en_id": langs["en"]["id"],
            "he_id": langs["he"]["id"],
            "en": langs["en"]["question"],
            "he": langs["he"]["question"],
        }
        # S1 twice on purpose. Its fulltext half is a Lucene query in the *question's*
        # language, and no analyzer here bridges Hebrew and English, so `s1` measures the
        # fusion and `s1_vector` measures what bge-m3 alone does across languages.
        for name, runner in (
            ("s1", lambda q: search_chunks(ctx, q, k=10, log_mode="eval")),
            ("s1_vector", lambda q: search_chunks(ctx, q, k=10, mode="vector", log_mode="eval")),
            ("s3", lambda q: local_search(ctx, q, k=10, log_mode="eval")),
        ):
            en = anchor_keys(runner(langs["en"]["question"]))
            he = anchor_keys(runner(langs["he"]["question"]))
            shared = [k for k in en if k in he]
            union = len(set(en) | set(he)) or 1
            entry[name] = {
                "en_anchors": en,
                "he_anchors": he,
                "shared": shared,
                "jaccard": round(len(shared) / union, 3),
                "top1_match": bool(en and he and en[0] == he[0]),
                "identical": set(en) == set(he),
            }
        out.append(entry)
    return out


def latency_profile(
    ctx: RetrieveContext, rows: list[dict[str, Any]], repeats: int = REPEATS
) -> dict[str, Any]:
    """p50/p90 per strategy, over every question, embedding round trip included."""
    samples: dict[str, list[int]] = {}
    for _ in range(repeats):
        for row in rows:
            result = ask(ctx, row["question"], k=10, log_mode="eval")
            samples.setdefault(result.strategy, []).append(result.latency_ms)
            for strategy in ("s1", "s2", "s3"):
                fixed = ask(ctx, row["question"], strategy=strategy, k=10, log_mode="eval")
                samples.setdefault(f"{strategy}(fixed)", []).append(fixed.latency_ms)
    return {
        name: {
            "n": len(values),
            "p50_ms": int(statistics.median(values)),
            "p90_ms": int(sorted(values)[max(0, int(len(values) * 0.9) - 1)]),
            "max_ms": max(values),
        }
        for name, values in sorted(samples.items())
    }


def vector_syntax_check(ctx: RetrieveContext) -> dict[str, Any]:
    """Plan decision 4, answered by the server rather than by the release notes."""
    probe = [0.0] * int(ctx.settings.embed_dim)
    probe[0] = 1.0
    out: dict[str, Any] = {"server": None, "search_clause": None, "query_nodes": None}
    try:
        row = ctx.read("CALL dbms.components() YIELD name, versions, edition RETURN *")[0]
        out["server"] = f"{row['name']} {row['versions'][0]} ({row['edition']})"
    except Exception as exc:  # noqa: BLE001
        out["server"] = f"unavailable: {exc}"
    index = ctx.index(CHUNK_INDEX)
    for name, cypher in (
        # The index name is interpolated, not parameterised: the declarative clause takes a
        # name, not an expression, so a parameter would fail for the wrong reason.
        (
            "search_clause",
            f"SEARCH VECTOR INDEX `{index}` FOR $vector YIELD node, score RETURN score LIMIT 1",
        ),
        (
            "query_nodes",
            "CALL db.index.vector.queryNodes($index, 1, $vector) YIELD score RETURN score LIMIT 1",
        ),
    ):
        try:
            ctx.read(cypher, index=index, vector=probe)
            out[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            out[name] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    out["chosen"] = "db.index.vector.queryNodes" if out["query_nodes"] == "ok" else "none"
    return out


def graph_stats(ctx: RetrieveContext) -> dict[str, Any]:
    counts = ctx.read(
        f"MATCH (c:{ctx.label('Chunk')}) "
        "RETURN count(c) AS chunks, "
        "count(CASE WHEN NOT coalesce(c.orphaned, false) THEN 1 END) AS live, "
        "count(c.embedding) AS embedded"
    )[0]
    entities = ctx.read(
        f"MATCH (e:{ctx.label('Entity')}) RETURN count(e) AS entities, "
        "count(e.embedding) AS embedded"
    )[0]
    return {
        "chunks": counts["chunks"],
        "live_chunks": counts["live"],
        "chunks_with_embedding": counts["embedded"],
        "entities": entities["entities"],
        "entities_with_embedding": entities["embedded"],
        "indexes": sorted(
            n
            for n in ctx.index_names()
            if n in {ctx.index(CHUNK_INDEX), ctx.index(ENTITY_INDEX), ctx.index(CHUNK_FULLTEXT)}
        ),
    }


#: p50 ceiling the plan sets for the three ranked strategies, embedding round trip included.
LATENCY_BUDGET_MS = 1500

NOTES: tuple[str, ...] = (
    "`SEARCH VECTOR INDEX … FOR $v` is refused with a syntax error by Neo4j 2026.06.0 "
    "Community on both CYPHER 5 and CYPHER 25, even though `db.index.vector.queryNodes` "
    "reports itself deprecated in favour of it. `vector_syntax` records both probes; the "
    "library uses the procedure (plan decision 4).",
    "`route_strategy` is what spec §4.2's deterministic pre-router suggests; "
    "`executed_strategy` is what ran. S4 (guarded Text2Cypher) arrives in Task 2 and S5 "
    "(global community search) in Task 3, so a question routed to either is executed by "
    "`impact` (when it names a node the graph holds) or by S3/S1, and `fallback_from` "
    "says so. No question's routing was weakened to fit what is implemented.",
    "Cross-lingual is measured three ways because the halves behave differently: S3 "
    "anchors on the key both languages share, so its anchors are identical; S1's fulltext "
    "half is a Lucene query in the question's own language and no analyzer here bridges "
    "Hebrew and English, so `s1_vector` is the honest measure of what bge-m3 alone does.",
    "A `Chunk-[:MENTIONS]->Component` edge does not exist in this graph (extract produced "
    "MENTIONS only to Entity, WorkItem and Document), so a component anchor walks nowhere "
    "in S3's relation set. That is why an aggregation question naming a component falls "
    "back to `impact`, which traverses `IN_COMPONENT`.",
    "Items derived by traversal (S6, `impact`) carry the anchor node's own description "
    "chunk as provenance. It is weaker evidence than a quote, and it is what makes every "
    "answer checkable by `brain eval cite-check`.",
)


def acceptance_checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan 2 Task 1's acceptance list, each item answered by a number in this report."""
    summary = report["summary"]
    cross = report["cross_lingual_summary"]
    latency = report["latency"]
    slow = {
        name: stats["p50_ms"]
        for name, stats in latency.items()
        if name.startswith(("s1", "s2", "s3")) and stats["p50_ms"] >= LATENCY_BUDGET_MS
    }
    return [
        {
            "name": "every_evidence_question_has_valid_provenance",
            "ok": summary["with_valid_provenance"] == summary["evidence_questions"],
            "detail": f"{summary['with_valid_provenance']}/{summary['evidence_questions']}",
        },
        {
            "name": "no_citation_names_a_chunk_that_does_not_exist",
            "ok": summary["invalid_chunk_ids"] == 0,
            "detail": f"{summary['invalid_chunk_ids']} invalid chunk ids",
        },
        {
            "name": "no_question_returns_an_empty_answer",
            "ok": not summary["empty_answers"],
            "detail": ", ".join(summary["empty_answers"]) or "none",
        },
        {
            "name": "hebrew_questions_return_the_english_anchors_under_s3",
            "ok": cross["s3_identical_anchors"] == cross["pairs"],
            "detail": f"{cross['s3_identical_anchors']}/{cross['pairs']} identical; "
            f"s1 mean jaccard {cross['s1_mean_jaccard']}, "
            f"s1_vector {cross['s1_vector_mean_jaccard']}",
        },
        {
            "name": f"p50_under_{LATENCY_BUDGET_MS}ms_for_s1_s2_s3",
            "ok": not slow,
            "detail": ", ".join(
                f"{n} p50 {latency[n]['p50_ms']}ms"
                for n in sorted(latency)
                if n.startswith(("s1", "s2", "s3"))
            ),
        },
        {
            "name": "vector_index_syntax_resolved_against_the_live_server",
            "ok": report["vector_syntax"]["chosen"] != "none",
            "detail": report["vector_syntax"]["chosen"],
        },
    ]


def build_report(
    ctx: RetrieveContext, rows: list[dict[str, Any]], repeats: int = REPEATS
) -> dict[str, Any]:
    """Every number the acceptance list asks for, measured against the live graph."""
    questions = [run_question(ctx, row) for row in rows]
    evidence = [q for q in questions if q["type"] in EVIDENCE_TYPES]
    pairs = cross_lingual(ctx, rows)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "step": "plan2-task1-retrieval",
        "graph": graph_stats(ctx),
        "vector_syntax": vector_syntax_check(ctx),
        "embedding": {
            "model": ctx.settings.embed_model,
            "dim": ctx.settings.embed_dim,
            "chunk_index_meta": ctx.index_meta(CHUNK_INDEX),
            "entity_index_meta": ctx.index_meta(ENTITY_INDEX),
        },
        "questions": questions,
        "summary": {
            "questions": len(questions),
            "evidence_questions": len(evidence),
            "with_valid_provenance": sum(
                1 for q in evidence if q["items_with_valid_provenance"] >= 1
            ),
            "invalid_chunk_ids": sum(len(q["invalid_chunk_ids"]) for q in questions),
            "route_matches_expected": sum(
                1 for q in questions if q["route_strategy"] == q["expected_strategy"]
            ),
            "fallbacks": sorted({q["fallback_from"] for q in questions if q.get("fallback_from")}),
            "empty_answers": [q["id"] for q in questions if q["items"] == 0],
        },
        "cross_lingual": pairs,
        "cross_lingual_summary": {
            "pairs": len(pairs),
            "s3_identical_anchors": sum(1 for p in pairs if p["s3"]["identical"]),
            "s3_mean_jaccard": round(sum(p["s3"]["jaccard"] for p in pairs) / (len(pairs) or 1), 3),
            "s1_mean_jaccard": round(sum(p["s1"]["jaccard"] for p in pairs) / (len(pairs) or 1), 3),
            "s1_vector_mean_jaccard": round(
                sum(p["s1_vector"]["jaccard"] for p in pairs) / (len(pairs) or 1), 3
            ),
        },
        "latency": latency_profile(ctx, rows, repeats=repeats),
        "notes": list(NOTES),
    }


def write_report(report: dict[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def run(
    ctx: RetrieveContext,
    *,
    rebuild_questions: bool = True,
    questions_path: Path | None = None,
    report_path: Path | None = None,
    repeats: int = REPEATS,
) -> tuple[dict[str, Any], Path]:
    """Build (or reuse) the question set, sweep it, and write the report."""
    if rebuild_questions or not competency.read(questions_path):
        rows = competency.build(ctx)
        competency.write(rows, questions_path)
    else:
        rows = competency.read(questions_path)
    report = build_report(ctx, rows, repeats=repeats)
    report["questions_file"] = str(questions_path or competency.DEFAULT_PATH)
    report["anchors"] = competency.anchor_report(competency.anchors(ctx))
    report["checks"] = acceptance_checks(report)
    return report, write_report(report, report_path)
