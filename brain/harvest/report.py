"""`data/reports/harvest.json` — what the run actually did, plus corpus density.

The report is *merged*, not overwritten: `brain harvest --source git` must not erase what
the Jira run recorded yesterday. And the link-density table is always recomputed from the
raw full pull on disk, so it stays true even on a run that only touched one source.

`raw_layout` is the handover to `brain canon`: which directory is authoritative per
source, which directories are incremental, and the key to deduplicate them on.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from brain.harvest import jira as jira_mod
from brain.harvest.base import HarvestResult, utc_now_iso, write_json_atomic
from brain.harvest.registry import Registry, get_registry

DENSITY_COLUMNS = (
    "n",
    "pct_formal_links",
    "pct_kip_mention",
    "pct_with_history",
    "avg_comments",
    "pct_assignee",
    "pct_fix_versions",
)

#: How canon must deduplicate the overlap between the full pull and any `--since` pull.
#: Mirrors the contract documented in `brain.harvest.base`. Keyed by source **type**, not
#: by name: two Jira instances in `sources.yaml` are two directories with one dedupe rule.
#: `ado`/`xray` are the template values from the public docs — untested, see
#: `docs/guides/adding-a-connector.md`.
DEDUPE: dict[str, dict[str, str | None]] = {
    "jira": {"key": "key", "recency": "fields.updated"},
    "confluence": {"key": "id", "recency": "version.number"},
    "git": {"key": "sha", "recency": None},
    "ado": {"key": "id", "recency": "fields.System.ChangedDate"},
    "xray": {"key": "key", "recency": "fields.updated"},
}


def report_sources(registry: Registry | None = None) -> tuple[tuple[str, str], ...]:
    """`(name, type)` for every enabled source, in `sources.yaml` order."""
    return tuple((s.name, s.type) for s in (registry or get_registry()).enabled())


LAYOUT_RULE = (
    "Read the authoritative directory first, then each incremental directory in date "
    "order, and deduplicate on dedupe_key keeping the record with the latest recency_field "
    "(git needs no tie-break: a sha is content-addressed). Enumerate a directory's pages "
    "from its checkpoint.json `files` list — never by globbing, because the page index "
    "restarts at 0 when the query signature changes."
)


def raw_layout(raw_dir: Path) -> dict[str, Any]:
    """Where the raw records are and how to fold the incremental pulls into the full one.

    Canon reads `data/raw/` without knowing which runs produced it. Spelling the layout
    out here means the overlap between a full pull and a `--since` pull is a documented
    dedupe rule rather than something a mapper has to infer from directory names.
    """
    sources: dict[str, Any] = {}
    for name, type_ in report_sources():
        base = raw_dir / name
        if not base.exists():
            continue
        incremental = sorted(str(p) for p in base.glob("since-*") if p.is_dir())
        rule = DEDUPE.get(type_, {"key": None, "recency": None})
        sources[name] = {
            "type": type_,
            "authoritative_dir": str(base),
            "incremental_dirs": incremental,
            "dedupe_key": rule["key"],
            "recency_field": rule["recency"],
        }
    return {"rule": LAYOUT_RULE, "sources": sources}


def link_density(raw_dir: Path, results: dict[str, HarvestResult], since: date | None) -> dict:
    """Per-component density from the **full** Jira pull (never from a `--since` slice)."""
    jira = next((s for s in get_registry().enabled() if s.type == "jira"), None)
    if jira is None:
        return {}
    if since is None and jira.name in results and results[jira.name].stats.get("link_density"):
        return results[jira.name].stats["link_density"]
    return jira_mod.analyze(
        jira_mod.iter_raw_issues(raw_dir / jira.name),
        components=tuple(jira.option("components", ()) or ()),
    ).get("link_density", {})


def build_report(
    results: dict[str, HarvestResult],
    *,
    raw_dir: Path,
    since: date | None,
    duration_s: float,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = dict(existing or {})
    report["step"] = "harvest"
    report["generated_at"] = utc_now_iso()
    report["last_run"] = {
        "sources": sorted(results),
        "since": since.isoformat() if since else None,
        "duration_s": round(duration_s, 2),
        "new_pages": sum(r.pages for r in results.values()),
        "new_records": sum(r.records for r in results.values()),
    }
    # A `--since` pull is a different result set living in its own directory, so it is
    # recorded next to the full pull, never on top of it: `sources` stays the authoritative
    # record of the corpus on disk even after a dozen incremental runs.
    if since is None:
        bucket: dict[str, Any] = dict(report.get("sources") or {})
    else:
        incremental = dict(report.get("incremental") or {})
        bucket = dict(incremental.get(since.isoformat()) or {})

    for name, result in results.items():
        # Rebuild the entry from this run rather than updating the old one in place, so a
        # field that a previous version of the report wrote cannot survive as a stale
        # value that looks current. `last_fetch` is the one thing carried forward.
        previous_fetch = (bucket.get(name) or {}).get("last_fetch")
        entry = result.as_dict()
        entry["since"] = since.isoformat() if since else None
        # `records`/`pages`/`duration_s` describe *this* run, so an idempotent re-run zeroes
        # them. Keep the last run that actually fetched, or the cold-pull cost — the number
        # that says what this corpus is worth re-fetching — disappears after one no-op run.
        if result.pages:
            entry["last_fetch"] = {
                "at": report["generated_at"],
                "pages": result.pages,
                "records": result.records,
                "duration_s": round(result.duration_s, 2),
            }
        elif previous_fetch:
            entry["last_fetch"] = previous_fetch
        bucket[name] = entry

    if since is None:
        report["sources"] = bucket
    else:
        incremental[since.isoformat()] = bucket
        report["incremental"] = incremental

    report["raw_layout"] = raw_layout(raw_dir)

    density = link_density(raw_dir, results, since)
    if density:
        report["link_density"] = density
    return report


def write_report(path: Path, report: dict[str, Any]) -> Path:
    write_json_atomic(path, report)
    return path


def _fmt_density(density: dict[str, Any]) -> list[str]:
    if not density:
        return []
    tail = ("other", "all")
    order = [k for k in density if k not in tail] + [k for k in tail if k in density]
    columns = "".join(f"{c.replace('pct_', '%'):>22}" for c in DENSITY_COLUMNS)
    head = f"  {'component':<10}" + columns
    lines = ["", "link density (from the full Jira pull):", head]
    for name in order:
        row = density[name]
        lines.append(f"  {name:<10}" + "".join(f"{row.get(c, '-'):>22}" for c in DENSITY_COLUMNS))
    return lines


def summarize(report: dict[str, Any]) -> str:
    """The short human summary printed at the end of a run."""
    since = (report.get("last_run") or {}).get("since")
    if since:
        bucket = (report.get("incremental") or {}).get(since) or {}
        lines = [f"harvest (incremental, since {since}):"]
    else:
        bucket = report.get("sources") or {}
        lines = ["harvest:"]
    for name, type_ in report_sources():
        entry = bucket.get(name)
        if not entry:
            continue
        cp = entry.get("checkpoint") or {}
        stats = entry.get("stats") or {}
        fatal = sum(1 for e in entry.get("errors") or [] if e.get("fatal"))
        retries = sum(1 for e in entry.get("errors") or [] if e.get("kind") == "retry")
        note = {
            "jira": f"{stats.get('issues', 0)} issues, "
            f"{stats.get('pct_changelog_expanded', 0)}% changelog expanded, "
            f"{stats.get('pct_with_comment_field', 0)}% comment field",
            "confluence": f"{stats.get('pages', 0)} pages, "
            f"{stats.get('pct_with_body_storage', 0)}% with body.storage, "
            f"{(stats.get('kip_key') or {}).get('pages_unparseable', 0)} keyless + "
            f"{(stats.get('kip_key') or {}).get('pages_space_separated', 0)} 'KIP N', "
            f"{(stats.get('kip_key') or {}).get('colliding_keys', 0)} colliding keys "
            f"over {(stats.get('kip_key') or {}).get('pages_in_collisions', 0)} pages",
            "git": f"{stats.get('commits', 0)} commits, "
            f"{stats.get('with_issue_key', 0)} keyed "
            f"({stats.get('pct_of_keyed_with_pr_number', 0)}% of those carry a PR)",
        }.get(type_, f"{entry.get('records', 0)} records")
        lines.append(
            f"  {name:<11} +{entry.get('pages', 0)} pages / +{entry.get('records', 0)} records "
            f"in {entry.get('duration_s', 0)}s "
            f"(total on disk: {cp.get('records', 0)} records, done={cp.get('done')}) "
            f"| {note}"
            + (f" | {fatal} errors" if fatal else "")
            + (f" | {retries} retries" if retries else "")
        )
    lines += _fmt_density(report.get("link_density") or {})
    return "\n".join(lines)
