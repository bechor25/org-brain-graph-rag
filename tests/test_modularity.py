"""The Task 10 acceptance, as a test: config moved, corpus did not.

Three digests are pinned, and only one of them is a claim about canon.

`GOLDEN_SHA1` is the real one. It runs `brain canon` end to end on the raw fixtures in
`tests/fixtures/canon/` — planted the way harvest writes them — and digests the five files
that come out. That is what fails when a mapper, the issue-key allowlist, the title
pattern, the sort order or the record model changes what canon produces, and it runs on a
fresh clone with no database and no harvest.

`MINI_SHA1` is **not** that. `data/fixtures/mini` is a hand-written canonical corpus, not
canon output: comparing it to a constant proves only that nobody edited the committed
file. That still matters — it is what `make smoke` and the live load tests run on, so a
silent edit would move every live assertion — but it says nothing about the mappers.

The real corpus lives in `data/`, which is not committed, so its digests are pinned in
`brain/modularity.BASELINE_SHA1` and checked here only when the directory exists. That is
a weaker test in exchange for covering 45 MB of real records instead of 20 lines, and the
report's `rerun` section is what turns it into a statement about today's code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.canon.runner import run_canon
from brain.modularity import (
    BASELINE_SHA1,
    build_report,
    canonical_digests,
    redaction_check,
    registry_facts,
    same_type_check,
    synthetic_counts,
    write_report,
)
from tests.canon_helpers import load_fixture, plant_commits, plant_pages

MINI = Path("data/fixtures/mini")
REAL = Path("data/canonical")

#: sha1 of every file in `data/fixtures/mini`, recorded 2026-09-07. These are committed
#: *data*, not canon output: a change here is a change to the fixture, and any change to
#: the fixture must be deliberate because `make smoke` asserts against its contents.
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
    """The committed load corpus, unchanged. Not a canon check — see the golden test."""
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
    assert set(report) == {
        "step",
        "generated_at",
        "canonical",
        "synthetic",
        "redaction",
        "same_type_sources",
        "registry",
    }


@pytest.mark.skipif(not REAL.is_dir(), reason="no harvested corpus on this machine")
def test_the_real_corpus_still_matches_the_pre_refactor_digests():
    """The acceptance criterion: registry-driven canon is byte-identical.

    Skipped on a machine that has never harvested — `data/` is not committed. Where it
    does run, it is the only check that covers the real 45 MB.
    """
    report = build_report(REAL)
    assert report["canonical"]["drift"] == {}
    assert report["canonical"]["matches_baseline"] is True


# ---------------------------------------------------------------- the real canon golden

#: sha1 of the five files `run_canon` writes from the raw fixtures in
#: `tests/fixtures/canon/`. Unlike `MINI_SHA1` these are *produced*, so they move when the
#: mappers, the allowlist, the title pattern, the sort order or the record model move —
#: which is the whole point. Regenerate deliberately, and read the diff before pinning.
GOLDEN_SHA1: dict[str, str] = {
    "workitems.jsonl": "4870a4cb877373a96ad324fedd3a4c025b1de91f",  # KAFKA-17725
    "documents.jsonl": "87b499c6450c69147fb8092acf5e60899165d40e",  # KIP-752
    "persons.jsonl": "771e94c26bda302bacc1a0fdf1459946d258b7f8",  # 5 identities
    "changes.jsonl": "44d5964ea810c1c2f43b98f4ae49305cfbd8adc7",  # a commit and its PR
    "containers.jsonl": "d00cb9d4e8a8c735b0af59e360a8d5e65750eaae",  # component, space, version
}


@pytest.fixture
def golden_raw(tmp_path):
    """The three committed raw samples, planted the way `brain harvest` writes them."""
    raw = tmp_path / "raw"
    plant_pages(raw, "jira", [load_fixture("jira", "raw_issue.json")])
    plant_pages(raw, "confluence", [load_fixture("confluence", "raw_page.json")])
    plant_commits(raw, [load_fixture("git", "raw_commit.json")])
    return tmp_path


def test_canon_still_produces_the_golden_bytes(golden_raw):
    """`brain canon` end to end on real raw responses, digested.

    This is the check the mini-fixture digest was mistaken for: it runs the pipeline, so
    a changed mapper or a changed `sources.yaml` policy fails here rather than silently
    producing a different corpus on the next harvest.
    """
    _report, code = run_canon(
        ["jira", "confluence", "git"],
        raw_dir=golden_raw / "raw",
        canonical_dir=golden_raw / "canonical",
        reports_dir=golden_raw / "reports",
        echo=lambda _m: None,
    )
    assert code == 0
    got = {
        name: entry["sha1"] for name, entry in canonical_digests(golden_raw / "canonical").items()
    }
    assert set(got) == set(GOLDEN_SHA1)
    assert got == GOLDEN_SHA1


def test_the_golden_run_writes_no_source_id(golden_raw):
    """A single-instance registry says nothing new with it, so the field is absent.

    Pinned separately from the digests because it is the reason they did not move when
    `source_id` was added: the byte-identity of the real corpus rests on this.
    """
    run_canon(
        ["jira", "confluence", "git"],
        raw_dir=golden_raw / "raw",
        canonical_dir=golden_raw / "canonical",
        reports_dir=golden_raw / "reports",
        echo=lambda _m: None,
    )
    for path in sorted((golden_raw / "canonical").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert "source_id" not in json.loads(line), path.name


# ------------------------------------------------------- the three blockers, as sections


def test_the_redaction_section_harvests_a_leaky_connector_and_finds_nothing(tmp_path):
    """The evidence in the report is a measurement, not a claim that a test passed."""
    section = redaction_check()
    assert section["ran"] is True
    assert section["token_in_report_bytes"] is False
    assert section["token_in_report_object"] is False
    assert section["token_in_echoed_lines"] is False
    assert section["redaction_marker_in_report"] is True
    assert section["exit_code"] == 1  # a fatal error is still a failure
    # the `basic` scheme encodes the credential, so redacting the token alone is not enough
    assert section["basic_scheme_secrets"] == 2
    assert section["basic_encoded_is_redacted"] is True


def test_the_redaction_section_never_writes_into_the_real_reports():
    before = Path("data/reports/harvest.json")
    stamp = before.stat().st_mtime if before.exists() else None
    redaction_check()
    assert (before.stat().st_mtime if before.exists() else None) == stamp


def test_the_same_type_section_proves_both_halves_of_the_decision():
    section = same_type_check()
    assert section["duplicate_id_rejected"] is True
    assert "duplicate source id 'jira-eu'" in section["duplicate_id_message"]
    assert section["two_of_one_type_accepted"] is True
    assert section["two_of_one_type_ids"] == ["jira-eu", "jira-us"]
    assert section["same_type_ids"] == {"jira": ["jira-eu", "jira-us"]}
    assert section["raw_dirs_distinct"] is True
    assert section["live_registry_same_type_ids"] == {}
