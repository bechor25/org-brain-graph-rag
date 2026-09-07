"""The runner's contract: it writes two artefacts, never data, and reports every threshold.

The census document is generated from the report dict (decision 4), so the test that
matters most here is `test_every_number_in_the_document_comes_from_the_report`: it renders
a report full of made-up numbers and asserts they are the ones in the Markdown. If anyone
ever hardcodes a count in `render.py`, that test is what catches it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from brain.graph.context import GraphContext
from brain.index import render as render_mod
from brain.index import runner as runner_mod


class ReadOnlyClient:
    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.reads = reads or {}

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        raise AssertionError(f"the census wrote: {cypher}")


def ctx(**kw) -> GraphContext:
    return GraphContext(ReadOnlyClient(**kw))


# ------------------------------------------------------------------------ sanity thresholds


def test_the_empty_kip_threshold_is_measured_even_when_it_is_zero():
    """A threshold that only appears when it trips cannot be told from an unrun check."""
    c = ctx(reads={"count(d) AS chunked": [{"chunked": 267, "empty": 0}]})
    out = runner_mod.kip_entity_coverage(c, {"Document", "Chunk", "Entity"})
    assert out == {
        "measured": True,
        "chunked_kips": 267,
        "without_an_entity": 0,
        "pct_without_an_entity": 0.0,
        "rule": out["rule"],
    }


def test_a_kip_with_no_entity_becomes_a_warning_not_a_filter():
    report = {
        "sanity": {
            "kip_entity_coverage": runner_mod.kip_entity_coverage(
                ctx(reads={"count(d) AS chunked": [{"chunked": 100, "empty": 4}]}),
                {"Document", "Chunk", "Entity"},
            )
        }
    }
    warns = runner_mod.sanity_warnings(ctx(), report, set())
    named = {w["name"]: w for w in warns}
    assert "4 of 100" in named["kip_with_zero_entities"]["detail"]
    assert named["kip_with_zero_entities"]["severity"] == "warn"


def test_the_threshold_is_skipped_with_a_reason_when_a_label_is_absent():
    out = runner_mod.kip_entity_coverage(ctx(), {"Document"})
    assert out["measured"] is False
    assert "Entity" in out["reason"]


def test_an_index_that_is_not_online_is_an_error_level_warning():
    report = {"indexes": {"not_online": {"chunk_text": "POPULATING"}}}
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert warns["indexes_not_online"]["severity"] == "error"


def test_missing_provenance_is_an_error_level_warning():
    report = {"provenance": {"edges": {"missing": 3, "by_type": {"DECIDES": 3}}}}
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert warns["llm_edges_without_provenance"]["severity"] == "error"


def test_an_unflagged_label_is_named_because_reset_decides_by_that_flag():
    report = {"synthetic": {"by_label": {"File": {"missing_flag": 8594}, "Commit": {"real": 1}}}}
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert "'File': 8594" in warns["synthetic_flag_missing"]["detail"]


def test_index_meta_that_disagrees_with_the_live_count_is_a_warning():
    """`brain index` writes that number, so a gap means the write did not reach the row."""
    report = {
        "index_meta": {
            "rows": [],
            "missing_meta": [],
            "disagreements": [
                {"index": "chunk_embedding", "stored_live": 13846, "live_vectors": 12915}
            ],
        }
    }
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    row = warns["index_meta_count_differs_from_live"]
    assert row["severity"] == "warn"
    assert "13846" in row["detail"] and "12915" in row["detail"]
    assert "decision 5" in row["detail"]


def test_index_meta_rows_that_agree_raise_nothing():
    report = {"index_meta": {"rows": [], "missing_meta": [], "disagreements": []}}
    warns = {w["name"] for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert "index_meta_count_differs_from_live" not in warns


def test_absent_communities_are_a_warning_with_the_census_note():
    report = {"communities": {"present": False, "note": "brain communities has not run"}}
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert "has not run" in warns["communities_absent"]["detail"]


def test_unsummarised_communities_are_coverage_not_a_defect():
    """`brain communities` summarises a selected set and leaves the rest as `misc`.
    Counting those as 'missing a report' would invent a defect out of a design decision."""
    report = {
        "communities": {
            "present": True,
            "communities": 1297,
            "summarised": 186,
            "pct_summarised": 14.34,
            "distinct_members": 11875,
            "members_in_a_summarised_community": 10618,
            "pct_members_in_a_summarised_community": 89.41,
        }
    }
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert "communities_without_a_report" not in warns
    row = warns["community_report_coverage"]
    assert row["severity"] == "info"
    assert "89.41% of members" in row["detail"]
    assert "misc" in row["detail"]


def test_a_graph_where_no_community_was_summarised_is_a_real_warning():
    report = {"communities": {"present": True, "communities": 1297, "summarised": 0}}
    warns = {w["name"]: w for w in runner_mod.sanity_warnings(ctx(), report, set())}
    assert warns["no_community_has_a_report"]["severity"] == "warn"


# --------------------------------------------------------------------- the census document


FAKE_REPORT: dict[str, Any] = {
    "generated_at": "2026-09-07T00:00:00+00:00",
    "report_path": "data/reports/index.json",
    "gate": {
        "source": "plan1 Task 9",
        "ok": False,
        "passed": 1,
        "total": 2,
        "failed_names": ["b"],
        "criteria": [
            {
                "name": "a",
                "requirement": ">= 1",
                "value": 4242,
                "status": "PASS",
                "ok": True,
                "note": "",
            },
            {
                "name": "b",
                "requirement": ">= 0.85",
                "value": 0.7439,
                "status": "FAIL",
                "ok": False,
                "note": "accepted but failing",
            },
        ],
    },
    "corpus": {"real_issues": 1416, "commits": 6107, "kips_embedded": 267},
    "nodes": {
        "total": 58634,
        "structural": {"WorkItem": {"present": True, "count": 3265}},
        "derived": {"Community": {"present": False, "count": 0}},
        "workitem_sublabels": {"Bug": 774},
        "other_labels": {},
        "note": "n",
    },
    "synthetic": {
        "by_label": {
            "WorkItem": {"present": True, "real": 1416, "synthetic": 1849, "missing_flag": 0}
        }
    },
    "edges": {
        "total": 162098,
        "llm_derived": {"total": 14875, "by_type": {"MENTIONS": 9390}},
        "deterministic": {"total": 147223, "by_type": {"TOUCHES": 48032}},
    },
    "provenance": {
        "rule": "rule 3",
        "nodes": {
            "by_label": {
                "Entity": {
                    "present": True,
                    "total": 9038,
                    "llm_derived": 9038,
                    "with_provenance": 9038,
                    "missing": 0,
                    "pct": 100.0,
                    "llm_derived_rule": "all",
                }
            }
        },
        "edges": {
            "by_type": {
                "MENTIONS": {"total": 9390, "with_provenance": 9390, "missing": 0, "pct": 100.0}
            }
        },
        "same_as": {"total": 0},
    },
    "resolution": {
        "available": True,
        "source": "data/reports/resolve.json",
        "generated_at": "x",
        "target": 0.85,
        "kinds": {
            "person": {
                "nodes_before": 2187,
                "nodes_after": 1229,
                "precision": 0.9792,
                "recall": 0.7439,
                "f1": 0.8455,
                "merges_by_tier": {"1": 268},
            }
        },
    },
    "communities": {"present": False, "note": "not run"},
    "chunks": {
        "present": True,
        "chunks": 13846,
        "live": 12915,
        "orphaned": 931,
        "embedded": 13846,
        "pct_embedded": 100.0,
        "live_embedded": 12915,
        "pct_live_embedded": 100.0,
        "by_kind": {"message": 4150},
        "by_parent_kind": {"WorkItem": 7052},
    },
    "indexes": {
        "managed": 11,
        "online": 11,
        "applied": {"created": 0, "existing": 11},
        "status": [
            {
                "name": "chunk_text",
                "type": "FULLTEXT",
                "state": "ONLINE",
                "population_percent": 100.0,
                "owner": "index",
                "managed": True,
                "labels": ["Chunk"],
                "properties": ["text"],
            }
        ],
    },
    "index_meta": {
        "note": "m",
        "rows": [
            {
                "index": "chunk_embedding",
                "label": "Chunk",
                "model": "bge-m3",
                "dim": 1024,
                "similarity": "cosine",
                "live_vectors": 12915,
                "total_vectors": 13846,
                "orphaned_vectors": 931,
                "stored_live": 12915,
                "stored_total": 13846,
                "updated_at": "y",
            }
        ],
    },
    "schema_deviations": {
        "rule": "spec §2.4 declares a closed node-label set",
        "total": 1,
        "nodes_outside_the_closed_set": 24,
        "rows": [{"label": "Area", "count": 24, "kind": "node label", "note": "ADO area paths"}],
    },
    "orphans": {
        "total": 180,
        "of_nodes": 58633,
        "pct": 0.31,
        "note": "n",
        "by_label": {"Sprint": {"orphans": 92, "of": 92, "pct": 100.0}},
    },
    "dangling_refs": {
        "available": True,
        "source": "data/reports/load.json",
        "note": "n",
        "by_kind": {"issue": 8217},
        "refs_by_kind": {"issue": 12229},
        "links_declared": 2988,
        "links_dangling": 276,
    },
    "canonical": {
        "dir": "data/canonical",
        "files": {"workitems.jsonl": {"sha256": "abc123", "bytes": 19209121, "records": 3265}},
    },
    "versions": {
        "neo4j": {"version": "2026.06.0", "edition": "community"},
        "gds": {"version": "2026.06.0"},
        "apoc": {"version": "2026.06.0"},
        "ollama": {
            "version": "0.32.6",
            "url": "u",
            "model": "bge-m3",
            "model_detail": {"name": "bge-m3:latest", "digest": "790764642607"},
        },
    },
    "sanity": {
        "kip_entity_coverage": {
            "measured": True,
            "chunked_kips": 267,
            "without_an_entity": 0,
            "pct_without_an_entity": 0.0,
            "rule": "r",
        }
    },
    "warnings": [{"severity": "warn", "name": "w1", "detail": "d1"}],
}


def test_every_number_in_the_document_comes_from_the_report():
    md = render_mod.render(FAKE_REPORT)
    for needle in (
        "4,242",  # a gate value
        "0.7439",  # the failing criterion
        "58,634",  # node total
        "162,098",  # edge total
        "13,846",  # chunks
        "8,217",  # dangling issue refs
        "12,915",  # the live vector count decision 5 asks for
        "931",  # the orphaned vectors, still visible beside it
        "2026.06.0",  # neo4j
        "790764642607",  # the embedding model digest
        "abc123",  # the canonical fingerprint
    ):
        assert needle in md, needle


def test_the_document_names_the_sections_that_are_absent_instead_of_dropping_them():
    md = render_mod.render(FAKE_REPORT)
    assert "## קהילות" in md
    assert "not run" in md


def test_the_document_says_up_front_that_no_number_was_typed_by_hand():
    md = render_mod.render(FAKE_REPORT)
    assert "אין כאן מספר שהוקלד ביד" in md
    assert md.startswith("# מפקד הגרף")


def test_a_whole_float_keeps_a_decimal_so_a_precision_column_stays_one_kind_of_number():
    assert render_mod._fmt(1.0) == "1.0"
    assert render_mod._fmt(0.7439) == "0.7439"
    assert render_mod._fmt(9038) == "9,038"
    assert render_mod._fmt(None) == "—"


def test_the_document_renders_from_an_almost_empty_report():
    """A run against a graph where nothing has landed still has to produce a document."""
    md = render_mod.render({"generated_at": "t", "gate": {"criteria": []}})
    assert "מפקד הגרף" in md
    assert "*(אין נתונים)*" in md


# ------------------------------------------------------------------------------- the smoke


def test_the_smoke_runner_refuses_to_recurse(monkeypatch, tmp_path):
    monkeypatch.setenv(runner_mod.SMOKE_GUARD, "1")
    out = runner_mod.run_smoke(tmp_path, echo=lambda _m: None)
    assert out["ok"] is False
    assert out["status"] == "SKIPPED"


def test_the_smoke_runner_records_the_exit_code(monkeypatch, tmp_path):
    class Done:
        returncode = 0
        stdout = "7 passed"
        stderr = ""

    monkeypatch.delenv(runner_mod.SMOKE_GUARD, raising=False)
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Done())
    out = runner_mod.run_smoke(tmp_path, echo=lambda _m: None)
    assert out["ok"] is True
    assert out["status"] == "PASS"
    assert out["tail"] == "7 passed"


def test_a_failed_smoke_is_recorded_as_a_failure(monkeypatch, tmp_path):
    class Failed:
        returncode = 1
        stdout = "1 failed"
        stderr = ""

    monkeypatch.delenv(runner_mod.SMOKE_GUARD, raising=False)
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Failed())
    out = runner_mod.run_smoke(tmp_path, echo=lambda _m: None)
    assert out["ok"] is False
    assert out["exit_code"] == 1


def test_a_failed_smoke_names_every_failing_test_not_just_the_last_few(monkeypatch, tmp_path):
    """A 15-line tail on a 154-test live suite showed 13 of 22 failures and hid the rest,
    which makes a recorded FAIL unactionable."""

    class Failed:
        returncode = 1
        stdout = "\n".join(
            [f"FAILED tests/live/test_resolve_live.py::test_{i}" for i in range(20)]
            + ["ERROR tests/live/test_chunk_live.py::test_setup"]
            + [f"noise line {i}" for i in range(60)]
            + ["19 failed, 135 passed"]
        )
        stderr = ""

    monkeypatch.delenv(runner_mod.SMOKE_GUARD, raising=False)
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Failed())
    out = runner_mod.run_smoke(tmp_path, echo=lambda _m: None)
    assert len(out["failures"]) == 21
    assert out["failing_files"] == [
        "tests/live/test_chunk_live.py",
        "tests/live/test_resolve_live.py",
    ]
    assert len(out["tail"].splitlines()) == runner_mod.SMOKE_TAIL_LINES


# -------------------------------------------------------------------------------- report io


def test_a_previous_runs_keys_survive_a_rerun(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"smoke": {"ok": True}, "nodes": {"total": 1}}), encoding="utf-8")
    merged = runner_mod._merge_report(path, {"nodes": {"total": 2}})
    assert merged["smoke"] == {"ok": True}
    assert merged["nodes"]["total"] == 2


def test_a_corrupt_previous_report_is_replaced_not_raised(tmp_path):
    path = tmp_path / "index.json"
    path.write_text("{oops", encoding="utf-8")
    assert runner_mod._merge_report(path, {"step": "index"}) == {"step": "index"}


def test_the_context_never_writes_during_a_census():
    with pytest.raises(AssertionError):
        ctx().write("CREATE (n)")


def test_the_document_names_a_schema_deviation_as_a_deviation():
    md = render_mod.render(FAKE_REPORT)
    assert "## חריגות סכמה" in md
    assert "Area" in md.split("## חריגות סכמה")[1].split("##")[0]


def test_the_head_sha_helper_returns_none_outside_a_repo(tmp_path):
    assert runner_mod.head_sha(tmp_path / "nowhere") is None


def test_the_head_sha_helper_reads_the_commit(monkeypatch, tmp_path):
    class Done:
        returncode = 0
        stdout = "abc123def456\n"
        stderr = ""

    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Done())
    assert runner_mod.head_sha(tmp_path) == "abc123def456"


def test_a_smoke_result_records_the_commit_it_was_measured_on(monkeypatch, tmp_path):
    """Without this the gate cannot tell a fresh result from a republished one."""

    class Done:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    monkeypatch.delenv(runner_mod.SMOKE_GUARD, raising=False)
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(runner_mod, "head_sha", lambda _repo: "deadbeef")
    assert runner_mod.run_smoke(tmp_path, echo=lambda _m: None)["head_sha"] == "deadbeef"
