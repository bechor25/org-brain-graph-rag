"""Orchestration for `brain harvest`: pick sources, run them, merge the report.

One source failing must not take the others down — every failure lands in the report and
the run keeps going. The exit code tells you whether anything was fatal.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from brain.canon.models import INCREMENTAL_SLICE
from brain.harvest.base import HarvestResult
from brain.harvest.confluence import ConfluenceConnector
from brain.harvest.git import GitConnector
from brain.harvest.jira import JiraConnector
from brain.harvest.registry import Registry, RegistryError, SourceConfig, get_registry
from brain.harvest.report import build_report, summarize, write_report

#: source **type** -> the connector class that implements it. A type with a registry
#: entry but no class here is the honest state of a half-added system: the guide's ADO and
#: Xray skeletons live in the docs until someone writes the class, and `brain harvest`
#: says exactly that instead of pretending the source does not exist.
CONNECTORS: dict[str, type] = {
    "jira": JiraConnector,
    "confluence": ConfluenceConnector,
    "git": GitConnector,
}


def resolve_sources(source: str, registry: Registry | None = None) -> list[str]:
    """`--source` → the source ids to run, through the registry (`sources.yaml`)."""
    return (registry or get_registry()).resolve(source)


def build_connector(
    config: SourceConfig,
    raw_dir: Path,
    *,
    max_records: int | None = None,
    new_slice: bool = False,
) -> Any:
    cls = CONNECTORS.get(config.type)
    if cls is None:
        raise RegistryError(
            f"source {config.id!r} has type {config.type!r}, which has no connector yet. "
            f"Implemented: {', '.join(sorted(CONNECTORS))}. "
            "docs/guides/adding-a-connector.md has the skeleton."
        )
    if new_slice and not getattr(cls, "supports_new_slice", False):
        # Refused rather than ignored: `--slice incremental` means "fetch what the base
        # slice does not have", and for a connector that cannot rewrite its own window
        # the flag would silently degrade into "fetch what changed" — the same 10 items
        # nobody wanted, marked as an increment.
        able = sorted(n for n, c in CONNECTORS.items() if getattr(c, "supports_new_slice", False))
        raise RegistryError(
            f"--slice incremental is not implemented for source {config.id!r} (type "
            f"{config.type!r}): opening the base window is query-language work and only "
            f"{', '.join(able)} does it. Run it without --slice to pull what changed "
            "inside the slice."
        )
    kwargs: dict[str, Any] = {"source": config, "max_records": max_records}
    if new_slice:
        kwargs["new_slice"] = True
    return cls(raw_dir, **kwargs)


def load_existing(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_harvest(
    sources: Sequence[str],
    *,
    raw_dir: Path,
    reports_dir: Path,
    since: date | None = None,
    max_records: int | None = None,
    slice_: str | None = None,
    factories: dict[str, Callable[[Path], Any]] | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Run the requested connectors and write `data/reports/harvest.json`.

    `slice_` names the slice this pull is for. `"incremental"` asks each connector for the
    records the base slice does **not** have (its window opens at `--since` and loses the
    base query's end date); anything else keeps `--since` meaning "what changed inside the
    slice". Which slice a record ends up in is decided later, by `brain canon`, from the
    directory it was first seen in — the flag chooses the query, not the label.

    Returns the merged report and the process exit code (1 if anything was fatal).
    """
    registry = get_registry()
    started = time.perf_counter()
    results: dict[str, HarvestResult] = {}

    for name in sources:
        factory = (factories or {}).get(name)
        connector = (
            factory(raw_dir)
            if factory
            else build_connector(
                registry.source(name),
                raw_dir,
                max_records=max_records,
                new_slice=slice_ == INCREMENTAL_SLICE,
            )
        )
        credentials = getattr(connector, "credentials", None)
        if credentials is not None and credentials.present:
            # The variable name, never the value (ADR-0005 §4).
            echo(f"[{name}] auth: {credentials.describe()}")
        echo(f"[{name}] {connector.query_text(since)}")
        if max_records:
            echo(f"[{name}] limit: {max_records} record(s) — this pull is capped on purpose")
        result = connector.run(since)
        results[name] = result
        close = getattr(getattr(connector, "http", None), "close", None)
        if close:
            close()

    report_path = reports_dir / "harvest.json"
    report = build_report(
        results,
        raw_dir=raw_dir,
        since=since,
        duration_s=time.perf_counter() - started,
        existing=load_existing(report_path),
        slice_=slice_,
        max_records=max_records,
    )
    write_report(report_path, report)
    echo(summarize(report))
    echo(f"report: {report_path}")

    fatal = sum(1 for r in results.values() for e in r.errors if e.get("fatal"))
    return report, (1 if fatal else 0)
