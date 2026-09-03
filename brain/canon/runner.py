"""Orchestration for `brain canon`: raw → the five canonical files → `canon.json`.

Three properties the step has to keep:

* **Partial runs must not delete.** `brain canon --source git` rewrites the changes, but
  the Jira work items and the Confluence documents stay exactly as they were. Records are
  therefore merged by *owner*, not truncated.
* **Synthetic records survive.** The Xray/ADO layer (Task 3) appends to the same files.
  Canon never drops a record marked `synthetic`, whatever `--source` says.
* **A rerun is byte-identical.** Output is sorted with a natural key and written
  temp+rename, so `shasum` before and after a rerun is the proof of idempotence.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from brain.canon.io import read_jsonl, write_jsonl
from brain.canon.mappers.base import Bundle
from brain.canon.mappers.confluence import map_pages
from brain.canon.mappers.git import map_commits
from brain.canon.mappers.jira import map_issues
from brain.canon.models import Change, Container, Document, Person, WorkItem
from brain.canon.raw import SOURCES, load_source
from brain.canon.report import build_report, summarize, write_report

#: source -> its mapper. Adding an org system is a new entry here, never a new schema.
MAPPERS: dict[str, Callable[[Any], Bundle]] = {
    "jira": map_issues,
    "confluence": map_pages,
    "git": map_commits,
}

#: canonical file stem -> model. The five types of spec §2.2, one file each.
FILES: dict[str, type[BaseModel]] = {
    "workitems": WorkItem,
    "documents": Document,
    "persons": Person,
    "changes": Change,
    "containers": Container,
}

#: `Change` has no `source` field (spec §2.2) and only the git mapper produces one, so
#: ownership is by convention. Synthetic PRs from Task 3 are protected by `synthetic`.
CHANGE_OWNER = "git"

_DIGITS = re.compile(r"(\d+)")


def resolve_sources(source: str) -> list[str]:
    if source == "all":
        return list(SOURCES)
    if source not in MAPPERS:
        raise ValueError(f"unknown source {source!r}; expected one of jira|confluence|git|all")
    return [source]


def natural_key(text: str) -> tuple[object, ...]:
    """`KAFKA-9` before `KAFKA-10`. Sorting is only for humans; determinism is the point."""
    return tuple(int(p) if p.isdigit() else p for p in _DIGITS.split(text))


def owner_of(record: BaseModel) -> str:
    if isinstance(record, Person):
        return record.identities[0].source
    return str(getattr(record, "source", CHANGE_OWNER))


def sort_key(record: BaseModel) -> tuple[object, ...]:
    kind = str(getattr(record, "kind", ""))
    return (kind, *natural_key(str(record.id)))


class CanonError(RuntimeError):
    """The step cannot produce a correct file. Never downgraded to a partial result."""


def read_existing(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """The records already on disk, or a loud failure.

    Swallowing a read error here silently deletes data: everything this run does not
    produce — the other sources' records and every synthetic record — is carried over
    from this file. An empty list on a parse error looks like a successful run that
    happens to have dropped half the corpus, and the next step would believe it.
    """
    if not path.is_file():
        return []
    try:
        return list(read_jsonl(path, model))
    except (OSError, ValueError) as exc:
        raise CanonError(
            f"{path} exists but could not be read as {model.__name__}: {exc}\n"
            "Records from other sources and every synthetic record are carried over from "
            "this file, so canon will not continue and silently drop them. Fix or remove "
            "the file (a full `brain canon` rebuilds every source) and run again."
        ) from exc


def carried_over(
    existing: Iterable[BaseModel], produced: Iterable[BaseModel], sources: Sequence[str]
) -> tuple[list[BaseModel], dict[str, int]]:
    """Existing records this run is not responsible for: other sources, plus synthetics.

    Returns the records and a count of them, because "what did this run keep rather than
    build" is exactly the number a partial run has to be judged on.
    """
    fresh = {r.id for r in produced}
    keep: list[BaseModel] = []
    synthetic = 0
    for record in existing:
        if record.id in fresh:
            continue
        if record.synthetic or owner_of(record) not in sources:
            keep.append(record)
            synthetic += 1 if record.synthetic else 0
    return keep, {"total": len(keep), "synthetic": synthetic}


def merge_bundles(bundles: dict[str, Bundle]) -> dict[str, list[BaseModel]]:
    out: dict[str, list[BaseModel]] = {name: [] for name in FILES}
    for bundle in bundles.values():
        out["workitems"].extend(bundle.workitems)
        out["documents"].extend(bundle.documents)
        out["changes"].extend(bundle.changes)
        out["persons"].extend(bundle.persons.values())
        out["containers"].extend(bundle.containers.values())
    return out


def run_canon(
    sources: Sequence[str],
    *,
    raw_dir: Path,
    canonical_dir: Path,
    reports_dir: Path,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Map the requested sources and rewrite `data/canonical/`. Returns (report, exit code)."""
    started = time.perf_counter()
    bundles: dict[str, Bundle] = {}
    slices: dict[str, dict[str, Any]] = {}
    durations: dict[str, float] = {}

    for name in sources:
        began = time.perf_counter()
        raw = load_source(raw_dir, name)
        bundle = MAPPERS[name](raw.records)
        bundles[name] = bundle
        slices[name] = raw.stats()
        durations[name] = round(time.perf_counter() - began, 2)
        echo(
            f"[{name}] {raw.stats()['unique']} raw records "
            f"({raw.stats()['duplicates']} duplicate) → "
            f"{sum(len(v) for v in (bundle.workitems, bundle.documents, bundle.changes))} records, "
            f"{len(bundle.persons)} identities, {durations[name]}s"
        )

    produced = merge_bundles(bundles)
    written: dict[str, int] = {}
    kept: dict[str, dict[str, int]] = {}
    records: dict[str, list[BaseModel]] = {}
    for name, model in FILES.items():
        path = canonical_dir / f"{name}.jsonl"
        keep, kept[name] = carried_over(read_existing(path, model), produced[name], sources)
        merged = produced[name] + keep
        merged.sort(key=sort_key)
        records[name] = merged
        written[name] = write_jsonl(path, merged)

    echo("wrote " + ", ".join(f"{n} {name}" for name, n in written.items()))

    report_path = reports_dir / "canon.json"
    report = build_report(
        bundles,
        records=records,
        carried_over=kept,
        slices=slices,
        sources=sources,
        durations=durations,
        duration_s=time.perf_counter() - started,
        existing=_load_json(report_path),
    )
    write_report(report_path, report)
    echo(summarize(report))
    echo(f"report: {report_path}")
    return report, 0


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
