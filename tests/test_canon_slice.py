"""`slice` — which pull a canonical record came from, and what that costs on disk.

The rules under test, in one line each:

* first seen in `since-<date>/` → `incremental`; first seen in the base directory → `base`,
  even when an incremental copy later won the dedupe (that is an *update*, not an addition);
* a `Person` or a `Container` any base record names is `base`, whoever else names it;
* a `base` record serializes **byte-identically** to one written before the field existed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from brain.canon.mappers.git import map_commits
from brain.canon.mappers.jira import map_issues
from brain.canon.models import WorkItem
from brain.canon.raw import load_source
from brain.canon.runner import run_canon, slice_census
from tests.canon_helpers import commit, issue, plant_commits, plant_pages

NEW = {"created": "2026-01-05T00:00:00.000+0000", "updated": "2026-01-06T00:00:00.000+0000"}


def canonical(tmp_path, name: str) -> list[dict]:
    path = tmp_path / "canonical" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run(tmp_path, sources=("jira",)):
    for d in ("canonical", "reports"):
        (tmp_path / d).mkdir(exist_ok=True)
    return run_canon(
        list(sources),
        raw_dir=tmp_path,
        canonical_dir=tmp_path / "canonical",
        reports_dir=tmp_path / "reports",
        echo=lambda _m: None,
    )


# --------------------------------------------------------------------------- raw


def test_a_record_only_the_since_directory_has_is_incremental(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1")])
    plant_pages(tmp_path, "jira", [issue("KAFKA-9001", **NEW)], since="2026-01-01")

    raw = load_source(tmp_path, "jira")

    assert raw.slice_of() == {"KAFKA-9001": "incremental"}
    assert raw.stats()["by_slice"] == {"base": 1, "incremental": 1}


def test_a_record_the_base_pull_had_stays_base_even_when_the_increment_supersedes_it(tmp_path):
    """An update to a base record is not an addition, and a slice reset must not eat it."""
    plant_pages(tmp_path, "jira", [issue("KAFKA-1", updated="2024-02-01T00:00:00.000+0000")])
    plant_pages(
        tmp_path,
        "jira",
        [issue("KAFKA-1", updated="2026-02-01T00:00:00.000+0000", summary="reworded")],
        since="2026-01-01",
    )

    raw = load_source(tmp_path, "jira")

    assert raw.records[0]["fields"]["summary"] == "reworded"  # the newer copy won…
    assert raw.slice_of() == {}  # …and the record is still base
    assert raw.stats()["superseded_by_incremental"] == 1


def test_no_since_directory_means_no_slice_table_at_all(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1"), issue("KAFKA-2")])
    assert load_source(tmp_path, "jira").slice_of() == {}


# --------------------------------------------------------------------------- mappers


def test_the_mapper_stamps_the_work_item_and_the_containers_it_alone_names():
    bundle = map_issues(
        [
            issue("KAFKA-1", components=[{"name": "clients"}]),
            issue("KAFKA-9001", components=[{"name": "clients"}, {"name": "kraft"}], **NEW),
        ],
        slice_of={"KAFKA-9001": "incremental"},
    )

    assert {w.key: w.slice for w in bundle.workitems} == {
        "KAFKA-1": "base",
        "KAFKA-9001": "incremental",
    }
    # `clients` is named by a base issue too, so it is not part of the increment.
    assert bundle.containers["jira:component:clients"].slice == "base"
    assert bundle.containers["jira:component:kraft"].slice == "incremental"


def test_an_identity_a_base_issue_also_reports_is_not_part_of_the_increment():
    bundle = map_issues(
        [
            issue("KAFKA-1", assignee={"name": "mjsax", "displayName": "M"}),
            issue("KAFKA-9001", assignee={"name": "mjsax"}, reporter={"name": "newbie"}, **NEW),
        ],
        slice_of={"KAFKA-9001": "incremental"},
    )

    assert bundle.persons["jira:mjsax"].slice == "base"
    assert bundle.persons["jira:newbie"].slice == "incremental"


def test_a_past_assignee_only_an_incremental_issues_history_names_is_incremental():
    history = {
        "histories": [
            {
                "id": "1",
                "created": "2026-01-06T00:00:00.000+0000",
                "items": [
                    {
                        "field": "assignee",
                        "from": "ghost",
                        "fromString": "Ghost",
                        "to": None,
                        "toString": None,
                    }
                ],
            }
        ]
    }
    bundle = map_issues(
        [issue("KAFKA-1"), {**issue("KAFKA-9001", **NEW), "changelog": history}],
        slice_of={"KAFKA-9001": "incremental"},
    )
    assert bundle.persons["jira:ghost"].slice == "incremental"


def test_a_pull_request_inherits_the_slice_of_the_commit_that_owns_it():
    bundle = map_commits(
        [commit("a" * 40, "KAFKA-1: old (#100)"), commit("b" * 40, "KAFKA-9001: new (#200)")],
        base_url="https://github.com/apache/kafka",
        slice_of={"b" * 40: "incremental"},
    )
    assert {c.id: c.slice for c in bundle.changes} == {
        "a" * 40: "base",
        "b" * 40: "incremental",
        "pr:100": "base",
        "pr:200": "incremental",
    }


def test_a_mapper_called_without_the_table_produces_only_base_records():
    bundle = map_issues([issue("KAFKA-1"), issue("KAFKA-2")])
    assert {w.slice for w in bundle.workitems} == {"base"}
    assert {p.slice for p in bundle.persons.values()} <= {"base"}


# --------------------------------------------------------------------------- serialization


def test_a_base_record_serializes_without_the_field_at_all():
    item = WorkItem(
        id="jira:KAFKA-1",
        key="KAFKA-1",
        source="jira",
        type="Bug",
        title="t",
        status="Open",
        created=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert "slice" not in item.model_dump(mode="json")
    item.slice = "incremental"
    assert item.model_dump(mode="json")["slice"] == "incremental"


def test_the_canonical_bytes_of_a_base_only_corpus_do_not_move(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1"), issue("KAFKA-2")])
    run(tmp_path)
    before = {
        name: hashlib.sha256((tmp_path / "canonical" / f"{name}.jsonl").read_bytes()).hexdigest()
        for name in ("workitems", "persons", "containers")
    }
    assert all('"slice"' not in (tmp_path / "canonical" / f"{n}.jsonl").read_text() for n in before)
    run(tmp_path)
    after = {
        name: hashlib.sha256((tmp_path / "canonical" / f"{name}.jsonl").read_bytes()).hexdigest()
        for name in before
    }
    assert before == after


# --------------------------------------------------------------------------- `brain canon`


def test_canon_appends_the_increment_and_leaves_the_base_records_alone(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1"), issue("KAFKA-2")])
    run(tmp_path)
    base_lines = (tmp_path / "canonical" / "workitems.jsonl").read_bytes()

    plant_pages(tmp_path, "jira", [issue("KAFKA-9001", **NEW)], since="2026-01-01")
    report, code = run(tmp_path)

    assert code == 0
    rows = canonical(tmp_path, "workitems")
    assert [r["key"] for r in rows] == ["KAFKA-1", "KAFKA-2", "KAFKA-9001"]
    assert [r.get("slice", "base") for r in rows] == ["base", "base", "incremental"]
    # the two base lines are the same bytes they were before the increment arrived
    now = (tmp_path / "canonical" / "workitems.jsonl").read_bytes()
    assert now.splitlines()[:2] == base_lines.splitlines()
    assert report["slices"]["workitems"] == {"base": 2, "incremental": 1}
    assert report["slices"]["total"]["incremental"] == 1


def test_canon_is_idempotent_over_an_increment(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1")])
    plant_pages(tmp_path, "jira", [issue("KAFKA-9001", **NEW)], since="2026-01-01")
    run(tmp_path)
    first = {
        name: (tmp_path / "canonical" / f"{name}.jsonl").read_bytes()
        for name in ("workitems", "persons", "containers")
    }
    run(tmp_path)
    assert {
        name: (tmp_path / "canonical" / f"{name}.jsonl").read_bytes() for name in first
    } == first


def test_a_later_full_pull_that_covers_the_increment_makes_it_base_again(tmp_path):
    """The window moved: the record is inside the corpus now, and stops being an increment."""
    plant_pages(tmp_path, "jira", [issue("KAFKA-9001", **NEW)], since="2026-01-01")
    run(tmp_path)
    assert canonical(tmp_path, "workitems")[0]["slice"] == "incremental"

    plant_pages(tmp_path, "jira", [issue("KAFKA-9001", **NEW)])
    run(tmp_path)
    assert "slice" not in canonical(tmp_path, "workitems")[0]


def test_git_commits_carry_the_slice_through_canon(tmp_path):
    plant_commits(tmp_path, [commit("a" * 40, "KAFKA-1: old (#100)")])
    plant_commits(tmp_path, [commit("b" * 40, "KAFKA-9001: new (#200)")], since="2026-01-01")
    run(tmp_path, sources=("git",))

    rows = {r["id"]: r.get("slice", "base") for r in canonical(tmp_path, "changes")}
    assert rows == {
        "a" * 40: "base",
        "pr:100": "base",
        "b" * 40: "incremental",
        "pr:200": "incremental",
    }


def test_the_census_counts_what_was_written_not_what_was_read():
    records = {
        "workitems": [
            WorkItem(
                id="jira:A",
                key="A",
                source="jira",
                type="Bug",
                title="t",
                status="Open",
                created=datetime(2024, 1, 1, tzinfo=UTC),
                slice="incremental",
            )
        ],
        "documents": [],
    }
    assert slice_census(records) == {
        "workitems": {"base": 0, "incremental": 1},
        "documents": {"base": 0, "incremental": 0},
        "total": {"base": 0, "incremental": 1},
    }
