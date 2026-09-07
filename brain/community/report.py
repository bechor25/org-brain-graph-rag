"""`data/reports/communities.json` — one file, three sections, written days apart.

`build`, `batches` and `merge` are three commands with agent work between them, and the
conventions ask for one report per step. So each command read-modify-writes its own
section, exactly as `brain extract` does, and the file is always the whole step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.harvest.base import utc_now_iso, write_json_atomic

STEP = "communities"
REPORT_NAME = "communities.json"
SECTIONS: tuple[str, ...] = ("build", "batches", "merge")


def report_path(reports_dir: Path) -> Path:
    return reports_dir / REPORT_NAME


def load_report(reports_dir: Path) -> dict[str, Any]:
    path = report_path(reports_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_section(reports_dir: Path, section: str, payload: dict[str, Any]) -> Path:
    """Replace one section, keep the others. Never rewrites a section it was not given."""
    if section not in SECTIONS:
        raise ValueError(f"{section!r} is not one of {list(SECTIONS)}")
    report = load_report(reports_dir)
    report["step"] = STEP
    report["generated_at"] = utc_now_iso()
    report[section] = payload
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(report_path(reports_dir), report)
    return report_path(reports_dir)
