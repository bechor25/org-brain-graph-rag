"""Layer 2 of spec §5.2 — what retrieval found, measured without a judge.

The whole value of Plan 3 rests on one rule from the conventions: *never report an answer
score without its retrieval score*. This module is the retrieval score. It takes the packed
`Result` a strategy returned and the `gold_evidence` the question set declared, and it
answers four questions arithmetically:

* **Did the gold evidence come back at all?** `recall`, and `hit@k` for the ranks an agent
  actually reads.
* **How much of the context was worth its tokens?** `precision` — items with at least one
  gold match over items returned.
* **What did it cost?** latency p50/p95, context tokens, Cypher statements.
* **Where did a strategy not even apply?** `na`, with the reason, because an empty cell that
  does not say why is indistinguishable from a cell nobody ran.

### Why there are two recalls

`gold_evidence` mixes node keys (`KAFKA-14649`), chunk ids (40 hex) and `truth:<section>:<i>`
references into the synthetic truth file. S3 and S4 return *nodes*, so a node key is a
direct hit for them. S1 returns *chunks*, and the chunk whose `parent_key` is `KAFKA-14649`
is the raw text the gold fact was written in — it found the evidence, under a different id.
Counting that as a miss would hand the graph strategies a win the corpus did not give them,
which is exactly the leak the plan's decision 2 is written to prevent.

So a parent match counts in `recall` and is excluded from `recall_strict`, and every match
records which kind it was. Both numbers are published; neither is a choice made quietly.

### Why a pending question is not a zero

The 19 competency questions carried over from Plan 2 have `gold_source: pending` and no gold
yet. They still run — latency, tokens and Cypher counts are real measurements — but they are
counted as `pending` and left out of every recall average. A zero would be a claim about
retrieval; `pending` is a claim about the question set, which is the true one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

#: The ranks `hit@k` is reported at. 10 is the top of a default `k`; 1 and 3 are what an
#: agent reads before it stops reading.
HIT_KS: tuple[int, ...] = (1, 3, 5, 10)

#: A chunk id is `sha1(parent|kind|position|text)` (`brain/chunk/text.py`), so 40 hex chars.
#: A shorter hex string in `gold_evidence` is treated as a prefix of one — but only from this
#: length up: eight hex characters is where a prefix stops being a coincidence over a corpus
#: of ~14k chunks, and a six-character "prefix" would match by accident often enough to
#: inflate recall.
MIN_CHUNK_PREFIX = 8
CHUNK_ID_CHARS = 40

_HEX = re.compile(r"^[0-9a-f]+$")

#: Which fields of a `synthetic_truth.json` record name a graph key. The four sections the
#: question shapes draw on (`brain/eval/paths.py`) are spelled out, and anything else falls
#: back to the naming convention the generator uses (`*_key`, or a bare `key`). A record
#: whose keys we cannot name would silently score 0 recall for every strategy, so the
#: fallback is deliberately generous and `unresolved` reports when even it found nothing.
TRUTH_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "renames": ("test_key", "jira_key"),
    "text_only_links": ("from_key", "to_key"),
    "duplicate_tests": ("a", "b"),
    "stale_states": ("ado_key", "jira_key"),
}

#: Match kinds, strongest first. The first two are the item's *own* identity (a node key, a
#: chunk's id); `provenance` is a pointer the item carries; `chunk_prefix` is the same
#: pointer under a shortened gold id. `parent` is the only one outside `recall_strict`.
MATCH_KINDS: tuple[str, ...] = ("key", "chunk", "provenance", "chunk_prefix", "parent")
STRICT_KINDS: frozenset[str] = frozenset({"key", "chunk", "provenance", "chunk_prefix"})


# ----------------------------------------------------------------------------- gold references


@dataclass(frozen=True)
class GoldRef:
    """One entry of `gold_evidence`, and the strings a returned item must show to reach it."""

    ref: str
    kind: str  # "key" | "chunk" | "truth"
    targets: tuple[str, ...] = ()
    unresolved: bool = False


def _looks_like_chunk_id(value: str) -> bool:
    return (
        MIN_CHUNK_PREFIX <= len(value) <= CHUNK_ID_CHARS
        and bool(_HEX.match(value))
        # A pure-digit string is a number somebody wrote, not a sha1 prefix.
        and not value.isdigit()
    )


def truth_targets(truth: dict[str, Any], section: str, index: int) -> tuple[str, ...]:
    """The graph keys the synthetic-truth record at `<section>:<index>` names."""
    rows = truth.get(section)
    if not isinstance(rows, list) or not 0 <= index < len(rows):
        return ()
    row = rows[index]
    if not isinstance(row, dict):
        return ()
    fields = TRUTH_KEY_FIELDS.get(section)
    if fields is None:
        fields = tuple(k for k in row if k == "key" or k.endswith("_key"))
    out = [str(row[f]) for f in fields if isinstance(row.get(f), str) and row[f]]
    return tuple(dict.fromkeys(out))


def gold_refs(
    gold_evidence: list[str] | None, truth: dict[str, Any] | None = None
) -> list[GoldRef]:
    """Parse `gold_evidence` into references, de-duplicated so recall cannot exceed 1."""
    truth = truth or {}
    out: list[GoldRef] = []
    seen: set[str] = set()
    for raw in gold_evidence or []:
        ref = str(raw).strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        if ref.startswith("truth:"):
            _, _, rest = ref.partition("truth:")
            section, _, index = rest.rpartition(":")
            try:
                targets = truth_targets(truth, section, int(index))
            except ValueError:
                targets = ()
            out.append(GoldRef(ref, "truth", targets, unresolved=not targets))
        elif _looks_like_chunk_id(ref):
            out.append(GoldRef(ref, "chunk", (ref,)))
        else:
            out.append(GoldRef(ref, "key", (ref,)))
    return out


# ----------------------------------------------------------------------------- the matcher


@dataclass(frozen=True)
class ItemKeys:
    """Everything one returned item offers a gold reference to match against.

    `own_chunk` is kept apart from `provenance` on purpose: an item that *is* the gold chunk
    and an item that merely *cites* it are both hits, and the report should be able to say
    which — a chunk id a node carries in its provenance is a claim about where the node's
    text came from, not the text itself.
    """

    key: str = ""
    own_chunk: str = ""
    provenance: frozenset[str] = field(default_factory=frozenset)
    parents: frozenset[str] = field(default_factory=frozenset)

    @property
    def chunk_ids(self) -> frozenset[str]:
        return self.provenance | ({self.own_chunk} if self.own_chunk else frozenset())


def item_keys(item: dict[str, Any]) -> ItemKeys:
    """Read an item's identifiers out of the packed `Result` JSON, tolerating a thin one."""
    props = item.get("props") or {}
    key = str(item.get("key") or "")
    prov_ids: set[str] = set()
    parents: set[str] = set()
    for entry in item.get("provenance") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("chunk_id"):
            prov_ids.add(str(entry["chunk_id"]))
        if entry.get("source"):
            parents.add(str(entry["source"]))
    if isinstance(props, dict):
        for name in ("parent_key", "parent"):
            if props.get(name):
                parents.add(str(props[name]))
    own_chunk = key if item.get("kind") == "Chunk" else ""
    # A chunk stands for its parent, never for itself as a node key.
    parents.discard(key)
    return ItemKeys(
        key=key,
        own_chunk=own_chunk,
        provenance=frozenset(prov_ids),
        parents=frozenset(parents),
    )


