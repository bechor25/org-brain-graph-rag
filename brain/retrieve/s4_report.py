"""The Task 2 evidence, measured: the guard's attack table, the reranker's effect, the bank.

Three claims are made about S4, and a claim about security that is not a number is a
slogan. So each one is measured against the live graph and written into the step report
next to Task 1's numbers:

* **`guard`** — every case in `guard_cases.BLOCKED` is sent through the real `run_cypher`
  with a real server behind it, and every case in `ALLOWED` is *run*, not merely planned:
  a guard that refuses everything is safe and useless, so the read table is as much of the
  evidence as the write table. The report records which layer refused each attack (the
  deny-list, the `EXPLAIN` plan, or the server), how many rejections reached the JSONL
  trace, the timeout proven against a deliberately slow query, and a count of the nodes the
  attacks would have created if any had succeeded.
* **`rerank`** — S1 over the nineteen competency questions, with and without the local
  cross-encoder, comparing top-1 and the top-5 set. Plan decision 5 does not ask for an
  improvement here (Plan 3 judges relevance); it asks for the change to be *measured*, and
  the cost in latency is measured with it.
* **`cypher_examples`** — the bank as it stands: every example guarded, run and required to
  return a row, plus the batch waiting for `cypher-author` and the two aggregation-shaped
  competency questions (cq03, cq06) answered end to end through S4's example-bank mode.

Merged rather than written: `brain competency` owns the file, `brain serve --check` adds
the MCP sections, and this adds three more. Whoever runs last must not erase the others.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.retrieve import competency
from brain.retrieve import cypher_guard as guard
from brain.retrieve import examples as ex
from brain.retrieve import rerank as rerank_mod
from brain.retrieve.context import RetrieveContext
from brain.retrieve.guard_cases import ALLOWED, BLOCKED, PLAN_BLOCKED
from brain.retrieve.hybrid import search_chunks
from brain.retrieve.log import DEFAULT_LOG, read_log
from brain.retrieve.schema import get_schema
from brain.retrieve.text2cypher import text2cypher
from brain.retrieve.types import Result, RetrieveError

REPORT_PATH = Path("data/reports/retrieve.json")
#: Same as the rest of this report: three runs, so a p50 is not a cold start.
REPEATS = 3
#: 200M rows of nothing. Cheap to plan, impossible to finish inside a second.
SLOW_QUERY = "UNWIND range(1, 200000000) AS x RETURN count(x) AS n"
#: Far below the 10s production budget, because the point is that the *server* stops it.
TIMEOUT_BUDGET_S = 1.0
#: The two competency questions the router sends to S4 (aggregation-shaped).
S4_QUESTION_IDS: tuple[str, ...] = ("cq03", "cq06")
#: Labels the attack table would have created. Counted afterwards, not assumed absent.
ATTACK_LABELS: tuple[str, ...] = ("_GuardTmp", "Foo", "Poisoned")
#: Which layer of the guard a refusal came from (module 10: defence in depth is only
#: defence in depth if you can say which layer did the work).
LAYERS: dict[str, str] = {
    "empty": "static",
    "too-long": "static",
    "multiple-statements": "static",
    "escape-sequence": "static",
    "unbalanced-quote": "static",
    "write-verb": "deny-list",
    "call-subquery": "deny-list",
    "procedure-not-allowed": "allowlist",
    "show-not-allowed": "allowlist",
    "write-plan": "explain",
    "invalid-cypher": "explain",
    "timeout": "server",
    "read-mode": "server",
}


def _p(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))
    return ordered[index]


def _stats(values: list[int]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": _p(values, 0.5),
        "p90_ms": _p(values, 0.9),
        "mean_ms": int(statistics.fmean(values)) if values else 0,
        "max_ms": max(values) if values else 0,
    }


# --------------------------------------------------------------------------- guard


def read_params(ctx: RetrieveContext) -> dict[str, Any]:
    """Bindings for the read table, chosen by the graph rather than typed here.

    The read cases are parameterised (`$key`, `$vector`, `$q`) because real queries are.
    Picking the anchor in code — the work item that actually carries tests, changes and an
    assignee — is plan decision 7 applied to the guard's own evidence: a report whose read
    table returns rows only because someone hardcoded a lucky key starts lying the first
    time the corpus is rebuilt.
    """
    rows = ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')}) "
        f"OPTIONAL MATCH (t:{ctx.label('Test')})-[:TESTS]->(w) "
        f"OPTIONAL MATCH (w)-[:HAS_CHANGE]->(s:{ctx.label('StatusChange')}) "
        f"OPTIONAL MATCH (w)-[:ASSIGNED_TO]->(p:{ctx.label('Person')}) "
        "WITH w, count(DISTINCT t) AS tests, count(DISTINCT s) AS changes, "
        "count(DISTINCT p) AS people, max(s.at) AS last_change "
        "WHERE tests > 0 AND changes > 0 AND people > 0 "
        "RETURN w.key AS key, toString(date(last_change)) AS date "
        "ORDER BY tests + changes + people DESC, w.key LIMIT 1"
    )
    key = rows[0]["key"] if rows else "KAFKA-15538"
    # The date is this key's last status change, not a date typed here: "status at a date"
    # only returns a row for a date the item has lived through.
    return {
        "key": key,
        "date": (rows[0].get("date") if rows else None) or "2024-01-16",
        "q": "rebalance",
        "component": "streams",
        "version": "3.8",
        "vector": ctx.embed_query("consumer rebalance protocol"),
    }


def _attack_side_effects(ctx: RetrieveContext) -> dict[str, Any]:
    """Did any attack land? Counted, not assumed."""
    counts = {
        label: ctx.read(f"MATCH (n:`{label}`) RETURN count(n) AS n")[0]["n"]
        for label in ATTACK_LABELS
    }
    # Filtered by name on the server: the attack table's two index names, not every index
    # in the database (Plan 1 review lesson on database-wide reads in step code).
    evil = ctx.read(
        "SHOW INDEXES YIELD name WHERE name IN $names RETURN name", names=["evil_idx", "evil"]
    )
    return {
        "labels": counts,
        "nodes_created": sum(counts.values()),
        "evil_indexes": sorted(r["name"] for r in evil),
    }


def guard_section(ctx: RetrieveContext, *, log_path: Path | None = None) -> dict[str, Any]:
    """Run both tables through the real guard against the real server, and count."""
    log_file = log_path or DEFAULT_LOG
    before = len(read_log(log_file))
    params = read_params(ctx)

    blocked: list[dict[str, Any]] = []
    for name, cypher, expected in BLOCKED:
        started = time.perf_counter()
        row: dict[str, Any] = {"id": name, "expected": expected}
        try:
            result = guard.run_cypher(ctx, cypher, params, log_path=log_path)
        except guard.GuardError as err:
            row.update(
                {
                    "blocked": True,
                    "reason": err.reason,
                    "layer": LAYERS.get(err.reason, "?"),
                    "as_expected": err.reason == expected,
                    "hint": err.hint,
                }
            )
        except Exception as exc:  # noqa: BLE001 - anything else is a guard that leaked
            row.update({"blocked": False, "reason": f"{type(exc).__name__}: {exc}"[:200]})
        else:
            row.update({"blocked": False, "reason": None, "rows": len(result.items)})
        row["ms"] = int((time.perf_counter() - started) * 1000)
        blocked.append(row)

    allowed: list[dict[str, Any]] = []
    for name, cypher in ALLOWED:
        row = {"id": name}
        try:
            result = guard.run_cypher(ctx, cypher, params, limit=5, log_path=log_path)
        except guard.GuardError as err:
            row.update({"passed": False, "reason": err.reason, "detail": err.detail[:200]})
        else:
            row.update(
                {
                    "passed": True,
                    "rows": len(result.items),
                    "latency_ms": result.latency_ms,
                    "limit_injected": bool(result.route and result.route.get("limit_injected")),
                }
            )
        allowed.append(row)

    plan_only = _plan_layer_proof(ctx)
    timeout = _timeout_proof(ctx, log_path=log_path)

    records = read_log(log_file)[before:]
    rejections = [r for r in records if r.get("rejected")]
    refused = [r for r in blocked if r["blocked"]]
    by_reason: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for row in refused:
        by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + 1
        by_layer[row["layer"]] = by_layer.get(row["layer"], 0) + 1
    passed = [r for r in allowed if r["passed"]]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": ex.head_sha(),
        "mode": "live",
        "params": {
            k: (f"<{len(v)} floats>" if isinstance(v, list) else v) for k, v in params.items()
        },
        "blocked": {
            "cases": len(BLOCKED),
            "refused": len(refused),
            "refused_pct": round(100.0 * len(refused) / (len(BLOCKED) or 1), 1),
            "refused_for_the_expected_reason": sum(1 for r in refused if r["as_expected"]),
            "leaked": [r["id"] for r in blocked if not r["blocked"]],
            "by_reason": dict(sorted(by_reason.items())),
            "by_layer": dict(sorted(by_layer.items())),
            "table": blocked,
        },
        "allowed": {
            "cases": len(ALLOWED),
            "passed": len(passed),
            "returned_rows": sum(1 for r in passed if r.get("rows")),
            "limit_injected": sum(1 for r in passed if r.get("limit_injected")),
            "refused": [r["id"] for r in allowed if not r["passed"]],
            "latency": _stats([r["latency_ms"] for r in passed if "latency_ms" in r]),
            "table": allowed,
        },
        "plan_only": plan_only,
        "timeout": timeout,
        "logging": {
            "path": str(log_file),
            "records_written": len(records),
            "rejections_logged": len(rejections),
            "rejections_expected": len(refused) + (1 if timeout["refused"] else 0),
            "reasons_logged": sorted({str(r["rejected"]) for r in rejections}),
            "every_rejection_carries_a_reason": all(r.get("rejected") for r in rejections),
        },
        "side_effects": _attack_side_effects(ctx),
    }


def _plan_layer_proof(ctx: RetrieveContext) -> dict[str, Any]:
    """Layer 3 on its own: the plan, read without the deny-list in front of it.

    `INSERT` is why this measurement exists. On 2026.06 it is GQL's `CREATE`, the deny-list
    had never heard of the word, and the only thing that refused it was the operator the
    planner produced. The word is in `WRITE_TOKENS` now — which is exactly when a defence
    stops being visible, so the plan is still asked, and the answer is still recorded.

    Nothing runs here: `EXPLAIN` plans and returns. The attack labels are counted afterwards
    by `_attack_side_effects` like every other case in the table.
    """
    cases: list[dict[str, Any]] = []
    for name, cypher, expected in PLAN_BLOCKED:
        row: dict[str, Any] = {"id": name, "expected": expected}
        try:
            plan = guard.explain(ctx, cypher, {}, guard.DEFAULT_TIMEOUT_S)
        except guard.GuardError as err:
            row.update({"refused": True, "by": "server", "reason": err.reason})
        else:
            writes = guard.plan_write_operators(plan)
            row.update(
                {
                    "refused": bool(writes),
                    "by": "explain",
                    "operators": sorted(set(writes)),
                    "also_refused_by_the_deny_list": _refused_statically(cypher),
                }
            )
        cases.append(row)
    return {
        "cases": len(PLAN_BLOCKED),
        "refused": sum(1 for c in cases if c["refused"]),
        "table": cases,
        "note": "the deny-list is bypassed here on purpose; `guard.check` refuses all of "
        "these too, which is what the unit test asserts",
    }


def _refused_statically(cypher: str) -> str | None:
    try:
        guard.check(cypher)
    except guard.GuardError as err:
        return err.reason
    return None


def _timeout_proof(ctx: RetrieveContext, *, log_path: Path | None = None) -> dict[str, Any]:
    """A read-only query can still be an outage. Prove the server, not the client, stops it."""
    started = time.perf_counter()
    reason, detail = None, ""
    try:
        guard.run_cypher(ctx, SLOW_QUERY, timeout_s=TIMEOUT_BUDGET_S, log_path=log_path)
    except guard.GuardError as err:
        reason, detail = err.reason, err.detail[:200]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "cypher": SLOW_QUERY,
        "budget_s": TIMEOUT_BUDGET_S,
        "elapsed_ms": elapsed_ms,
        "refused": reason == "timeout",
        "reason": reason,
        "detail": detail,
        "enforced_by": "server (neo4j.Query timeout)",
        "production_budget_s": guard.DEFAULT_TIMEOUT_S,
    }


# --------------------------------------------------------------------------- rerank


def _keys(result: Result, n: int = 5) -> list[str]:
    return [i.key for i in result.items[:n]]


def _sources(result: Result, n: int = 5) -> list[str]:
    """The parent a chunk belongs to. A chunk id moving says less than its document moving."""
    out: list[str] = []
    for item in result.items[:n]:
        source = next((p.source for p in item.provenance if p.source), "")
        out.append(str(source or item.props.get("parent_key") or item.key))
    return out


def rerank_section(
    ctx: RetrieveContext, rows: list[dict[str, Any]], *, k: int = 10
) -> dict[str, Any]:
    """S1 with and without the cross-encoder, over every competency question."""
    started = time.perf_counter()
    model = rerank_mod.load_model()
    load_ms = int((time.perf_counter() - started) * 1000)
    state = rerank_mod.status()
    if model is None:
        return {
            "model": rerank_mod.MODEL_NAME,
            "available": False,
            "error": state["error"],
            "questions": [],
            "note": "plan decision 5: a missing reranker is a warning and the original order",
        }

    measured: list[dict[str, Any]] = []
    for row in rows:
        question = row["question"]
        base = search_chunks(ctx, question, k=k, rerank=False, log_mode="eval")
        ranked = search_chunks(ctx, question, k=k, rerank=True, log_mode="eval")
        before, after = _keys(base, k), _keys(ranked, k)
        src_before, src_after = _sources(base, k), _sources(ranked, k)
        measured.append(
            {
                "id": row["id"],
                "lang": row["lang"],
                "type": row["type"],
                "question": question,
                "top1_before": before[0] if before else "",
                "top1_after": after[0] if after else "",
                "top1_source_before": src_before[0] if src_before else "",
                "top1_source_after": src_after[0] if src_after else "",
                "top1_source_changed": bool(
                    src_before and src_after and src_before[0] != src_after[0]
                ),
                "top1_changed": bool(before and after and before[0] != after[0]),
                "top5_overlap": len(set(before[:5]) & set(after[:5])),
                "order_changed": before != after,
                "moved_into_top5": [key for key in after[:5] if key not in before[:5]],
                "latency_ms": base.latency_ms,
                "latency_rerank_ms": ranked.latency_ms,
                "items": len(ranked.items),
            }
        )

    changed = [m for m in measured if m["top1_changed"]]
    plain = [m["latency_ms"] for m in measured]
    with_rerank = [m["latency_rerank_ms"] for m in measured]
    return {
        "model": rerank_mod.MODEL_NAME,
        "available": True,
        "load_ms": load_ms,
        "strategy": "s1",
        "k": k,
        "questions": measured,
        "summary": {
            "questions": len(measured),
            "top1_changed": len(changed),
            "top1_changed_pct": round(100.0 * len(changed) / (len(measured) or 1), 1),
            "top1_changed_ids": [m["id"] for m in changed],
            "top1_source_changed": sum(1 for m in measured if m["top1_source_changed"]),
            "order_changed": sum(1 for m in measured if m["order_changed"]),
            "mean_top5_overlap": round(
                statistics.fmean([m["top5_overlap"] for m in measured]) if measured else 0.0, 2
            ),
            "latency_without_rerank": _stats(plain),
            "latency_with_rerank": _stats(with_rerank),
            "rerank_cost_p50_ms": _p(with_rerank, 0.5) - _p(plain, 0.5),
        },
        "note": "measured, not improved: relevance is judged in Plan 3 (plan decision 5). "
        "A 100% top-1 change rate is what an RRF baseline invites: the fusion scores of ten "
        "chunks sit within ~0.002 of each other (1/(60+rank)), so any second opinion "
        "reorders them. `top1_source_changed` is the stricter number — how often the "
        "reranker moved the answer to a different document.",
    }


# --------------------------------------------------------------------------- the bank


def _validate_bank(ctx: RetrieveContext) -> list[dict[str, Any]]:
    """Every example in the bank, guarded and run. The acceptance criterion is ≥1 row each."""
    out: list[dict[str, Any]] = []
    for example in ex.cypher_examples():
        row: dict[str, Any] = {
            "id": example["id"],
            "type": example["type"],
            "source": example.get("source", "?"),
            "question": example["question"],
            "params": example.get("params", {}),
        }
        try:
            result = guard.run_cypher(
                ctx,
                example["cypher"],
                example.get("params") or {},
                limit=ex.VALIDATION_LIMIT,
                question=example["question"],
                log_mode="eval",
            )
        except guard.GuardError as err:
            row.update({"runs": False, "rows": 0, "reason": err.reason, "detail": err.detail[:200]})
        else:
            row.update({"runs": True, "rows": len(result.items), "latency_ms": result.latency_ms})
        out.append(row)
    return out


#: The sample of assignee changes the join check reads. Bounded on the server: the point is
#: the *rate* at which two identifier spaces meet, and 200 names measure it as well as
#: 4,000 do at a fraction of the cost.
JOIN_SAMPLE = 200


def schema_gaps(ctx: RetrieveContext) -> list[dict[str, Any]]:
    """The three gaps `cypher-author` hit, measured rather than repeated.

    An agent's complaint about a schema is a hypothesis. Two of these turned out to be
    exactly right, one is a missing *join key* that has a partial substitute nobody had
    measured — and the difference matters, because the analyst in Task 4 will otherwise
    either avoid the join entirely or trust it completely.
    """
    schema = get_schema(ctx)
    samples = schema.get("sample_values", {})
    statuses = ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')}) WITH w.status AS v WHERE v IS NOT NULL "
        "RETURN count(DISTINCT v) AS n"
    )
    motivated = ctx.read(
        "MATCH (a)-[:MOTIVATED_BY]->(b) RETURN labels(a) AS from_labels, labels(b) AS to_labels, "
        "count(*) AS n ORDER BY n DESC LIMIT 5"
    )
    by_id = ctx.read(
        f"MATCH (s:{ctx.label('StatusChange')}) "
        "WHERE s.field = 'assignee' AND s.to_id IS NOT NULL "
        "WITH DISTINCT s.to_id AS to_id LIMIT $limit "
        f"OPTIONAL MATCH (p:{ctx.label('Person')}) WHERE p.id = 'jira:' + to_id "
        "RETURN count(DISTINCT to_id) AS sampled, count(DISTINCT p) AS joined",
        limit=JOIN_SAMPLE,
    )
    by_name = ctx.read(
        f"MATCH (s:{ctx.label('StatusChange')}) "
        "WHERE s.field = 'assignee' AND s.to IS NOT NULL "
        "WITH DISTINCT s.to AS name LIMIT $limit "
        f"OPTIONAL MATCH (p:{ctx.label('Person')}) WHERE p.display = name "
        "RETURN count(DISTINCT name) AS sampled, count(DISTINCT p) AS joined",
        limit=JOIN_SAMPLE,
    )
    id_join = by_id[0] if by_id else {"sampled": 0, "joined": 0}
    name_join = by_name[0] if by_name else {"sampled": 0, "joined": 0}
    return [
        {
            "gap": "no sample values for WorkItem.source/status/type, Document.kind, "
            "HAS_RUN.status in the schema snapshot",
            "reported_by": "cypher-author (batch cypher-001)",
            "status": "closed" if samples else "open",
            "fix": "get_schema() now carries `sample_values`: the commonest values of those "
            "five properties, capped at 10 each and cached with the rest of the schema",
            "evidence": {
                "properties_sampled": sorted(samples),
                "workitem_status_distinct_values": statuses[0]["n"] if statuses else 0,
                "workitem_status_sample": samples.get("WorkItem.status", []),
                "has_run_status_sample": samples.get("HAS_RUN.status", []),
            },
        },
        {
            "gap": "MOTIVATED_BY exists only Entity→Entity, so a rationale query cannot walk "
            "Document→Entity through it",
            "reported_by": "cypher-author (batch cypher-001)",
            "status": "open (data, not code)",
            "fix": "plan decision 8 already routes rationale through DECIDES/REJECTS plus "
            "MENTIONS quotes; the seed rationale example does exactly that",
            "evidence": {"endpoints": motivated},
        },
        {
            "gap": "no join key between StatusChange.to (a display name) and Person",
            "reported_by": "cypher-author (batch cypher-001)",
            "status": "open, with a measured substitute",
            "fix": "`p.display = s.to` is the usable join; `'jira:' + s.to_id = p.id` only "
            "covers the Jira half of the corpus. S6's assignees_over_time avoids both by "
            "reading the ASSIGNED_TO edge, which carries valid_from/valid_to",
            "evidence": {
                "sample": JOIN_SAMPLE,
                "join_by_person_id": id_join,
                "join_by_display_name": name_join,
            },
        },
    ]


def _batch_state(batch_dir: Path | None = None) -> dict[str, Any]:
    """What `cypher-examples build` wrote and whether the author has answered it yet."""
    directory = Path(batch_dir) if batch_dir is not None else ex.BATCH_DIR
    inputs = sorted(directory.glob("*.in.json"))
    outputs = sorted(directory.glob("*.out.json"))
    described: list[dict[str, Any]] = []
    for path in inputs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            described.append({"path": str(path), "error": str(exc)[:200]})
            continue
        schema = payload.get("schema", {})
        described.append(
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "within_cap": path.stat().st_size <= ex.MAX_BATCH_BYTES,
                "questions": len(payload.get("questions", [])),
                "labels": len(schema.get("labels", [])),
                "relationships": len(schema.get("relationships", [])),
                "style_examples": len(payload.get("style_examples", [])),
                "instructions": len(payload.get("instructions", [])),
            }
        )
    return {
        "dir": str(directory),
        "max_bytes": ex.MAX_BATCH_BYTES,
        "inputs": described,
        "outputs": [str(p) for p in outputs],
        "state": "merged" if outputs else "awaiting cypher-author (001.out.json)",
    }


def s4_runs(
    ctx: RetrieveContext, rows: list[dict[str, Any]], repeats: int = REPEATS
) -> list[dict[str, Any]]:
    """The aggregation-shaped competency questions, answered through S4 both ways.

    Mode A (question + type against the bank) is what `brain ask --strategy s4` does without
    a Cypher author; mode B (`--cypher`) is what the asking agent does over MCP. Both are
    timed, because the second one is the one the gate in Task 4 will use.
    """
    by_id = {row["id"]: row for row in rows}
    out: list[dict[str, Any]] = []
    for qid in S4_QUESTION_IDS:
        row = by_id.get(qid)
        if not row:
            continue
        record: dict[str, Any] = {"id": qid, "question": row["question"], "type": row["type"]}
        latencies: list[int] = []
        for _ in range(repeats):
            try:
                result = text2cypher(
                    ctx, row["question"], question_type=row["type"], log_mode="eval"
                )
            except (RetrieveError, guard.GuardError) as exc:
                record.update({"mode_a": {"ok": False, "error": str(exc)[:300]}})
                break
            latencies.append(result.latency_ms)
            record["mode_a"] = {
                "ok": True,
                "example_id": (result.route or {}).get("example_id"),
                "similarity": (result.route or {}).get("similarity"),
                "params": (result.route or {}).get("params"),
                "rows": len(result.items),
                "truncated": result.truncated,
                "cypher": result.cypher_used[0] if result.cypher_used else "",
                "latencies_ms": latencies,
                **_stats(latencies),
            }
        # Mode B: the same query, handed in the way an agent hands it over MCP.
        given = (record.get("mode_a") or {}).get("cypher") or ""
        if given:
            given_latencies: list[int] = []
            params = (record["mode_a"] or {}).get("params") or {}
            for _ in range(repeats):
                result = text2cypher(
                    ctx, row["question"], cypher=given, params=params, log_mode="eval"
                )
                given_latencies.append(result.latency_ms)
            record["mode_b"] = {
                "ok": True,
                "rows": len(result.items),
                "latencies_ms": given_latencies,
                **_stats(given_latencies),
            }
        out.append(record)
    return out


def _merge_summary() -> dict[str, Any]:
    """What `brain cypher-examples merge` decided, per question, with its reasons.

    Read from the merge's own step report rather than re-run here: validating an answer
    means executing it, and a report that silently re-executes nineteen queries every time
    it is regenerated is a report with a side effect.
    """
    report = ex.read_merge_report()
    if not report:
        return {"ran": False, "note": "no merge report yet — run `brain cypher-examples merge`"}
    rejected = report.get("rejected", [])
    return {
        "ran": True,
        "generated_at": report.get("generated_at"),
        "report_path": str(ex.MERGE_REPORT),
        "batches": report.get("batches", []),
        "answers": report.get("answers"),
        "validated": report.get("validated"),
        "accepted": report.get("accepted"),
        "rejected": len(rejected),
        "rejected_by_reason": {
            reason: sum(1 for r in rejected if r.get("reason") == reason)
            for reason in sorted({str(r.get("reason")) for r in rejected})
        },
        "per_question": report.get("per_question", []),
    }


def _unique_per_type() -> dict[str, int]:
    """Distinct examples per type in the bank as it stands on disk."""
    counts: dict[str, int] = dict.fromkeys(ex.QUESTION_TYPES, 0)
    seen: set[tuple[str, str]] = set()
    for example in ex.cypher_examples():
        keys = ex.dedupe_keys(example)
        if any(k in seen for k in keys):
            continue
        seen.update(keys)
        counts[example["type"]] = counts.get(example["type"], 0) + 1
    return counts


def _duplicate_examples() -> list[dict[str, str]]:
    """Bank rows that repeat an earlier row's query or question. Should be empty after a merge."""
    out: list[dict[str, str]] = []
    seen: dict[tuple[str, str], str] = {}
    for example in ex.cypher_examples():
        for key in ex.dedupe_keys(example):
            if key in seen:
                out.append({"id": example["id"], "same": key[0], "as": seen[key]})
                break
        for key in ex.dedupe_keys(example):
            seen.setdefault(key, example["id"])
    return out


