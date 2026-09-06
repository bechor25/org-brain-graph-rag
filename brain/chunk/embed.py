"""Embedding: the measurement first, then the run that uses what it measured.

`brain chunk --measure` exists because "batches of 64" (spec §3.4) is a guess until
somebody times it on 600-token paragraphs rather than on sentences. It embeds the same
200 real chunks at four batch sizes and reports tokens/s and s/batch for each; the full
run then takes its batch size and its HTTP timeout from that table.

The real token counts come from `OllamaEmbedder.prompt_tokens` — `bge-m3`'s own tokenizer,
via the `prompt_eval_count` Ollama returns on every embed. That is what calibrates the
chunker's `chars/4` estimate, and it is why this module has no HTTP code of its own: there
is exactly one embedding path in this codebase and it is `brain/embed/client.py`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from brain.embed.client import OllamaEmbedder

#: Batch sizes the measurement compares (brief §06 decision 4).
MEASURE_BATCHES: tuple[int, ...] = (8, 16, 32, 64)
MEASURE_SAMPLE = 200
#: A batch must not be able to time out on a slow first token: the chosen timeout is this
#: multiple of the slowest batch the measurement saw, never below the floor.
TIMEOUT_SAFETY = 5
TIMEOUT_FLOOR_S = 60


@dataclass
class Measurement:
    batch_size: int
    chunks: int
    seconds: float
    real_tokens: int
    est_tokens: int

    @property
    def batches(self) -> int:
        return math.ceil(self.chunks / self.batch_size)

    def row(self) -> dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "chunks": self.chunks,
            "batches": self.batches,
            "seconds": round(self.seconds, 2),
            "s_per_batch": round(self.seconds / max(self.batches, 1), 3),
            "chunks_per_s": round(self.chunks / self.seconds, 1) if self.seconds else 0.0,
            "real_tokens": self.real_tokens,
            "tokens_per_s": round(self.real_tokens / self.seconds) if self.seconds else 0,
            "est_tokens_per_s": round(self.est_tokens / self.seconds) if self.seconds else 0,
        }


@dataclass
class Throughput:
    """The measurement table and the two numbers the full run reads off it."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    batch_size: int = 32
    timeout_s: int = TIMEOUT_FLOOR_S
    sample_chunks: int = 0
    sample_est_tokens: int = 0
    sample_real_tokens: int = 0

    @property
    def chars_per_token(self) -> float | None:
        """Measured `chars/token`, against which `CHARS_PER_TOKEN = 4` is a guess."""
        if not self.sample_real_tokens:
            return None
        return round(self.sample_est_tokens * 4 / self.sample_real_tokens, 3)

    def report(self) -> dict[str, Any]:
        return {
            "sample_chunks": self.sample_chunks,
            "table": self.rows,
            "chosen_batch_size": self.batch_size,
            "chosen_timeout_s": self.timeout_s,
            "estimate_calibration": {
                "est_tokens": self.sample_est_tokens,
                "real_tokens": self.sample_real_tokens,
                "measured_chars_per_token": self.chars_per_token,
                "assumed_chars_per_token": 4,
                "note": "Ollama 0.32.6 has no /api/tokenize; the real count is the "
                "`prompt_eval_count` bge-m3 reports for the text it just embedded.",
            },
        }


def merge_tables(previous: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> list:
    """One cumulative table across measure runs, keyed by (batch size, sample size).

    The 200-chunk comparison of 8/16/32/64 and a single full-corpus pass answer different
    questions — "which batch size" and "how long will the whole thing take" — and both
    belong in the report. Re-running either replaces its own rows and keeps the other's.
    """
    merged = {(r["batch_size"], r["chunks"]): r for r in previous}
    merged.update({(r["batch_size"], r["chunks"]): r for r in rows})
    return [merged[k] for k in sorted(merged)]


def sample_chunks(chunks: Sequence, n: int = MEASURE_SAMPLE) -> list:
    """`n` chunks spread evenly through the corpus order, not the first `n`.

    The first 200 chunks of this corpus are all KIP sections; a batch size chosen on them
    would be tuned for the longest texts in the slice and wrong for the 4,150 commit
    messages that follow.
    """
    if len(chunks) <= n:
        return list(chunks)
    step = len(chunks) / n
    return [chunks[int(i * step)] for i in range(n)]


def measure(
    embedder: OllamaEmbedder,
    chunks: Sequence,
    *,
    batches: Sequence[int] = MEASURE_BATCHES,
    sample_size: int = MEASURE_SAMPLE,
    echo: Callable[[str], None] = print,
) -> Throughput:
    """Time the same sample at every batch size. Vectors are thrown away; this writes
    nothing to the graph, so it is safe to run over the whole corpus."""
    sample = sample_chunks(chunks, sample_size)
    texts = [c.text for c in sample]
    est = sum(c.token_est for c in sample)
    results: list[Measurement] = []
    real_tokens = 0
    for batch_size in batches:
        before_tokens, before_requests = embedder.prompt_tokens, embedder.requests
        started = time.perf_counter()
        embedder.embed(texts, batch_size=batch_size)
        seconds = time.perf_counter() - started
        tokens = embedder.prompt_tokens - before_tokens
        real_tokens = tokens
        m = Measurement(batch_size, len(sample), seconds, tokens, est)
        results.append(m)
        echo(
            f"  batch {batch_size:>3}: {m.row()['seconds']}s total, "
            f"{m.row()['s_per_batch']}s/batch, {m.row()['tokens_per_s']} tokens/s "
            f"({embedder.requests - before_requests} requests)"
        )
    best = max(results, key=lambda m: m.real_tokens / m.seconds if m.seconds else 0)
    slowest_batch = max(m.seconds / max(m.batches, 1) for m in results)
    timeout = max(TIMEOUT_FLOOR_S, math.ceil(slowest_batch * TIMEOUT_SAFETY))
    return Throughput(
        rows=[m.row() for m in results],
        batch_size=best.batch_size,
        timeout_s=timeout,
        sample_chunks=len(sample),
        sample_est_tokens=est,
        sample_real_tokens=real_tokens,
    )
