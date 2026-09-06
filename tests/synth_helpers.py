"""Fixture helpers for the synthetic-layer tests: a tiny real corpus and batch outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REAL_PERSONS = [
    {
        "id": "jira:jrao",
        "identities": [{"source": "jira", "key": "jrao", "display": "Jun Rao", "email": None}],
        "resolved": False,
        "synthetic": False,
    },
    {
        "id": "jira:dlee",
        "identities": [{"source": "jira", "key": "dlee", "display": "Dana Lee", "email": None}],
        "resolved": False,
        "synthetic": False,
    },
]

REAL_CONTAINERS = [
    {
        "id": "jira:component:clients",
        "source": "jira",
        "kind": "component",
        "name": "clients",
        "parent": None,
        "synthetic": False,
    },
    {
        "id": "jira:version:3.7.0",
        "source": "jira",
        "kind": "version",
        "name": "3.7.0",
        "parent": None,
        "synthetic": False,
    },
]

REAL_DOCUMENTS = [
    {
        "id": "confluence:1",
        "key": "KIP-5",
        "source": "confluence",
        "kind": "KIP",
        "title": "KIP-5: Next generation consumer group protocol",
        "body_md": "## Motivation\nThe current protocol rebalances too often. " * 30,
        "version": 1,
        "created": "2023-01-01T00:00:00Z",
        "updated": "2023-02-01T00:00:00Z",
        "author": "jrao",
        "ancestors": [],
        "kip_of": None,
        "labels": [],
        "refs": [],
        "synthetic": False,
        "raw_url": None,
    }
]


def real_item(key: str, **over: Any) -> dict[str, Any]:
    """A real Jira WorkItem as canon writes it. Eligible for selection unless overridden."""
    base: dict[str, Any] = {
        "id": f"jira:{key}",
        "key": key,
        "source": "jira",
        "type": "Improvement",
        "title": f"Title of {key}",
        "description": f"Description of {key}. Mentions KIP-5.",
        "status": "Resolved",
        "resolution": "Fixed",
        "resolved_at": "2024-03-01T00:00:00Z",
        "priority": "Major",
        "created": "2024-01-10T09:00:00Z",
        "updated": "2024-03-02T10:00:00Z",
        "reporter": "jrao",
        "assignee": "dlee",
        "components": ["clients"],
        "labels": [],
        "fix_versions": ["3.7.0"],
        "affects_versions": [],
        "parent": None,
        "links": [],
        "comments": [{"author": "dlee", "at": "2024-02-01T12:00:00Z", "body": "Patch is up."}],
        "changelog": [],
        "refs": [{"kind": "kip", "key": "KIP-5", "via": "text"}],
        "synthetic": False,
        "raw_url": None,
    }
    base.update(over)
    return base


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def plant_canonical(
    canonical_dir: Path,
    *,
    workitems: list[dict[str, Any]] | None = None,
    persons: list[dict[str, Any]] | None = None,
    containers: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> Path:
    """A minimal `data/canonical/` a build or merge run can read."""
    write_jsonl(
        canonical_dir / "workitems.jsonl",
        workitems if workitems is not None else [real_item("KAFKA-100"), real_item("KAFKA-101")],
    )
    write_jsonl(canonical_dir / "persons.jsonl", persons if persons is not None else REAL_PERSONS)
    write_jsonl(
        canonical_dir / "containers.jsonl",
        containers if containers is not None else REAL_CONTAINERS,
    )
    write_jsonl(
        canonical_dir / "documents.jsonl", documents if documents is not None else REAL_DOCUMENTS
    )
    return canonical_dir


# --------------------------------------------------------------------------- batch output


def xray_test(key: str, *, covers: str | None = "KAFKA-100", **over: Any) -> dict[str, Any]:
    """A synthetic Xray Test. `covers=None` leaves it without a formal `tests` link."""
    base: dict[str, Any] = {
        "id": f"xray:{key}",
        "key": key,
        "source": "xray",
        "type": "Test",
        "title": f"{key} verifies the assignment path",
        "description": "1) start broker 2) join group 3) assert assignment within 5s.",
        "status": "Active",
        "created": "2024-02-15T09:00:00Z",
        "reporter": "rao.jun",
        "components": ["clients"],
        "links": [{"type": "tests", "target": covers}] if covers else [],
        "refs": [],
        "synthetic": True,
    }
    base.update(over)
    return base


def ado_item(key: str, item_type: str = "User Story", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"ado:{key}",
        "key": key,
        "source": "ado",
        "type": item_type,
        "title": f"{key} delivery item",
        "description": "Delivers the client side of the new protocol.",
        "status": "Active",
        "created": "2024-01-20T09:00:00Z",
        "reporter": "rao.jun",
        "links": [{"type": "related", "target": "KAFKA-100"}],
        "refs": [],
        "synthetic": True,
    }
    base.update(over)
    return base


def synthetic_person(key: str, real_id: str, display: str, source: str = "ado") -> dict[str, Any]:
    return {
        "id": f"{source}:{key}",
        "identities": [{"source": source, "key": key, "display": display, "email": None}],
        "resolved": False,
        "synthetic": True,
    }


def truth(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identity_map": {"ado:rao.jun": "jira:jrao"},
        "text_only_links": [],
        "stale_states": [],
        "renames": [],
        "duplicate_tests": [],
    }
    base.update(over)
    return base


def batch_output(batch_id: str = "shard-01/001", **over: Any) -> dict[str, Any]:
    """A minimal valid `NNN.out.json`: one Test, one Story, one new identity."""
    base: dict[str, Any] = {
        "batch_id": batch_id,
        "workitems": [xray_test("XT-10001"), ado_item("ADO-10001")],
        "containers": [],
        "persons": [synthetic_person("rao.jun", "jira:jrao", "Rao, Jun")],
        "truth": truth(),
    }
    base.update(over)
    return base


def write_output(root: Path, payload: dict[str, Any]) -> Path:
    """Write `<batches>/synthetic/<shard>/NNN.out.json` for the payload's own batch_id."""
    shard, index = payload["batch_id"].split("/")
    path = root / "synthetic" / shard / f"{index}.out.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def read_jsonl_raw(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
