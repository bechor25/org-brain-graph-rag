"""Reading `data/raw/` back, per the contract in `brain.harvest.base`.

Two rules, both of which exist because breaking them produces *duplicates*, not errors:

1. **Never glob a run directory.** The page list lives in that directory's
   `checkpoint.json` under `files`. A page index restarts at 0 when the query signature
   changes, so a glob can pick up `issues-0000.json` from an older, different query.
2. **Authoritative first, then `since-*` in date order, then deduplicate.** An
   incremental pull re-fetches records the full pull already has; the record with the
   latest recency field wins (a git sha is content-addressed, so the first copy wins).

The dedupe table is restated in `data/reports/harvest.json` under `raw_layout`; it is
duplicated here as code so canon does not have to parse a report to read its own inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.harvest.base import checkpoint_pages
from brain.harvest.git import COMMITS_FILE
from brain.harvest.registry import Registry, get_registry

#: source **type** -> (dedupe key path, recency path or None). Mirrors
#: `harvest.report.DEDUPE`. Keyed by type so two Jira instances in `sources.yaml` share
#: one rule instead of needing an entry each.
DEDUPE: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {
    "jira": (("key",), ("fields", "updated")),
    "confluence": (("id",), ("version", "number")),
    "git": (("sha",), None),
    "ado": (("id",), ("fields", "System.ChangedDate")),
    "xray": (("key",), ("fields", "updated")),
}

#: The JSON key each source type's raw page keeps its records under (git is JSONL).
PAGE_ITEMS = {"jira": "issues", "confluence": "results", "ado": "value", "xray": "issues"}


def source_names(registry: Registry | None = None) -> tuple[str, ...]:
    """Every enabled source name, in `sources.yaml` order."""
    return (registry or get_registry()).names()


def dig(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = record
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def run_dirs(raw_dir: Path, source: str) -> list[Path]:
    """The authoritative directory first, then each `since-<date>/` in date order."""
    base = raw_dir / source
    if not base.is_dir():
        return []
    return [base, *sorted((p for p in base.glob("since-*") if p.is_dir()), key=lambda p: p.name)]


def iter_dir(run_dir: Path, source_type: str) -> Iterator[dict[str, Any]]:
    """Every raw record in one run directory, in write order. Keyed by source *type*."""
    if source_type == "git":
        path = run_dir / COMMITS_FILE
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    for path in checkpoint_pages(run_dir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        yield from payload.get(PAGE_ITEMS[source_type]) or []


@dataclass
class RawSlice:
    """The deduplicated records of one source, plus how much overlap there was."""

    source: str
    records: list[dict[str, Any]] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    read: int = 0
    duplicates: int = 0
    superseded: int = 0

    def stats(self) -> dict[str, Any]:
        return {
            "dirs": self.dirs,
            "records_read": self.read,
            "unique": len(self.records),
            "duplicates": self.duplicates,
            "superseded_by_incremental": self.superseded,
        }


def _recency(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value = dig(record, path)
    return "" if value is None else value


def load_source(raw_dir: Path, source: str, *, source_type: str | None = None) -> RawSlice:
    """Read one source's raw records: every run directory, deduplicated, in first-seen order.

    First-seen order is kept even when an incremental copy wins, so the output ordering
    does not depend on which `--since` runs happen to exist on disk.

    `source` is the registry *name* (the directory under `data/raw/`); the dedupe rule
    comes from its *type*, looked up in `sources.yaml` unless given.
    """
    type_ = source_type or get_registry().source(source).type
    key_path, recency_path = DEDUPE[type_]
    slice_ = RawSlice(source=source)
    index: dict[str, int] = {}

    for run_dir in run_dirs(raw_dir, source):
        slice_.dirs.append(str(run_dir))
        incremental = run_dir.name.startswith("since-")
        for record in iter_dir(run_dir, type_):
            slice_.read += 1
            key = str(dig(record, key_path) or "")
            if key not in index:
                index[key] = len(slice_.records)
                slice_.records.append(record)
                continue
            slice_.duplicates += 1
            position = index[key]
            if recency_path is None:
                # git: a sha *is* the content, so the copies cannot differ. First wins.
                continue
            if _recency(record, recency_path) > _recency(slice_.records[position], recency_path):
                slice_.records[position] = record
                slice_.superseded += 1 if incremental else 0
    return slice_
