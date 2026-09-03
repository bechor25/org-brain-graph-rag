"""Fixture helpers shared by the canon tests: plant raw runs the way harvest writes them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "canon"


def load_fixture(*parts: str) -> Any:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def plant_pages(
    raw_dir: Path,
    source: str,
    records: list[dict[str, Any]],
    *,
    since: str | None = None,
    name: str | None = None,
) -> Path:
    """Write one raw page plus the checkpoint that lists it (never a bare page file)."""
    run_dir = raw_dir / source / (f"since-{since}" if since else "")
    run_dir.mkdir(parents=True, exist_ok=True)
    key = "issues" if source == "jira" else "results"
    name = name or ("issues-0000.json" if source == "jira" else "pages-0000.json")
    (run_dir / name).write_text(json.dumps({key: records}), encoding="utf-8")
    (run_dir / "checkpoint.json").write_text(
        json.dumps({"source": source, "signature": "sig", "files": [name], "done": True}),
        encoding="utf-8",
    )
    return run_dir


def plant_commits(
    raw_dir: Path, commits: list[dict[str, Any]], *, since: str | None = None
) -> Path:
    run_dir = raw_dir / "git" / (f"since-{since}" if since else "")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "commits.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in commits), encoding="utf-8"
    )
    (run_dir / "checkpoint.json").write_text(
        json.dumps({"source": "git", "files": ["commits.jsonl"], "done": True}), encoding="utf-8"
    )
    return run_dir


def issue(key: str, **fields: Any) -> dict[str, Any]:
    """A minimal raw Jira issue: only what the mapper requires, plus what a test sets."""
    base: dict[str, Any] = {
        "summary": f"summary of {key}",
        "created": "2024-01-01T00:00:00.000+0000",
        "updated": "2024-02-01T00:00:00.000+0000",
        "issuetype": {"name": "Bug"},
        "status": {"name": "Open"},
        "components": [{"name": "clients"}],
    }
    base.update(fields)
    return {
        "id": key.split("-")[-1],
        "key": key,
        "self": f"https://issues.apache.org/jira/rest/api/2/issue/{key}",
        "fields": base,
    }


def page(page_id: str, title: str, body: str = "<p>body</p>", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": page_id,
        "title": title,
        "body": {"storage": {"value": body}},
        "version": {"number": 1, "when": "2024-01-01T00:00:00.000Z", "by": {"username": "editor"}},
        "history": {
            "createdDate": "2023-01-01T00:00:00.000Z",
            "createdBy": {"userKey": "author-key", "displayName": "Author"},
        },
        "_links": {
            "self": f"https://cwiki.apache.org/confluence/rest/api/content/{page_id}",
            "webui": f"/spaces/KAFKA/pages/{page_id}/x",
        },
    }
    base.update(over)
    return base


def commit(sha: str, subject: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sha": sha,
        "author_name": "Jun Rao",
        "author_email": "Jun@example.com",
        "authored_at": "2024-03-01T10:00:00+02:00",
        "subject": subject,
        "body": "",
        "files": ["core/src/A.java"],
    }
    base.update(over)
    return base
