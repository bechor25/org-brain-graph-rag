"""The cost row of spec §5.2 — latency, context tokens, tool calls, Cypher, agent time.

Kept apart from `metrics.py` because cost is the one table that has to span both run modes.
Mode A (Task 2) spends one tool call per question and no agent time; mode B (Task 3) spends
an unknown number of both, and the report compares them in the same units. A cost table
built inside the fixed-mode runner would have to be rebuilt to say that, so it is built from
records here instead, over whatever fields a mode fills in.

Two honesty rules the shape enforces:

* **`agent_time_ms` is `None`, not `0`, in mode A.** Nothing dispatched an agent, so there is
  no measurement — and a zero in a comparison table reads as "free", which is a claim.
* **`tool_calls` counts calls, not strategies.** In mode A that is 1 per run by construction;
  writing the constant into the record rather than assuming it here is what lets a strategy
  that one day makes two calls report two.
"""

from __future__ import annotations

from typing import Any

from brain.eval.metrics import percentile

#: What a cost row carries, in the order a table prints it.
COLUMNS: tuple[str, ...] = (
    "runs",
    "na",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_total_ms",
    "context_tokens_p50",
    "context_tokens_total",
    "tool_calls",
    "cypher_total",
    "cypher_p50",
    "agent_time_ms",
)


def _values(records: list[dict[str, Any]], field: str) -> list[int]:
    out: list[int] = []
    for record in records:
        value = record.get(field)
        if value is not None:
            out.append(int(value))
    return out


def row(records: list[dict[str, Any]]) -> dict[str, Any]:
    """One cost row over the runs that actually ran; `n/a` records count only in `na`."""
    ok = [r for r in records if r.get("status") == "ok"]
    latencies = _values(ok, "latency_ms")
    tokens = _values(ok, "context_tokens")
    cyphers = _values(ok, "cypher_count")
    calls = _values(ok, "tool_calls")
    agent = _values(ok, "agent_time_ms")
    return {
        "runs": len(ok),
        "na": len(records) - len(ok),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "latency_total_ms": sum(latencies),
        "context_tokens_p50": percentile(tokens, 50),
        "context_tokens_total": sum(tokens),
        "tool_calls": sum(calls) if calls else len(ok),
        "cypher_total": sum(cyphers),
        "cypher_p50": percentile(cyphers, 50),
        # None, not 0: mode A dispatches no agent, so there is nothing to report.
        "agent_time_ms": percentile(agent, 50) if agent else None,
    }


def table(records: list[dict[str, Any]], *, by: str = "strategy") -> dict[str, dict[str, Any]]:
    """`{strategy: cost row}` — or by any other field a mode groups its records on."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get(by) or ""), []).append(record)
    return {name: row(rows) for name, rows in sorted(grouped.items())}


def lines(costs: dict[str, dict[str, Any]]) -> list[str]:
    """The cost table as fixed-width text, for the CLI summary."""
    head = (
        f"{'strategy':<9}{'runs':>5}{'n/a':>5}{'p50ms':>8}{'p95ms':>8}{'tok p50':>9}{'cypher':>8}"
    )
    out = [head, "-" * len(head)]
    for name, values in costs.items():
        out.append(
            f"{name:<9}{values['runs']:>5}{values['na']:>5}"
            f"{_num(values['latency_p50_ms']):>8}{_num(values['latency_p95_ms']):>8}"
            f"{_num(values['context_tokens_p50']):>9}{values['cypher_total']:>8}"
        )
    return out


def _num(value: Any) -> str:
    return "-" if value is None else str(value)
