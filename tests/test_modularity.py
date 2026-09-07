"""The Task 10 acceptance, as a test: config moved, corpus did not.

Two digests are pinned, for two different reasons.

`MINI_SHA1` is the committed mini fixture. It runs anywhere — a fresh clone, CI, another
machine — and it is what fails if a mapper, the allowlist or the title pattern quietly
changes what canon produces.

The real corpus lives in `data/`, which is not committed, so its digests are pinned in
`brain/modularity.BASELINE_SHA1` and checked here only when the directory exists. That is
a weaker test in exchange for covering 45 MB of real records instead of 20 lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.modularity import (
    BASELINE_SHA1,
    build_report,
    canonical_digests,
    registry_facts,
    synthetic_counts,
    write_report,
)

MINI = Path("data/fixtures/mini")
REAL = Path("data/canonical")

#: sha1 of every file in `data/fixtures/mini`, recorded 2026-09-07 with the registry in
#: place. These are committed data: a change here is a change to the fixture, and any
#: change to the fixture must be deliberate.
MINI_SHA1: dict[str, str] = {
    "workitems.jsonl": "418c8cf10ecb8ece933a87f70a0b0dfa9501e195",
    "documents.jsonl": "3a8e9fe021a9a4bd40acfc4cf05a9f9551a53fb0",
    "persons.jsonl": "ecb40dacecd09725377862fcd146485cb37cb590",
    "changes.jsonl": "c9e3d8704f646409dade41c66681fb5a1e393168",
    "containers.jsonl": "622214cbfd3d54086ae016ca433042bbddc82311",
}

#: The query signatures `sources.yaml` resolves to. Pinned twice on purpose — here and in
#: `tests/test_registry.py` — because this is the number that decides whether an existing
#: `data/raw/` checkpoint is still valid, and it must be impossible to change by accident.
SIGNATURES = {
    "jira": "5da028abe2be",
    "confluence": "162b225214ef",
    "git": "c7899d52babc",
}


def test_the_mini_fixture_has_not_moved():
    """The corpus a fresh clone can check. Update only with a deliberate fixture change."""
    got = {name: entry["sha1"] for name, entry in canonical_digests(MINI).items()}
    assert set(got) == set(MINI_SHA1)
    assert got == MINI_SHA1


def test_the_report_names_every_drifting_file():
    """A digest mismatch must say which file, not just that something moved."""
    report = build_report(MINI)
    assert report["canonical"]["matches_baseline"] is False  # the fixture is not the corpus
    assert set(report["canonical"]["drift"]) == set(MINI_SHA1)
    for name, entry in report["canonical"]["drift"].items():
        assert entry["expected"] == BASELINE_SHA1[name]
        assert entry["got"] == MINI_SHA1[name]


def test_the_registry_facts_are_the_configured_ones():
    facts = registry_facts()
    assert facts["query_signatures"] == SIGNATURES
    assert facts["issue_project_allowlist"] == ["ADO", "KAFKA", "XE", "XP", "XS", "XT"]
    assert facts["issue_key_blacklist"] == ["KAFKA-1"]
    assert facts["document"] == {
        "kind": "KIP",
        "title_pattern": r"KIP-(\d+)",
        "title_pattern_loose": r"KIP\s+(\d+)",
        "key_format": "KIP-{number}",
    }
    assert [s["id"] for s in facts["sources"] if s["enabled"]] == ["jira", "confluence", "git"]
    # The registry allows two sources of one type; this corpus has none, and says so.
    assert facts["same_type_ids"] == {}
    assert all(s["display_name"] for s in facts["sources"])
    # Every disabled source that still contributes keys is named, not silently folded in.
    assert len(facts["allowlist_warnings"]) == 2


def test_synthetic_counts_read_the_fixture_without_writing_it():
    before = {p.name: p.read_bytes() for p in sorted(MINI.glob("*.jsonl"))}
    counts = synthetic_counts(MINI)
    assert counts["records"]["workitems"] == 4
    assert counts["records_total"] == 4
    assert {p.name: p.read_bytes() for p in sorted(MINI.glob("*.jsonl"))} == before


def test_the_report_is_written_where_every_other_step_reports(tmp_path):
    path, report = write_report(MINI, tmp_path)
    assert path == tmp_path / "modularity.json"
    assert json.loads(path.read_text(encoding="utf-8"))["step"] == "modularity"
    assert set(report) == {"step", "generated_at", "canonical", "synthetic", "registry"}


@pytest.mark.skipif(not REAL.is_dir(), reason="no harvested corpus on this machine")
def test_the_real_corpus_still_matches_the_pre_refactor_digests():
    """The acceptance criterion: registry-driven canon is byte-identical.

    Skipped on a machine that has never harvested — `data/` is not committed. Where it
    does run, it is the only check that covers the real 45 MB.
    """
    report = build_report(REAL)
    assert report["canonical"]["drift"] == {}
    assert report["canonical"]["matches_baseline"] is True