def examples_section(
    ctx: RetrieveContext, rows: list[dict[str, Any]], repeats: int = REPEATS
) -> dict[str, Any]:
    bank = _validate_bank(ctx)
    per_type: dict[str, int] = dict.fromkeys(ex.QUESTION_TYPES, 0)
    for row in bank:
        per_type[row["type"]] = per_type.get(row["type"], 0) + 1
    working = [r for r in bank if r.get("rows")]
    duplicates = _duplicate_examples()
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": ex.head_sha(),
        "bank_path": str(ex.DEFAULT_BANK),
        "bank_size": len(bank),
        "per_type": per_type,
        # Unique by query and by question, which is what "3–5 examples per type" means: a
        # type whose five rows are two patterns has two examples and a misleading count.
        "unique_per_type": _unique_per_type(),
        "min_per_type": ex.MIN_PER_TYPE,
        "thin_types": sorted(t for t, n in _unique_per_type().items() if n < ex.MIN_PER_TYPE),
        "duplicates": duplicates,
        "sources": sorted({r["source"] for r in bank}),
        "run_on_the_live_graph": len([r for r in bank if r.get("runs")]),
        "returning_at_least_one_row": len(working),
        "working_pct": round(100.0 * len(working) / (len(bank) or 1), 1),
        "wanted_per_type": f"3-{ex.MAX_PER_TYPE}",
        "examples": bank,
        "batch": _batch_state(),
        "merge": _merge_summary(),
        "schema_gaps": schema_gaps(ctx),
        "s4": s4_runs(ctx, rows, repeats=repeats),
        "note": "seeds are hand-written and live-verified; the cypher-author batch adds the "
        "rest through `brain cypher-examples merge`, which runs every candidate",
    }


