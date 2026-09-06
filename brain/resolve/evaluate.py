"""`brain resolve eval` — precision, recall and F1 per kind, per tier, against the gold.

Two things this refuses to do.

It does not grade what it cannot grade. The gold covers the synthetic identity map and
the hard negatives around it; a merge of `jira:jrao` with `git:junrao@…` is almost
certainly right and appears in no gold row, so it is counted as `ungraded_merges` and
kept out of precision entirely. Quietly calling those false positives would understate
precision; quietly calling them true positives would invent recall.

It does not read the graph. Predictions come from `data/canonical/resolution_ledger.json`,
which is where the merges are recorded — so the same numbers come out whether or not the
merged nodes still exist, and a tier can be scored on its own by replaying only the
ledger rows that tier wrote.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.resolve.gold import GOLD_NAME, read_gold
from brain.resolve.ledger import ResolutionLedger

REPORT_NAME = "resolve.json"
TIERS: tuple[int, ...] = (1, 2, 3)
#: The course target the brief holds resolution to, after tier 3.
TARGET = 0.85


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def same(self, a: str, b: str) -> bool:
        return a in self.parent and b in self.parent and self.find(a) == self.find(b)

    def components(self) -> list[list[str]]:
        groups: dict[str, list[str]] = {}
        for node in self.parent:
            groups.setdefault(self.find(node), []).append(node)
        return [sorted(v) for v in groups.values() if len(v) > 1]


def predicted(ledger: ResolutionLedger, kind: str, *, max_tier: int) -> _Union:
    """Replay only the ledger rows written at or below `max_tier`.

    `via` and not `canonical`: `canonical` is path-compressed, so an identity tier 1
    merged into a node tier 2 later moved would look like a tier-1 merge into the final
    survivor. `via` is the hop that tier actually made.
    """
    union = _Union()
    for identity, entry in ledger.section(kind).items():
        tier = entry.get("tier")
        if tier is None or int(tier) > max_tier:
            continue
        union.union(identity, str(entry.get("via") or entry["canonical"]))
    return union


def score(gold: Sequence[dict[str, Any]], union: _Union) -> dict[str, Any]:
    """P/R/F1 over the labelled pairs only, plus what the labels never covered."""
    tp = fp = fn = tn = 0
    false_positives: list[dict[str, Any]] = []
    false_negatives: list[dict[str, Any]] = []
    for row in gold:
        merged = union.same(row["a"], row["b"])
        if row["label"] == "same":
            if merged:
                tp += 1
            else:
                fn += 1
                false_negatives.append(row)
        else:
            if merged:
                fp += 1
                false_positives.append(row)
            else:
                tn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    graded = {(r["a"], r["b"]) for r in gold}
    merged_pairs = {
        tuple(sorted(p)) for g in union.components() for p in itertools.combinations(g, 2)
    }
    return {
        "gold_pairs": len(gold),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "meets_target": bool(
            precision is not None
            and recall is not None
            and precision >= TARGET
            and recall >= TARGET
        ),
        "merged_pairs": len(merged_pairs),
        "ungraded_merges": len(merged_pairs - graded),
        "false_positives": false_positives[:25],
        "false_negatives": false_negatives[:25],
    }


def run_eval(
    *,
    canonical_dir: Path,
    eval_dir: Path,
    reports_dir: Path,
    kinds: Sequence[str],
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    ledger = ResolutionLedger.load(canonical_dir)
    gold_path = eval_dir / GOLD_NAME
    gold = read_gold(gold_path)
    results: dict[str, Any] = {}
    for kind in kinds:
        rows = [r for r in gold if r.get("kind") == kind]
        per_tier = {
            f"through_tier_{t}": score(rows, predicted(ledger, kind, max_tier=t)) for t in TIERS
        }
        results[kind] = {
            "gold_path": str(gold_path),
            "gold_pairs": len(rows),
            "positives": sum(1 for r in rows if r["label"] == "same"),
            "negatives": sum(1 for r in rows if r["label"] == "different"),
            **per_tier,
        }
        for name, stats in per_tier.items():
            echo(
                f"{kind} {name}: P={stats['precision']} R={stats['recall']} F1={stats['f1']} "
                f"(tp={stats['tp']} fp={stats['fp']} fn={stats['fn']}, "
                f"ungraded merges {stats['ungraded_merges']})"
            )

    section = {
        "eval": {
            "generated_at": utc_now_iso(),
            "target": TARGET,
            "ledger_available": ledger.available,
            "note": (
                "Precision and recall are computed over the labelled gold pairs only. "
                "Merges between identities the gold says nothing about (the real Jira / git "
                "/ Confluence duplicates) are counted as `ungraded_merges` and left out of "
                "both, because calling them right or wrong would be a guess."
            ),
            **results,
        }
    }
    if write_report:
        path = reports_dir / REPORT_NAME
        existing: dict[str, Any] = {}
        if path.exists():
            import json

            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        existing.update(section)
        write_json_atomic(path, existing)
    return section, 0