def _prefix_hit(target: str, chunk_ids: frozenset[str]) -> bool:
    if len(target) >= CHUNK_ID_CHARS or len(target) < MIN_CHUNK_PREFIX:
        return False
    return any(cid.startswith(target) for cid in chunk_ids)


def match_kind(ref: GoldRef, keys: ItemKeys) -> str | None:
    """How this item reaches this gold reference, or `None`. Strongest kind wins."""
    for target in ref.targets:
        if keys.own_chunk and target == keys.own_chunk:
            return "chunk"
    for target in ref.targets:
        if keys.key and target == keys.key:
            return "key"
    for target in ref.targets:
        if target in keys.provenance:
            return "provenance"
    for target in ref.targets:
        if _prefix_hit(target, keys.chunk_ids):
            return "chunk_prefix"
    for target in ref.targets:
        if target in keys.parents:
            return "parent"
    return None


def score_items(
    items: list[dict[str, Any]],
    refs: list[GoldRef],
    *,
    ks: tuple[int, ...] = HIT_KS,
) -> dict[str, Any]:
    """Recall, strict recall, context precision and hit@k for one packed context.

    `recall` is measured over the *whole* returned context, because the whole context is
    what the answering agent is handed; `hit@k` is what says whether the evidence was near
    the top or buried at rank nine.
    """
    parsed = [item_keys(i) for i in items]
    if not refs:
        return {
            "pending": True,
            "gold": 0,
            "gold_unresolved": 0,
            "matched": 0,
            "matched_strict": 0,
            "recall": None,
            "recall_strict": None,
            "precision": None,
            "hit": None,
            "hit_at": {str(k): None for k in ks},
            "items": len(items),
            "matches": [],
            "matched_refs": [],
        }

    matches: list[dict[str, Any]] = []
    first_rank: dict[str, int] = {}
    hit_kinds: dict[str, str] = {}
    items_with_gold: set[int] = set()
    for rank, keys in enumerate(parsed, start=1):
        for ref in refs:
            kind = match_kind(ref, keys)
            if kind is None:
                continue
            items_with_gold.add(rank)
            matches.append({"ref": ref.ref, "kind": kind, "rank": rank, "item_key": keys.key})
            if ref.ref not in first_rank:
                first_rank[ref.ref] = rank
                hit_kinds[ref.ref] = kind
            elif MATCH_KINDS.index(kind) < MATCH_KINDS.index(hit_kinds[ref.ref]):
                hit_kinds[ref.ref] = kind

    gold = len(refs)
    matched = len(first_rank)
    matched_strict = sum(1 for ref, kind in hit_kinds.items() if kind in STRICT_KINDS)
    return {
        "pending": False,
        "gold": gold,
        "gold_unresolved": sum(1 for r in refs if r.unresolved),
        "matched": matched,
        "matched_strict": matched_strict,
        "recall": round(matched / gold, 4),
        "recall_strict": round(matched_strict / gold, 4),
        "precision": round(len(items_with_gold) / len(items), 4) if items else 0.0,
        "hit": matched > 0,
        "hit_at": {str(k): any(r <= k for r in first_rank.values()) for k in ks},
        "items": len(items),
        "matches": matches,
        "matched_refs": sorted(first_rank),
    }


