"""Random labels in place of strategy names, and the map that is kept away from the judge.

Plan 3 decision 5: the judge is blind to which retrieval produced an answer. That is one
sentence and two mechanisms:

* every case is addressed by an opaque label, drawn from a seeded stream so a rebuild
  produces the same batches and a reviewer can reproduce them;
* the label → `(question, strategy)` map is written to `data/eval/blind_map.json`, which is
  **outside `data/batches/judge/`** — the judge's own agent definition gives it `Read` and
  points it at its shard directory, and nothing it is told to read leads to the map.

Keeping the map elsewhere is necessary and not sufficient, so `leaks()` is the second half:
it walks a batch payload before it is written and refuses any structural give-away — a field
named `strategy`, a value that *is* a strategy id, a question id that would let two cases be
lined up. It deliberately does not grep the prose: this corpus is Kafka, `s3` appears in it
as Amazon S3, and a check that cries wolf on a snippet is a check somebody switches off.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.harvest.base import utc_now_iso, write_json_atomic

BLIND_MAP_NAME = "blind_map.json"
#: Eight hex characters: enough that two labels in a set of a few hundred do not collide by
#: accident, short enough that a judge can copy one back into its output without a typo.
LABEL_CHARS = 8

#: Field names that would say which retrieval produced a case. `route` and `cypher_used`
#: are here because a Result's trace names the strategy inside it.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "strategy",
        "strategies",
        "executed",
        "expected_strategy",
        "route",
        "cypher_used",
        "qid",
        "question_id",
        "run_file",
        "origin",
        "gold_source",
    }
)


class BlindError(RuntimeError):
    """A batch would have told the judge something it must not know."""


def labels(seed: int) -> Iterator[str]:
    """An endless stream of distinct opaque labels, reproducible from `seed`."""
    rng = random.Random(seed)
    seen: set[str] = set()
    while True:
        label = f"{rng.randrange(16**LABEL_CHARS):0{LABEL_CHARS}x}"
        if label in seen:
            continue
        seen.add(label)
        yield label


@dataclass
class BlindMap:
    """What every label means. Written once by `judge build`, read once by `judge merge`."""

    seed: int = 0
    generated_at: str = ""
    sha: str = ""
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    pairs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_case(self, label: str, **fields: Any) -> str:
        self.cases[label] = dict(fields)
        return label

    def add_pair(self, label: str, **fields: Any) -> str:
        self.pairs[label] = dict(fields)
        return label

    def case(self, label: str) -> dict[str, Any] | None:
        return self.cases.get(label)

    def pair(self, label: str) -> dict[str, Any] | None:
        return self.pairs.get(label)

    @property
    def secrets(self) -> set[str]:
        """Everything a batch must not repeat: the strategy names and the question ids."""
        out: set[str] = set()
        for entry in list(self.cases.values()) + list(self.pairs.values()):
            for key in ("qid", "strategy"):
                if entry.get(key):
                    out.add(str(entry[key]))
            for side in ("a", "b"):
                value = entry.get(side)
                if isinstance(value, Mapping) and value.get("strategy"):
                    out.add(str(value["strategy"]))
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": "eval.judge.blind_map",
            "generated_at": self.generated_at or utc_now_iso(),
            "sha": self.sha,
            "seed": self.seed,
            "note": (
                "Label -> (question, strategy). Kept outside data/batches/judge/ on purpose: "
                "the judge reads its shard directory and nothing here. Do not copy it there, "
                "and do not paste any of it into a judge's prompt."
            ),
            "cases": self.cases,
            "pairs": self.pairs,
        }

    def write(self, path: Path) -> Path:
        return write_json_atomic(Path(path), self.as_dict())

    @classmethod
    def read(cls, path: Path) -> BlindMap:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BlindError(
                f"the blind map at {path} cannot be read ({exc}); without it a judgment "
                "cannot be attributed to a strategy. Re-run `brain eval judge build`."
            ) from exc
        if not isinstance(data, dict):
            raise BlindError(f"the blind map at {path} is not an object")
        return cls(
            seed=int(data.get("seed") or 0),
            generated_at=str(data.get("generated_at") or ""),
            sha=str(data.get("sha") or ""),
            cases={str(k): dict(v) for k, v in (data.get("cases") or {}).items()},
            pairs={str(k): dict(v) for k, v in (data.get("pairs") or {}).items()},
        )


def _walk(value: Any, trail: str = "") -> Iterator[tuple[str, str, Any]]:
    """Every (trail, key, value) in a JSON tree, keys and scalars alike."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{trail}.{key}" if trail else str(key)
            yield here, str(key), item
            yield from _walk(item, here)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            here = f"{trail}[{index}]"
            yield from _walk(item, here)


def leaks(payload: Any, secrets: Sequence[str] | set[str]) -> list[str]:
    """Structural give-aways in a batch payload, as human-readable findings.

    Two kinds, both exact rather than substring, because the corpus is full of words that
    look like identifiers:

    * a field *named* after the thing it would reveal (`strategy`, `qid`, `route`);
    * a string that *equals* a secret — a strategy id or a question id. A snippet is
      hundreds of characters, so equality never fires on prose.
    """
    wanted = {str(s) for s in secrets if s}
    found: list[str] = []
    for trail, key, value in _walk(payload):
        if key in FORBIDDEN_KEYS:
            found.append(f"{trail}: a field named `{key}` says which retrieval this came from")
        if isinstance(value, str) and value in wanted:
            found.append(f"{trail}: the value {value!r} is the thing the label replaces")
        if isinstance(value, Mapping):
            for inner in value:
                if str(inner) in wanted:
                    found.append(f"{trail}: keyed by {inner!r}, which is what the label replaces")
    return sorted(dict.fromkeys(found))


def assert_blind(payload: Any, secrets: Sequence[str] | set[str], *, where: str = "") -> None:
    """Refuse to write a batch that would tell the judge what it must not know."""
    found = leaks(payload, secrets)
    if found:
        head = f"{where}: " if where else ""
        raise BlindError(
            f"{head}the judge batch is not blind — "
            + "; ".join(found[:5])
            + (f" (+{len(found) - 5} more)" if len(found) > 5 else "")
        )


def map_is_outside(map_path: Path, batches_root: Path) -> bool:
    """True when the blind map does not live under the directory the judge reads."""
    try:
        Path(map_path).resolve().relative_to(Path(batches_root).resolve())
    except ValueError:
        return True
    return False
