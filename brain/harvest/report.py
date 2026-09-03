"""`data/reports/harvest.json` — what the run actually did, plus corpus density.

The report is *merged*, not overwritten: `brain harvest --source git` must not erase what
the Jira run recorded yesterday. And the link-density table is always recomputed from the
raw full pull on disk, so it stays true even on a run that only touched one source.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from brain.harvest import jira as jira_mod
from brain.harvest.base import HarvestResult, utc_now_iso, write_json_atomic

SOURCES = ("jira", "confluence", "git")
DENSITY_COLUMNS = (
    "n",
    "pct_formal_links",
    "pct_kip_mention",
    "pct_changelog_present",
    "avg_comments",
    "pct_assignee",
    "pct_fix_versions",
)


def link_density(raw_dir: Path, results: dict[str, HarvestResult], since: date | None) -> dict:
    """Per-component density from the **full** Jira pull (never from a `--since` slice)."""
    if since is None and "jira" in results and results["jira"].stats.get("link_density"):
        return results["jira"].stats["link_density"]
    return jira_mod.analyze(jira_mod.iter_raw_issues(raw_dir / "jira")).get("link_density", {})


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
    sources: dict[str, Any] = dict(report.get("sources") or {})
    for name, result in results.items():
        entry = dict(sources.get(name) or {})
        entry.update(result.as_dict())
        entry["since"] = since.isoformat() if since else None
        sources[name] = entry
    report["sources"] = sources

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
    order = [k for k in ("streams", "connect", "clients", "other", "all") if k in density]
    columns = "".join(f"{c.replace('pct_', '%'):>22}" for c in DENSITY_COLUMNS)
    head = f"  {'component':<10}" + columns
    lines = ["", "link density (from the full Jira pull):", head]
    for name in order:
        row = density[name]
        lines.append(f"  {name:<10}" + "".join(f"{row.get(c, '-'):>22}" for c in DENSITY_COLUMNS))
    return lines


def summarize(report: dict[str, Any]) -> str:
    """The short human summary printed at the end of a run."""
    lines = ["harvest:"]
    for name in SOURCES:
        entry = (report.get("sources") or {}).get(name)
        if not entry:
            continue
        cp = entry.get("checkpoint") or {}
        stats = entry.get("stats") or {}
        fatal = sum(1 for e in entry.get("errors") or [] if e.get("fatal"))
        retries = sum(1 for e in entry.get("errors") or [] if e.get("kind") == "retry")
        note = {
            "jira": f"{stats.get('issues', 0)} issues, "
            f"{stats.get('pct_with_changelog', 0)}% changelog, "
            f"{stats.get('pct_with_comment_field', 0)}% comment field",
            "confluence": f"{stats.get('pages', 0)} pages, "
            f"{stats.get('pct_with_body_storage', 0)}% with body.storage",
            "git": f"{stats.get('commits', 0)} commits, "
            f"{stats.get('with_issue_key', 0)} keyed "
            f"({stats.get('pct_of_keyed_with_pr_number', 0)}% of those carry a PR)",
        }[name]
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