# ----------------------------------------------------------------------------- records


def is_pending(record: dict[str, Any]) -> bool:
    """A question whose gold the planner still owes — run, measured, not scored."""
    return record.get("gold_source") == "pending" or not record.get("gold_evidence")


def score_records(
    records: list[dict[str, Any]],
    truth: dict[str, Any] | None = None,
    *,
    ks: tuple[int, ...] = HIT_KS,
) -> list[dict[str, Any]]:
    """Attach a `score` block to every run record. `n/a` records carry no score."""
    out: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        if row.get("status") != "ok":
            row["score"] = None
            out.append(row)
            continue
        items = ((row.get("result") or {}).get("items")) or []
        refs = [] if is_pending(row) else gold_refs(row.get("gold_evidence"), truth)
        row["score"] = score_items(items, refs, ks=ks)
        out.append(row)
    return out


# ----------------------------------------------------------------------------- aggregation


def percentile(values: list[int] | list[float], p: float) -> int | float | None:
    """Nearest-rank percentile: every published number is a value something measured.

    `statistics.median` averages the two middle samples on an even `n`, which invents a
    latency no call ever took. For a cost table that is read as "how slow is this tool", a
    real sample is the honest answer.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def cell(records: list[dict[str, Any]], *, ks: tuple[int, ...] = HIT_KS) -> dict[str, Any]:
    """One matrix cell: what ran, what it found, what it cost, and what did not apply."""
    ok = [r for r in records if r.get("status") == "ok"]
    na = [r for r in records if r.get("status") != "ok"]
    scored = [r for r in ok if r.get("score") and not r["score"]["pending"]]
    pending = [r for r in ok if r.get("score") and r["score"]["pending"]]

    latencies = [int(r.get("latency_ms") or 0) for r in ok]
    tokens = [int(r.get("context_tokens") or 0) for r in ok]
    cyphers = [int(r.get("cypher_count") or 0) for r in ok]
    reasons: dict[str, int] = {}
    for r in na:
        reasons[str(r.get("reason") or "unspecified")] = (
            reasons.get(str(r.get("reason") or "unspecified"), 0) + 1
        )

    return {
        "n": len(records),
        "ok": len(ok),
        "na": len(na),
        "na_reasons": reasons,
        "scored": len(scored),
        "pending": len(pending),
        "recall": _mean([r["score"]["recall"] for r in scored]),
        "recall_strict": _mean([r["score"]["recall_strict"] for r in scored]),
        "precision": _mean([r["score"]["precision"] for r in scored]),
        "hit_at": {
            str(k): _mean([1.0 if r["score"]["hit_at"][str(k)] else 0.0 for r in scored])
            for k in ks
        },
        "items_p50": percentile([int(r.get("items") or 0) for r in ok], 50),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "context_tokens_p50": percentile(tokens, 50),
        "context_tokens_total": sum(tokens),
        "cypher_total": sum(cyphers),
        "cypher_mean": _mean([float(c) for c in cyphers]),
    }


def _group(records: list[dict[str, Any]], *keys: str) -> dict[tuple[str, ...], list[dict]]:
    out: dict[tuple[str, ...], list[dict]] = {}
    for record in records:
        out.setdefault(tuple(str(record.get(k) or "") for k in keys), []).append(record)
    return out


def by_strategy(
    records: list[dict[str, Any]], *, ks: tuple[int, ...] = HIT_KS
) -> dict[str, dict[str, Any]]:
    grouped = _group(records, "strategy")
    return {key[0]: cell(rows, ks=ks) for key, rows in sorted(grouped.items())}


def by_type(
    records: list[dict[str, Any]], *, ks: tuple[int, ...] = HIT_KS
) -> dict[str, dict[str, Any]]:
    grouped = _group(records, "type")
    return {key[0]: cell(rows, ks=ks) for key, rows in sorted(grouped.items())}


def matrix(
    records: list[dict[str, Any]], *, ks: tuple[int, ...] = HIT_KS
) -> dict[str, dict[str, dict[str, Any]]]:
    """Strategy × question type — the learning product of Plan 3 (spec §5.3)."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for (strategy, qtype), rows in sorted(_group(records, "strategy", "type").items()):
        out.setdefault(strategy, {})[qtype] = cell(rows, ks=ks)
    return out


