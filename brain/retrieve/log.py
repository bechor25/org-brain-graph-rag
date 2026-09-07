"""One JSONL line per retrieval call, from Python and from MCP alike (plan decision 3).

This is the file that makes Plan 3 possible. "Which strategy answers rationale questions
best" is not a feeling; it is a group-by over `strategy` and `question` with `latency_ms`
and `hit_ids` in the rows. And when an agent later cites `[KAFKA-15123]`, the trace says
which call put that key in its context and with which Cypher.

Writing the log never fails a retrieval. A full disk or a read-only mount is a reason to
lose a trace line, not a reason for `brain ask` to raise — so the writer swallows its own
errors and records the last one for `brain doctor` to notice.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.retrieve.types import Item, Result

DEFAULT_LOG = Path("data/logs/retrieval.jsonl")

_lock = threading.Lock()
#: Last write failure, for diagnostics. `None` while the log is healthy.
last_error: str | None = None


def hit_ids(items: list[Item]) -> list[str]:
    """The keys this call put in the agent's context, in the order it returned them."""
    return [f"{i.kind}:{i.key}" for i in items]


def entry(
    question: str,
    result: Result,
    *,
    mode: str = "python",
    tokens_out: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The trace record. Spec §4: question, strategy, cypher, latency_ms, hit_ids, tokens_out."""
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "question": question,
        "strategy": result.strategy,
        "cypher": result.cypher_used,
        "latency_ms": result.latency_ms,
        "hit_ids": hit_ids(result.items),
        "tokens_out": tokens_out,
        "mode": mode,
        "truncated": result.truncated,
    }
    if result.route:
        record["route"] = result.route
    if extra:
        record.update(extra)
    return record


def append(record: dict[str, Any], path: Path | None = None) -> bool:
    """Append one record. Returns whether it landed; never raises.

    `O_APPEND` on a line short enough to be atomic is what lets the MCP server, `brain ask`
    and the evaluation harness share one file without a lock between processes.
    """
    global last_error
    target = Path(path) if path is not None else DEFAULT_LOG
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    try:
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        last_error = None
        return True
    except OSError as exc:  # a full disk is not a retrieval failure
        last_error = f"{type(exc).__name__}: {exc}"
        return False


def log_call(
    question: str,
    result: Result,
    *,
    mode: str = "python",
    tokens_out: int = 0,
    path: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = entry(question, result, mode=mode, tokens_out=tokens_out, extra=extra)
    append(record, path)
    return record


def read_log(path: Path | None = None) -> list[dict[str, Any]]:
    """Every trace line, skipping any the writer left half-written. For tests and reports."""
    target = Path(path) if path is not None else DEFAULT_LOG
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
