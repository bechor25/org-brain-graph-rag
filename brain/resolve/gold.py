"""`brain resolve gold` — the only module allowed to open `synthetic_truth.json`.

`data/canonical/synthetic_truth.json` is ground truth *by construction*: the synthetic
generator wrote down which invented identity belongs to which real Kafka person. Using it
anywhere in the pipeline would turn the whole measurement into a tautology, so it is read
here, once, to build `data/eval/resolution_gold.jsonl`, and nowhere else.
`tests/test_resolve_truth_isolation.py` fails the build if any other module in
`brain/resolve/` so much as names the file.

Positives are every pair inside a truth group (the Jira identity and each synthetic
identity that maps to it). Negatives are the hard ones and only the hard ones: two
identities of *different* real people whose display names look alike. A random negative
pair proves nothing — no resolver was ever going to merge "Jun Rao" with "Bruno Cadonna".
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from brain.canon.io import read_jsonl
from brain.canon.models import Person
from brain.harvest.base import utc_now_iso
from brain.resolve.names import display_tokens, norm_display

TRUTH_NAME = "synthetic_truth.json"
GOLD_NAME = "resolution_gold.jsonl"
#: How alike two normalised display names must look before a negative pair is "hard".
HARD_NEGATIVE_RATIO = 0.72
#: Brief 08 decision 6 (b): 100 adjudicated pairs make the entity gold.
ENTITY_GOLD_TARGET = 100


class GoldError(RuntimeError):
    """The gold set cannot be built from what is on disk."""


def _truth(canonical_dir: Path) -> dict[str, Any]:
    path = canonical_dir / TRUTH_NAME
    if not path.is_file():
        raise GoldError(
            f"{path} does not exist. The person gold is the synthetic identity map; without "
            "it there is nothing to grade resolution against."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("identity_map"), dict):
        raise GoldError(f"{path}: no `identity_map` object — is this the file `brain synth` wrote?")
    return raw


def _displays(canonical_dir: Path) -> dict[str, str]:
    """id -> display, straight from `persons.jsonl`.

    Not from the graph on purpose: `brain resolve` deletes the nodes it merges, so by the
    time gold is built half the identities in the truth map have no node left to read.
    """
    path = canonical_dir / "persons.jsonl"
    if not path.is_file():
        raise GoldError(f"{path} does not exist; the gold pairs would have no display names.")
    out: dict[str, str] = {}
    for person in read_jsonl(path, Person):
        first = person.identities[0] if person.identities else None
        out[person.id] = (first.display if first and first.display else None) or person.id
    return out


def truth_groups(identity_map: dict[str, str]) -> dict[str, list[str]]:
    """canonical (real) id -> every identity that is that person, the real one included."""
    groups: dict[str, set[str]] = defaultdict(set)
    for synthetic, real in identity_map.items():
        groups[real].update((synthetic, real))
    return {k: sorted(v) for k, v in sorted(groups.items())}


def positives(groups: dict[str, list[str]]) -> list[tuple[str, str]]:
    return sorted(
        {
            tuple(sorted(p))
            for members in groups.values()
            for p in itertools.combinations(members, 2)
        }
    )


def hard_negatives(
    groups: dict[str, list[str]],
    displays: dict[str, str],
    *,
    ratio: float = HARD_NEGATIVE_RATIO,
) -> list[tuple[str, str, float]]:
    """Pairs from *different* real people whose display names look alike.

    Blocked on a shared display token so the comparison is cheap, then kept only when the
    two normalised names are `ratio` alike. That is the band a resolver actually errs in:
    two people called "S. An", a "G. Wang" who is Guozhang and a "G. Wang" who is Gavin.
    """
    owner: dict[str, str] = {m: real for real, members in groups.items() for m in members}
    blocks: dict[str, list[str]] = defaultdict(list)
    for identity in owner:
        for token in display_tokens(displays.get(identity, identity)) or {
            norm_display(displays.get(identity, identity))
        }:
            blocks[token].append(identity)
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, float]] = []
    for members in blocks.values():
        for a, b in itertools.combinations(sorted(set(members)), 2):
            if owner[a] == owner[b] or (a, b) in seen:
                continue
            seen.add((a, b))
            score = SequenceMatcher(
                None, norm_display(displays.get(a, a)), norm_display(displays.get(b, b))
            ).ratio()
            if score >= ratio:
                out.append((a, b, round(score, 4)))
    return sorted(out, key=lambda row: (-row[2], row[0], row[1]))


def person_gold(canonical_dir: Path) -> list[dict[str, Any]]:
    raw = _truth(canonical_dir)
    displays = _displays(canonical_dir)
    groups = truth_groups(raw["identity_map"])
    rows: list[dict[str, Any]] = []
    for a, b in positives(groups):
        rows.append(
            {
                "kind": "person",
                "a": a,
                "b": b,
                "label": "same",
                "source": "identity_map",
                "a_display": displays.get(a),
                "b_display": displays.get(b),
            }
        )
    for a, b, score in hard_negatives(groups, displays):
        rows.append(
            {
                "kind": "person",
                "a": a,
                "b": b,
                "label": "different",
                "source": "hard_negative",
                "a_display": displays.get(a),
                "b_display": displays.get(b),
                "display_ratio": score,
            }
        )
    return rows


def entity_gold(batches_dir: Path, *, target: int = ENTITY_GOLD_TARGET) -> list[dict[str, Any]]:
    """Adjudicated grey-band pairs, `unsure` excluded — brief 08 decision 6 (b).

    Only what the adjudicator actually decided: a pair it could not judge is not a labelled
    pair, and the planner's hand-sampled ten arrive as their own file later.
    """
    from brain.resolve.build import SCHEMA_PATH, TASK
    from brain.resolve.decisions import discover, parse_batch

    root = batches_dir / TASK
    if not root.is_dir():
        return []
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for batch in discover(root):
        parse_batch(batch, schema)
        if not batch.ok or batch.batch_input is None or batch.output is None:
            continue
        if batch.batch_input.kind != "entity":
            continue
        sides = {p.pair_id: p for p in batch.batch_input.pairs}
        for decision in batch.output.decisions:
            if decision.verdict == "unsure" or decision.pair_id not in sides:
                continue
            pair = sides[decision.pair_id]
            rows.append(
                {
                    "kind": "entity",
                    "a": pair.a.id,
                    "b": pair.b.id,
                    "label": "same" if decision.verdict == "same" else "different",
                    "source": f"adjudicator:{batch.batch_id}",
                    "a_display": pair.a.name,
                    "b_display": pair.b.name,
                    "similarity": pair.similarity,
                    "reason": decision.reason,
                }
            )
    rows.sort(key=lambda r: (r["a"], r["b"]))
    return rows[:target]


def write_gold(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """One JSON object per line, sorted, atomically replaced."""
    ordered = sorted(rows, key=lambda r: (r["kind"], r["a"], r["b"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)
    return len(ordered)


def read_gold(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def run_gold(
    *,
    canonical_dir: Path,
    eval_dir: Path,
    batches_dir: Path,
    kinds: Sequence[str],
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    rows: list[dict[str, Any]] = []
    per_kind: dict[str, Any] = {}
    for kind in kinds:
        produced = person_gold(canonical_dir) if kind == "person" else entity_gold(batches_dir)
        rows.extend(produced)
        per_kind[kind] = {
            "pairs": len(produced),
            "same": sum(1 for r in produced if r["label"] == "same"),
            "different": sum(1 for r in produced if r["label"] == "different"),
        }
    # Kinds this run did not build keep whatever a previous run wrote for them.
    path = eval_dir / GOLD_NAME
    kept = [r for r in read_gold(path) if r["kind"] not in set(kinds)]
    total = write_gold(path, [*kept, *rows])
    summary = {
        "step": "resolve",
        "section": "gold",
        "generated_at": utc_now_iso(),
        "path": str(path),
        "kinds": per_kind,
        "kept_from_previous_runs": len(kept),
        "pairs": total,
    }
    for kind, stats in per_kind.items():
        echo(
            f"gold {kind}: {stats['pairs']} pairs "
            f"({stats['same']} same, {stats['different']} different)"
        )
    echo(f"gold: {total} pairs -> {path}")
    return summary, 0