# --------------------------------------------------------------------------- output


def merge(sections: dict[str, Any], path: Path | None = None) -> Path:
    """Add these sections to the step report without erasing anyone else's, and stamp them.

    Three commands write this file — `brain competency` (Task 1), `brain serve --check`
    (Task 3) and this one — and they run in any order, sometimes in parallel agents. So the
    file is read, updated key by key and replaced atomically rather than rewritten.

    The stamping is `report.merge_sections`'s, not a second implementation of it: one
    writer means one `sections` index, and a section written here that stamped itself some
    other way would be exactly the freshness lie the convention exists to prevent. The
    sections also carry their own `sha`/`generated_at`, which is what a reader quoting a
    single number sees without the index in front of them.
    """
    from brain.retrieve.report import merge_sections

    return merge_sections(sections, Path(path) if path is not None else REPORT_PATH)


def checks(sections: dict[str, Any]) -> list[dict[str, Any]]:
    """The acceptance list of Task 2, as booleans over the numbers just measured."""
    g = sections["guard"]
    rk = sections["rerank"]
    bank = sections["cypher_examples"]
    s4 = bank["s4"]
    return [
        {
            "name": "every_write_or_injection_case_is_refused",
            "ok": g["blocked"]["refused"] == g["blocked"]["cases"] >= 25,
            "detail": f"{g['blocked']['refused']}/{g['blocked']['cases']} refused",
        },
        {
            "name": "refused_for_the_right_reason",
            "ok": g["blocked"]["refused_for_the_expected_reason"] == g["blocked"]["cases"],
            "detail": f"{g['blocked']['refused_for_the_expected_reason']}/{g['blocked']['cases']}",
        },
        {
            "name": "read_queries_still_run",
            "ok": g["allowed"]["passed"] == g["allowed"]["cases"] >= 10,
            "detail": f"{g['allowed']['passed']}/{g['allowed']['cases']} passed, "
            f"{g['allowed']['returned_rows']} returned rows",
        },
        {
            "name": "no_attack_left_a_node_behind",
            "ok": g["side_effects"]["nodes_created"] == 0 and not g["side_effects"]["evil_indexes"],
            "detail": json.dumps(g["side_effects"]["labels"]),
        },
        {
            "name": "timeout_is_enforced_by_the_server",
            "ok": bool(g["timeout"]["refused"]),
            "detail": f"{g['timeout']['elapsed_ms']}ms for a {g['timeout']['budget_s']}s budget",
        },
        {
            "name": "every_rejection_is_logged_with_a_reason",
            "ok": g["logging"]["rejections_logged"] >= g["logging"]["rejections_expected"]
            and g["logging"]["every_rejection_carries_a_reason"],
            "detail": f"{g['logging']['rejections_logged']} logged, "
            f"{g['logging']['rejections_expected']} expected",
        },
        {
            "name": "the_plan_layer_refuses_a_write_on_its_own",
            "ok": g["plan_only"]["refused"] == g["plan_only"]["cases"] > 0,
            "detail": f"{g['plan_only']['refused']}/{g['plan_only']['cases']} refused by EXPLAIN "
            "with the deny-list bypassed",
        },
        {
            "name": "three_to_five_unique_examples_per_type",
            "ok": not bank["thin_types"] and not bank["duplicates"],
            "detail": f"{bank['unique_per_type']}"
            + (f", thin: {bank['thin_types']}" if bank["thin_types"] else "")
            + (f", duplicates: {len(bank['duplicates'])}" if bank["duplicates"] else ""),
        },
        {
            "name": "every_bank_example_returns_at_least_one_row",
            "ok": bank["returning_at_least_one_row"] == bank["bank_size"] > 0,
            "detail": f"{bank['returning_at_least_one_row']}/{bank['bank_size']}",
        },
        {
            "name": "rerank_effect_is_measured",
            "ok": bool(rk.get("available")) and bool(rk.get("questions")),
            "detail": (
                f"top-1 changed on {rk['summary']['top1_changed']}/"
                f"{rk['summary']['questions']} questions"
                if rk.get("available")
                else str(rk.get("error"))
            ),
        },
        {
            "name": "s4_answers_the_aggregation_questions",
            "ok": bool(s4) and all(r.get("mode_a", {}).get("rows") for r in s4),
            "detail": ", ".join(
                f"{r['id']}: {r.get('mode_a', {}).get('rows', 0)} rows in "
                f"{r.get('mode_a', {}).get('p50_ms', 0)}ms"
                for r in s4
            ),
        },
    ]


def run(
    ctx: RetrieveContext,
    *,
    questions_path: Path | None = None,
    report_path: Path | None = None,
    repeats: int = REPEATS,
    echo=lambda _m: None,
) -> tuple[dict[str, Any], Path]:
    """Measure the guard, the reranker and the bank, and merge the three sections."""
    rows = competency.read(questions_path)
    echo(f"guard: {len(BLOCKED)} write/injection cases, {len(ALLOWED)} read cases …")
    sections: dict[str, Any] = {"guard": guard_section(ctx)}
    echo(f"rerank: S1 over {len(rows)} competency questions, with and without …")
    sections["rerank"] = rerank_section(ctx, rows)
    echo("cypher examples: validating the bank and running S4 …")
    sections["cypher_examples"] = examples_section(ctx, rows, repeats=repeats)
    sections["cypher_examples"]["checks"] = checks(sections)
    return sections, merge(sections, report_path)