def question_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per question, the strategies side by side — the report's detail table."""
    out: list[dict[str, Any]] = []
    for (qid,), rows in sorted(_group(records, "qid").items()):
        head = rows[0]
        row: dict[str, Any] = {
            "qid": qid,
            "type": head.get("type"),
            "lang": head.get("lang"),
            "gold_source": head.get("gold_source"),
            "gold_evidence": head.get("gold_evidence") or [],
            "expected_strategy": head.get("expected_strategy"),
            "route": (head.get("route") or {}).get("strategy"),
            "strategies": {},
        }
        if head.get("pair"):
            row["pair"] = head["pair"]
        for record in sorted(rows, key=lambda r: str(r.get("strategy"))):
            score = record.get("score") or {}
            row["strategies"][str(record.get("strategy"))] = {
                "status": record.get("status"),
                "reason": record.get("reason"),
                "executed": record.get("executed"),
                "items": record.get("items"),
                "recall": score.get("recall"),
                "recall_strict": score.get("recall_strict"),
                "precision": score.get("precision"),
                "hit": score.get("hit"),
                "matched_refs": score.get("matched_refs"),
                "latency_ms": record.get("latency_ms"),
                "context_tokens": record.get("context_tokens"),
                "cypher_count": record.get("cypher_count"),
            }
        out.append(row)
    return out


def returned_keys(record: dict[str, Any]) -> list[str]:
    """What this run put in the agent's context, as `kind:key` — the log's `hit_ids` shape."""
    items = ((record.get("result") or {}).get("items")) or []
    return [f"{i.get('kind')}:{i.get('key')}" for i in items]


def cross_lingual(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hebrew rows next to their English twins, by `pair` (spec §5: cross-lingual).

    `key_jaccard` is the overlap of the two contexts' keys. bge-m3 being multilingual is a
    claim; two top-k windows over one corpus overlapping is a measurement — the same one
    `brain/retrieve/report.py` makes for Plan 2, here per strategy.
    """
    pairs: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for record in records:
        tag = record.get("pair")
        lang = record.get("lang")
        if not tag or lang not in ("en", "he"):
            continue
        pairs.setdefault(str(tag), {}).setdefault(str(record.get("strategy")), {}).setdefault(
            str(lang), []
        ).append(record)

    out: list[dict[str, Any]] = []
    for tag, strategies in sorted(pairs.items()):
        block: dict[str, Any] = {}
        for strategy, langs in sorted(strategies.items()):
            if not (langs.get("en") and langs.get("he")):
                continue
            en, he = langs["en"][0], langs["he"][0]
            en_keys, he_keys = set(returned_keys(en)), set(returned_keys(he))
            union = en_keys | he_keys
            block[strategy] = {
                "en": _side(en),
                "he": _side(he),
                "shared_keys": sorted(en_keys & he_keys)[:10],
                "key_jaccard": round(len(en_keys & he_keys) / len(union), 4) if union else None,
            }
        if block:
            out.append({"pair": tag, "strategies": block})
    return out


def _side(record: dict[str, Any]) -> dict[str, Any]:
    score = record.get("score") or {}
    return {
        "qid": record.get("qid"),
        "status": record.get("status"),
        "items": record.get("items"),
        "recall": score.get("recall"),
        "precision": score.get("precision"),
        "hit": score.get("hit"),
        "latency_ms": record.get("latency_ms"),
        "context_tokens": record.get("context_tokens"),
    }
