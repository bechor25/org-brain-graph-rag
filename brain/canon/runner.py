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
from brain.canon.models import (
    BASE_SLICE,
    INCREMENTAL_SLICE,
    Change,
    Container,
    Document,
    Person,
    WorkItem,
)
from brain.canon.raw import load_source
from brain.canon.report import build_report, summarize, write_report
from brain.harvest.registry import (
    DOCUMENT_TYPES,
    Registry,
    RegistryError,
    SourceConfig,
    get_registry,
)

#: source **type** -> its mapper. Adding an org system is a new entry here plus an entry
#: in `sources.yaml`, never a new schema. Keyed by type, so a second Jira instance is a
#: registry line and no code at all.
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

#: `Change` has no `source` field (spec §2.2) and only a git mapper produces one, so
#: ownership is by convention: the git source in `sources.yaml`. Synthetic PRs from Task 3
#: are protected by `synthetic`.
CHANGE_OWNER_TYPE = "git"


def change_owner(registry: Registry | None = None) -> str:
    """Which registry entry owns a `Change` that does not say (`source_id` unset).

    `Change` has no `source` field at all, so before `source_id` existed its ownership was
    pure convention. It still is for records written then: this is the fallback, and it is
    deliberately deterministic rather than an error, because refusing here would make a
    two-git-source registry unable to re-run canon at all.

    The entry whose `id` *is* `git` wins, because that is the one that would have written
    an unstamped record; otherwise the first git source in file order. Every record a
    two-instance registry writes from now on carries `source_id` and never reaches this.
    """
    reg = registry or get_registry()
    git = [s.id for s in reg.enabled() if s.type == CHANGE_OWNER_TYPE]
    if CHANGE_OWNER_TYPE in git:
        return CHANGE_OWNER_TYPE
    return git[0] if git else CHANGE_OWNER_TYPE


_DIGITS = re.compile(r"(\d+)")


def resolve_sources(source: str, registry: Registry | None = None) -> list[str]:
    """`--source` → the source ids to map, through the registry (`sources.yaml`)."""
    reg = registry or get_registry()
    names = reg.resolve(source)
    unmapped = [n for n in names if reg.source(n).type not in MAPPERS]
    if unmapped:
        raise RegistryError(
            f"no mapper for {', '.join(unmapped)} (type "
            f"{', '.join(sorted({reg.source(n).type for n in unmapped}))}). "
            f"Implemented: {', '.join(sorted(MAPPERS))}. "
            "docs/guides/adding-a-connector.md has the skeleton."
        )
    return names


def mapper_kwargs(config: SourceConfig) -> dict[str, Any]:
    """Per-source arguments a mapper needs from the registry.

    Only the wiki has one: its title→key rule. Passing *this source's* spec rather than
    letting the mapper reach for the corpus-wide default is what keeps two wikis honest —
    each page gets the key its own entry describes.
    """
    if config.type in DOCUMENT_TYPES:
        return {"document": config.document, "space": str(config.option("space", "") or "")}
    return {}


def stamp_source_id(bundle: Bundle, config: SourceConfig) -> int:
    """Write `source_id` on everything the mapper produced — when it says something new.

    An `id` equal to its `type` adds no information: `source` already carries it, and
    every record in a single-instance registry would gain a field repeating itself. That
    is not only noise, it would rewrite all five canonical files and retire the
    byte-identity check in `data/reports/modularity.json`. The field appears exactly when
    it disambiguates, which is when a second instance of a type exists — its id cannot
    also be the bare type.

    Returns the number of records stamped, for the report.
    """
    if config.id == config.type:
        return 0
    records = [
        *bundle.workitems,
        *bundle.documents,
        *bundle.changes,
        *bundle.persons.values(),
        *bundle.containers.values(),
    ]
    for record in records:
        record.source_id = config.id
    return len(records)


def slice_census(records: dict[str, list[BaseModel]]) -> dict[str, Any]:
    """`{file: {base: n, incremental: m}}` plus a total — what the report says about §3.9.

    Counted from the records that were **written**, not from the raw table: a work item
    whose raw copy came from a `since-` directory can still be `base` (the base pull had
    it too), and a `Person` is `base` as soon as any base record names it. The file is the
    only place those rules have already been applied.
    """
    out: dict[str, Any] = {}
    totals = {BASE_SLICE: 0, INCREMENTAL_SLICE: 0}
    for name, rows in records.items():
        counts = {BASE_SLICE: 0, INCREMENTAL_SLICE: 0}
        for record in rows:
            counts[getattr(record, "slice", BASE_SLICE)] += 1
        out[name] = counts
        for key, value in counts.items():
            totals[key] += value
    out["total"] = totals
    return out


def natural_key(text: str) -> tuple[object, ...]:
    """`KAFKA-9` before `KAFKA-10`. Sorting is only for humans; determinism is the point."""
    return tuple(int(p) if p.isdigit() else p for p in _DIGITS.split(text))


def owner_of(record: BaseModel, sources: Sequence[str] = ()) -> str:
    """Which run is responsible for a record: the registry entry that produced it.

    `source_id` answers directly when it is there, and it is there exactly when the answer
    is not obvious — a registry entry whose `id` differs from its `type`, which is what a
    second Jira instance must have. Without it the record comes from a single-instance
    setup, where `source` (a *type*) and the entry id are the same string, and for a
    `Change` — which has no `source` at all — from the git source by convention.
    """
    source_id = getattr(record, "source_id", None)
    if source_id:
        return str(source_id)
    if isinstance(record, Person):
        owner = record.identities[0].source
    else:
        owner = str(getattr(record, "source", "") or "") or change_owner()
    if owner in sources:
        return owner
    # `source` is a type; map it back to the registry entry this run is about.
    for name in sources:
        try:
            if get_registry().source(name).type == owner:
                return name
        except RegistryError:
            continue
    return owner


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
        if record.synthetic or owner_of(record, sources) not in sources:
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

    registry = get_registry()
    for name in sources:
        began = time.perf_counter()
        config = registry.source(name)
        raw = load_source(raw_dir, name, source_type=config.type)
        # `slice_of` is empty unless this source has a `since-<date>/` directory, so a
        # corpus that was never pulled incrementally maps exactly as it always did.
        bundle = MAPPERS[config.type](raw.records, **mapper_kwargs(config), slice_of=raw.slice_of())
        stamp_source_id(bundle, config)
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
    census = slice_census(records)
    if census["total"][INCREMENTAL_SLICE]:
        echo(
            f"slice: {census['total'][INCREMENTAL_SLICE]} record(s) marked "
            f"{INCREMENTAL_SLICE} ("
            + ", ".join(
                f"{n[INCREMENTAL_SLICE]} {name}"
                for name, n in census.items()
                if name != "total" and n[INCREMENTAL_SLICE]
            )
            + ") — `brain reset --slice incremental` is what takes them out again"
        )

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
    report["slices"] = census
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
