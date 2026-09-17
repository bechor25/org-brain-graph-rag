"""A local cross-encoder in front of the answer, and a promise that it can be missing.

A bi-encoder (`bge-m3`) embeds the question and the chunk separately and compares two
vectors; it never sees the pair. A cross-encoder reads question and chunk *together* and
scores the pair, which is strictly more information and strictly more expensive — which is
why the shape is always "retrieve `k` cheaply, then rerank those `k`", never "rerank the
corpus". `BAAI/bge-reranker-v2-m3` is the multilingual sibling of the embedding model
already in use, so a Hebrew question and an English chunk stay comparable (plan decision 5).

The hard requirement is the fallback. `sentence-transformers` is an optional extra
(`uv sync --extra local-embed`) that drags in torch, and the model is a download. On a
machine without either, `rerank=True` must produce a *warning and the original order*, not
a stack trace: the evaluation in Plan 3 measures with and without rerank, and an evaluation
that dies on the machine without the extra measures nothing. Every failure — import,
download, inference — lands in `status()['error']`, which `brain doctor` prints as an
optional check.

Loading is lazy and cached for the process: the model is ~2 GB and several seconds of
startup, and paying that per question would make the "with rerank" latency column a
measurement of disk speed.
"""

from __future__ import annotations

import importlib.util
import math
import os
import time
import warnings
from pathlib import Path
from typing import Any

#: Plan decision 5. Multilingual, and the same family as the embedder.
MODEL_NAME = "BAAI/bge-reranker-v2-m3"
#: How much of a chunk the cross-encoder reads. bge-reranker-v2-m3 takes 8192 tokens; the
#: chunks are ~600 characters after packing, so this only bounds a pathological input.
MAX_TEXT_CHARS = 4000

_MODEL: Any | None = None
_LOADED = False
_ERROR: str | None = None
_LOAD_MS = 0
_WARNED = False


def _warn(message: str) -> None:
    """Say it once. A warning per question is noise that trains people to ignore warnings."""
    global _WARNED
    if not _WARNED:
        warnings.warn(f"rerank disabled: {message}", RuntimeWarning, stacklevel=3)
        _WARNED = True


def load_model(name: str = MODEL_NAME) -> Any | None:
    """The CrossEncoder, or `None` with the reason recorded in `status()`."""
    global _MODEL, _LOADED, _ERROR, _LOAD_MS
    if _LOADED:
        return _MODEL
    _LOADED = True
    started = time.perf_counter()
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:  # noqa: BLE001 - the extra is optional by design
        _ERROR = f"sentence-transformers is not installed ({exc}); uv sync --extra local-embed"
        _warn(_ERROR)
        return None
    try:
        _MODEL = CrossEncoder(name)
    except Exception as exc:  # noqa: BLE001 - a model that will not download is not a crash
        _ERROR = f"could not load {name}: {exc}"
        _warn(_ERROR)
        _MODEL = None
        return None
    _LOAD_MS = int((time.perf_counter() - started) * 1000)
    return _MODEL


def available() -> bool:
    return load_model() is not None


def status() -> dict[str, Any]:
    """What `brain doctor` prints and the report records."""
    model = load_model()
    return {
        "model": MODEL_NAME,
        "available": model is not None,
        "load_ms": _LOAD_MS,
        "error": _ERROR,
    }


def cache_dir(name: str = MODEL_NAME) -> Path | None:
    """Where Hugging Face put the weights, or `None` if they have not been downloaded."""
    home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    hub = Path(os.environ.get("HUGGINGFACE_HUB_CACHE") or home / "hub")
    folder = hub / ("models--" + name.replace("/", "--"))
    snapshots = folder / "snapshots"
    if not snapshots.is_dir():
        return None
    return next((p for p in snapshots.iterdir() if p.is_dir()), None)


def probe(name: str = MODEL_NAME) -> dict[str, Any]:
    """Is the reranker usable — without paying the several seconds it takes to find out.

    `brain doctor` runs inside `make smoke`, and importing torch to load a 2 GB checkpoint
    to print one line would make the health check the slowest thing in the pipeline. So the
    cheap facts are checked instead: is the optional extra installed, and are the weights in
    the cache. When the model is already loaded in this process, the real status wins.
    """
    if _LOADED and _MODEL is not None:
        return status()
    installed = importlib.util.find_spec("sentence_transformers") is not None
    cached = cache_dir(name)
    error = None
    if not installed:
        error = "sentence-transformers is not installed (uv sync --extra local-embed)"
    elif cached is None:
        error = f"{name} is not in the Hugging Face cache; it downloads on first use"
    return {
        "model": name,
        "available": installed and cached is not None,
        "installed": installed,
        "cached": cached is not None,
        "load_ms": _LOAD_MS,
        "error": error,
    }


def reset() -> None:
    """Forget the loaded model — for tests that simulate a machine without the extra."""
    global _MODEL, _LOADED, _ERROR, _LOAD_MS, _WARNED
    _MODEL, _LOADED, _ERROR, _LOAD_MS, _WARNED = None, False, None, 0, False


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if -60 < x < 60 else (0.0 if x <= 0 else 1.0)


def score_pairs(query: str, texts: list[str]) -> list[float] | None:
    """Cross-encoder relevance for `(query, text)` pairs, mapped into (0, 1), or `None`.

    Some builds of the model return logits and some return probabilities depending on the
    activation the checkpoint declares. Squashing only when the values fall outside (0, 1)
    keeps both cases monotonic and keeps the score in the same range as an RRF score, which
    is what the packer sorts by.
    """
    model = load_model()
    if model is None or not texts:
        return None
    pairs = [(query, (text or "")[:MAX_TEXT_CHARS]) for text in texts]
    try:
        raw = model.predict(pairs)
    except Exception as exc:  # noqa: BLE001 - inference failure is a fallback, not a crash
        global _ERROR
        _ERROR = f"rerank inference failed: {exc}"
        _warn(_ERROR)
        return None
    scores = [float(s) for s in raw]
    if scores and (min(scores) < 0.0 or max(scores) > 1.0):
        scores = [_sigmoid(s) for s in scores]
    return scores


def rerank_rows(
    query: str, rows: list[dict[str, Any]], *, top_k: int | None = None
) -> list[dict[str, Any]]:
    """Reorder `[{node, score}]` (S1's shape) by cross-encoder score. Unchanged on failure.

    The row keeps its original fusion score in `rrf_score` so a report can say what the
    rerank actually moved — "top-1 changed" is only a meaningful number if both orders are
    recoverable from the same object.
    """
    if not rows:
        return rows
    texts = [str((row.get("node") or {}).get("text", "")) for row in rows]
    scores = score_pairs(query, texts)
    if scores is None:
        return rows
    reranked = []
    for row, score in zip(rows, scores, strict=False):
        updated = dict(row)
        updated["rrf_score"] = row.get("score")
        updated["score"] = score
        reranked.append(updated)
    reranked.sort(key=lambda r: -r["score"])
    return reranked[:top_k] if top_k else reranked


def rerank_items(query: str, items: list[Any], *, top_k: int | None = None) -> list[Any]:
    """The same, for a packed `Item` list: score against `snippet`, rewrite `score`."""
    if not items:
        return items
    scores = score_pairs(query, [f"{i.title} {i.snippet}".strip() for i in items])
    if scores is None:
        return items
    for item, score in zip(items, scores, strict=False):
        item.props["rrf_score"] = item.score
        item.score = score
    ordered = sorted(items, key=lambda i: -i.score)
    return ordered[:top_k] if top_k else ordered
