"""`data/reports/modularity.json` — the evidence that Task 10 changed nothing it touched.

ADR-0005 moved every URL, query, project key and title pattern out of Python and into
`sources.yaml`. The claim that has to survive review is not "the tests pass", it is
**"the canonical corpus is byte-for-byte what it was"** — a registry that builds an
equivalent-but-different query produces a corpus that is *almost* the same, and almost is
invisible until a retrieval answer cites a key that moved.

So this records three measurements, in the same place every other step reports:

* ``canonical`` — sha256 and record count of each of the five canonical files, next to
  the digests recorded before the refactor. Any difference is a regression, and the
  report says which file.
* ``synthetic`` — how much of the corpus `brain reset --synthetic` would remove, per
  file, plus the resolution-ledger rows that name a synthetic identity.
* ``registry`` — what the config actually resolved to: the sources, the allowlist, the
  document pattern, and the query signatures the connectors would use.

`data/` is not committed, so the digests of the real corpus live *here*, in a report, and
the digests of the committed mini fixture are pinned in `tests/test_modularity.py`. The
fixture is what a fresh clone can check; this file is what this machine's corpus is.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.harvest.registry import Registry, get_registry
from brain.reset import CANONICAL_FILES, prune_resolution_ledger, synthetic_person_ids

REPORT_NAME = "modularity.json"

#: The corpus as it stood before the registry refactor (2026-09-06 canon run, 1,416 real
#: work items + the synthetic layer). `brain canon` through `sources.yaml` reproduces
#: every one of these; a mismatch is the regression this report exists to catch.
BASELINE_SHA1: dict[str, str] = {
    "changes.jsonl": "c4870ae497cc240196997a8e5d29c52eeef0fad1",
    "containers.jsonl": "4bb4dc6bd9f6b779f2ecc909ae8faa1e8974101e",
    "documents.jsonl": "4e74e1a0df814bfd7487580cba7f278c10d5276d",
    "persons.jsonl": "73ae016f6c5f7695c8051bb71fdd0d4ac1430bae",
    "workitems.jsonl": "3472864688c1f933bea89786489ea02ccac04100",
}


def digest(path: Path) -> dict[str, Any]:
    """sha1, sha256 and the record count of one canonical file.

    sha1 because that is what `shasum` prints and what the step reports quoted; sha256
    because `brain load` already records canonical inputs that way and the two reports
    should be comparable without re-reading 45 MB.
    """
    data = path.read_bytes()
    return {
        "sha1": hashlib.sha1(data).hexdigest(),  # noqa: S324 - a file identity, not a MAC
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "records": data.count(b"\n"),
    }


def canonical_digests(canonical_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in CANONICAL_FILES:
        path = canonical_dir / f"{name}.jsonl"
        if path.is_file():
            out[path.name] = digest(path)
    return out


def synthetic_counts(canonical_dir: Path) -> dict[str, Any]:
    """What `brain reset --synthetic` would remove. Counts only — nothing is written."""
    per_file: dict[str, int] = {}
    for name in CANONICAL_FILES:
        path = canonical_dir / f"{name}.jsonl"
        if not path.is_file():
            continue
        # Iterating the handle, never `splitlines()`: the canonical files are written
        # with `ensure_ascii=False`, so a description containing U+2028 (LINE SEPARATOR)
        # or U+0085 lands in the file raw — and `str.splitlines()` breaks on both, cutting
        # a JSON record in half. Real records in this corpus do exactly that.
        with path.open(encoding="utf-8") as handle:
            per_file[name] = sum(
                1 for line in handle if line.strip() and json.loads(line).get("synthetic")
            )
    return {
        "records": per_file,
        "records_total": sum(per_file.values()),
        "resolution_ledger_rows": prune_resolution_ledger(
            canonical_dir, synthetic_person_ids(canonical_dir), apply=False
        ),
    }


def registry_facts(registry: Registry | None = None) -> dict[str, Any]:
    """What the config resolved to, including the signatures the connectors would send."""
    from brain.harvest.runner import CONNECTORS, build_connector

    reg = registry or get_registry()
    spec = reg.document_spec_default()
    signatures: dict[str, str] = {}
    for source in reg.enabled():
        if source.type in CONNECTORS:
            signatures[source.name] = build_connector(source, Path("data/raw")).signature(None)
    return {
        "path": str(reg.path),
        "sources": [
            {
                "name": s.name,
                "type": s.type,
                "display_name": s.display_name,
                "enabled": s.enabled,
                "project_keys": list(s.project_keys),
                "auth_env": s.auth_env,
                "auth_scheme": s.auth_scheme,
            }
            for s in reg.sources
        ],
        "issue_project_allowlist": sorted(reg.issue_project_allowlist()),
        "issue_key_blacklist": sorted(reg.issue_key_blacklist),
        "allowlist_warnings": reg.allowlist_warnings(),
        "synthetic_key_prefixes": dict(sorted(reg.synthetic_prefixes.items())),
        "document": {
            "kind": spec.kind,
            "title_pattern": spec.pattern.pattern,
            "title_pattern_loose": spec.loose.pattern if spec.loose else None,
            "key_format": spec.key_format,
        },
        "query_signatures": signatures,
    }


def build_report(canonical_dir: Path, registry: Registry | None = None) -> dict[str, Any]:
    digests = canonical_digests(canonical_dir)
    drift = {
        name: {"expected": BASELINE_SHA1[name], "got": entry["sha1"]}
        for name, entry in digests.items()
        if name in BASELINE_SHA1 and entry["sha1"] != BASELINE_SHA1[name]
    }
    return {
        "step": "modularity",
        "generated_at": utc_now_iso(),
        "canonical": {
            "dir": str(canonical_dir),
            "files": digests,
            "baseline_sha1": BASELINE_SHA1,
            "matches_baseline": not drift,
            "drift": drift,
        },
        "synthetic": synthetic_counts(canonical_dir),
        "registry": registry_facts(registry),
    }


def write_report(canonical_dir: Path, reports_dir: Path) -> tuple[Path, dict[str, Any]]:
    report = build_report(canonical_dir)
    path = reports_dir / REPORT_NAME
    write_json_atomic(path, report)
    return path, report
