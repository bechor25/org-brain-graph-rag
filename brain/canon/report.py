"""`data/reports/canon.json` — what the mapping produced and what it cost.

The counts are the boring half. The half that earns the step is:

* `refs.via_link` vs `refs.via_text` per source — how much of the organisation's
  traceability is stated formally and how much only exists in prose.
* `refs.filtered` — what the regex mistook for an issue key (`UTF-8`, `SHA-256`,
  `KAKFA-17173`), which is the price of deterministic extraction.
* `unmapped_fields` — every raw field name no canonical field represents, counted over
  the records that actually carried a value. This is "what normalization lost", measured.
* `kip_key` — every `KIP-N` two or more pages claim, with the decision and whether the
  rule had to fall back on body size.
* `checks` — the step brief's acceptance criteria, evaluated against the written files.
  A criterion that fails stays visible here; it is never quietly relaxed.

Like the harvest report, this one is *merged*: `brain canon --source git` must not erase
what the Jira run recorded. Counts are always recomputed from the written files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from brain.canon.mappers.base import Bundle
from brain.harvest.base import utc_now_iso, write_json_atomic

#: The one page in the corpus whose Confluence body is genuinely empty (harvest §3.2).
EMPTY_BODY_ALLOWED = {"KIP-929"}

TEXT_ONLY_TARGET_PCT = 20.0


def _counts(records: dict[str, list[BaseModel]]) -> dict[str, Any]:
    by_source: Counter = Counter()
    for name in ("workitems", "documents"):
        by_source.update(str(getattr(r, "source", "?")) for r in records[name])
    by_source.update("git" for _ in records["changes"])
    by_source.update(r.identities[0].source for r in records["persons"])
    by_source.update(str(getattr(r, "source", "?")) for r in records["containers"])
    return {
        "by_type": {name: len(rows) for name, rows in records.items()},
        "by_source": dict(sorted(by_source.items())),
        "documents_by_kind": dict(Counter(r.kind for r in records["documents"]).most_common()),
        "changes_by_kind": dict(Counter(r.kind for r in records["changes"]).most_common()),
        "containers_by_kind": dict(Counter(r.kind for r in records["containers"]).most_common()),
        "persons_by_source": dict(
            Counter(r.identities[0].source for r in records["persons"]).most_common()
        ),
        "unique_ids": {name: len({r.id for r in rows}) for name, rows in records.items()},
    }


def _checks(records: dict[str, list[BaseModel]]) -> list[dict[str, Any]]:
    workitems = records["workitems"]
    documents = records["documents"]
    changes = records["changes"]

    no_component = [w.key for w in workitems if not w.components]
    empty_body = [d.key for d in documents if not d.body_md and d.key not in EMPTY_BODY_ALLOWED]
    no_at = [c.id for c in changes if c.at is None]
    text_only = [
        w.key for w in workitems if any(r.kind == "issue" and r.via == "text" for r in w.refs)
    ]
    pct = round(100 * len(text_only) / (len(workitems) or 1), 1)

    def check(name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        return {"name": name, "ok": ok, **detail}

    return [
        check(
            "every_workitem_has_a_component",
            not no_component,
            without=len(no_component),
            sample=no_component[:10],
        ),
        check(
            "every_document_has_a_body",
            not empty_body,
            empty=len(empty_body),
            allowed_empty=sorted(EMPTY_BODY_ALLOWED),
            sample=empty_body[:10],
        ),
        check("every_change_has_at", not no_at, without=len(no_at)),
        check(
            "text_only_issue_refs_at_least_20pct",
            pct >= TEXT_ONLY_TARGET_PCT,
            workitems_with_text_only_issue_ref=len(text_only),
            pct=pct,
            target_pct=TEXT_ONLY_TARGET_PCT,
        ),
        *[
            check(
                f"unique_ids_{name}",
                len({r.id for r in rows}) == len(rows),
                records=len(rows),
                unique=len({r.id for r in rows}),
            )
            for name, rows in records.items()
        ],
    ]


def _merge_source_blocks(
    existing: dict[str, Any] | None, key: str, fresh: dict[str, Any]
) -> dict[str, Any]:
    block = dict((existing or {}).get(key) or {})
    block.update(fresh)
    return dict(sorted(block.items()))


def build_report(
    bundles: dict[str, Bundle],
    *,
    records: dict[str, list[BaseModel]],
    slices: dict[str, dict[str, Any]],
    sources: Sequence[str],
    durations: dict[str, float],
    duration_s: float,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    refs_by_source = {name: b.refs.report() for name, b in bundles.items()}
    unmapped = {name: b.stats.get("unmapped_fields", {}) for name, b in bundles.items()}
    stats = {
        name: {k: v for k, v in b.stats.items() if k != "unmapped_fields"}
        for name, b in bundles.items()
    }
    warnings = [w for b in bundles.values() for w in b.warnings]

    report: dict[str, Any] = {
        "step": "canon",
        "generated_at": utc_now_iso(),
        "last_run": {
            "sources": list(sources),
            "duration_s": round(duration_s, 2),
            "per_source_s": durations,
        },
        "counts": _counts(records),
        "dedupe": _merge_source_blocks(existing, "dedupe", slices),
        "source_stats": _merge_source_blocks(existing, "source_stats", stats),
        # `existing["refs"]` is already a built block, so the merge reaches into its
        # per-source map rather than folding the aggregates back in as if they were sources.
        "refs": _refs_block(
            _merge_source_blocks(
                {"refs": ((existing or {}).get("refs") or {}).get("by_source") or {}},
                "refs",
                refs_by_source,
            )
        ),
        "kip_key": (
            bundles["confluence"].stats.get("kip_key")
            if "confluence" in bundles
            else (existing or {}).get("kip_key")
        ),
        "unmapped_fields": _merge_source_blocks(existing, "unmapped_fields", unmapped),
        "checks": _checks(records),
        "warnings": {
            "counts": dict(Counter(w["code"] for w in warnings).most_common()),
            "sample": warnings[:50],
        },
        "limitations": sorted(
            {line for b in bundles.values() for line in b.stats.get("limitations", [])}
            | set((existing or {}).get("limitations") or [])
        ),
    }
    return report


def _refs_block(by_source: dict[str, Any]) -> dict[str, Any]:
    """Per-source ref audits plus the totals, from whatever sources the report knows."""
    per_source = {k: v for k, v in by_source.items() if isinstance(v, dict) and "via_link" in v}
    kinds: Counter = Counter()
    removed: Counter = Counter()
    for block in per_source.values():
        kinds.update(block.get("kept_by_kind") or {})
        removed.update({k: n for k, n in block.get("top_removed") or []})
    return {
        "by_source": dict(sorted(per_source.items())),
        "totals_by_kind": dict(sorted(kinds.items())),
        "total": sum(kinds.values()),
        "via_link": sum(b.get("via_link", 0) for b in per_source.values()),
        "via_text": sum(b.get("via_text", 0) for b in per_source.values()),
        "removed_total": sum(b.get("removed_total", 0) for b in per_source.values()),
        # Merged from the per-source top-20s, so it is exact for a full `--source all` run.
        "top_removed": [[k, n] for k, n in removed.most_common(20)],
    }


def summarize(report: dict[str, Any]) -> str:
    counts = report["counts"]["by_type"]
    refs = report["refs"]
    lines = [
        "canon: "
        + ", ".join(f"{n} {name}" for name, n in counts.items())
        + f" in {report['last_run']['duration_s']}s",
        f"refs: {refs['total']} kept "
        f"({refs['via_link']} via link, {refs['via_text']} via text), "
        f"{refs['removed_total']} filtered out",
    ]
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    lines.append("checks: all passed" if not failed else f"checks FAILED: {', '.join(failed)}")
    return "\n".join(lines)


def write_report(path: Path, report: dict[str, Any]) -> None:
    write_json_atomic(path, report)
