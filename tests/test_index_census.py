"""The census without a database: namespacing, absence, and who owes provenance.

Three other steps write into the same graph while this one runs, so the behaviour worth
pinning here is not the arithmetic (the live test checks that against real data) but the
refusal to crash: a label that has not landed, a report file nobody has written, an Ollama
that is not running. Each of those has to come out as a value in the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from brain.graph.context import GraphContext
from brain.index import census as cen


class FakeClient:
    """Answers a read when the query contains a registered fragment; else returns []."""

    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.reads = reads or {}
        self.seen: list[str] = []

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.seen.append(cypher)
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        raise AssertionError("the census must never write")


def ctx(prefix: str = "", **kw) -> GraphContext:
    return GraphContext(FakeClient(**kw), prefix=prefix)


# ------------------------------------------------------------------------------ namespacing


def test_the_empty_prefix_excludes_the_label_spaces_the_live_tests_build():
    where = cen.node_filter(ctx(), "n")
    assert where == "none(l IN labels(n) WHERE l STARTS WITH '_')"


def test_a_prefixed_run_sees_only_its_own_labels():
    assert cen.node_filter(ctx(prefix="_Smoke"), "a") == (
        "any(l IN labels(a) WHERE l STARTS WITH '_Smoke')"
    )


def test_present_labels_drops_other_namespaces():
    names = ("WorkItem", "_CommChunk", "_ResetTestWorkItem", "Entity")
    rows = [{"label": name} for name in names]
    reads = {"db.labels": rows, "count(n) AS c": [{"c": 3}]}
    assert cen.present_labels(ctx(reads=reads)) == ["Entity", "WorkItem"]


def test_present_labels_of_a_prefixed_run_strips_the_prefix():
    rows = [{"label": name} for name in ("_TWorkItem", "WorkItem", "_TChunk")]
    reads = {"db.labels": rows, "count(n) AS c": [{"c": 1}]}
    assert cen.present_labels(ctx(prefix="_T", reads=reads)) == ["Chunk", "WorkItem"]


def test_a_label_declared_by_a_constraint_but_holding_no_node_is_not_present():
    """`brain load` declares Chunk/Entity/Community constraints before any step fills them,
    and declaring a constraint registers the label token. Counting decides presence."""

    class ByLabel(FakeClient):
        def read(self, cypher: str, **params: Any):
            if "db.labels" in cypher:
                return [{"label": "WorkItem"}, {"label": "Chunk"}]
            if "MATCH (n:`WorkItem`)" in cypher:
                return [{"c": 6}]
            return [{"c": 0}]

    assert cen.present_labels(GraphContext(ByLabel())) == ["WorkItem"]


# -------------------------------------------------------------------------------- absence


def test_a_label_the_graph_does_not_have_is_reported_absent_not_zero():
    out = cen.node_census(ctx(reads={"count(n) AS c": [{"c": 0}]}), present={"WorkItem"})
    assert out["structural"]["WorkItem"] == {"present": True, "count": 0}
    assert out["structural"]["Document"] == {"present": False, "count": 0}
    assert out["derived"]["Community"]["present"] is False


def test_no_community_nodes_is_a_section_with_a_reason_not_a_crash():
    out = cen.community_census(ctx(), present=set())
    assert out["present"] is False
    assert out["communities"] == 0
    assert "brain communities" in out["note"]


def test_a_community_label_with_zero_nodes_is_still_absent():
    out = cen.community_census(ctx(reads={"count(n) AS c": [{"c": 0}]}), present={"Community"})
    assert out["present"] is False


def test_no_chunk_label_is_a_section_with_a_reason():
    out = cen.chunk_census(ctx(), present=set())
    assert out["present"] is False
    assert "brain chunk" in out["note"]


def test_corpus_census_skips_the_labels_it_does_not_have():
    out = cen.corpus_census(ctx(), present=set())
    assert out == {}


# ----------------------------------------------------------------------------- provenance


def _prov_rows(**over):
    base = {"total": 10, "derived": 10, "complete": 10}
    base.update(over)
    for p in cen.PROVENANCE_PROPS:
        base.setdefault(f"missing_{p}", 0)
    return [base]


def test_an_unsummarised_community_owes_no_provenance():
    """The Leiden partition is deterministic GDS output. Only the report is LLM-written."""
    assert cen.LLM_NODE_LABELS["Community"] == "n.summary IS NOT NULL"
    c = ctx(reads={"MATCH (n:`Community`)": _prov_rows(total=1297, derived=0, complete=0)})
    out = cen.provenance_census(c, present={"Community"})
    community = out["nodes"]["by_label"]["Community"]
    assert community["total"] == 1297
    assert community["llm_derived"] == 0
    assert community["missing"] == 0
    assert out["nodes"]["missing"] == 0


def test_a_summarised_community_without_evidence_is_counted_missing():
    c = ctx(reads={"MATCH (n:`Community`)": _prov_rows(total=200, derived=180, complete=170)})
    out = cen.provenance_census(c, present={"Community"})
    assert out["nodes"]["by_label"]["Community"]["missing"] == 10
    assert out["nodes"]["missing"] == 10


def test_every_entity_owes_provenance():
    assert cen.LLM_NODE_LABELS["Entity"] == ""
    c = ctx(reads={"MATCH (n:`Entity`)": _prov_rows(total=9038, derived=9038, complete=9037)})
    out = cen.provenance_census(c, present={"Entity"})
    assert out["nodes"]["by_label"]["Entity"]["missing"] == 1


def test_the_provenance_expression_demands_a_non_empty_evidence_list():
    expr = cen._prov_expr("r")
    for prop in cen.PROVENANCE_PROPS:
        assert f"r.`{prop}` IS NOT NULL" in expr
    assert "size(r.`evidence_chunk_ids`) > 0" in expr


def test_deterministic_same_as_tiers_are_not_held_to_provenance():
    rows = [
        {"tier": 1, "rule": "kip_title_alias", "c": 7, "complete": 0},
        {"tier": 3, "rule": "adjudicated", "c": 4, "complete": 4},
    ]
    out = cen._same_as_census(ctx(reads={"SAME_AS": rows}))
    assert out["total"] == 11
    assert out["adjudicated"] == 4
    assert out["missing_on_adjudicated"] == 0


def test_an_adjudicated_same_as_without_provenance_is_a_gap():
    rows = [{"tier": 3, "rule": "adjudicated", "c": 4, "complete": 1}]
    out = cen._same_as_census(ctx(reads={"SAME_AS": rows}))
    assert out["missing_on_adjudicated"] == 3


def test_llm_edge_types_are_the_closed_extract_set():
    from brain.extract.graph import LLM_RELATION_TYPES

    assert set(cen.LLM_EDGE_TYPES) == set(LLM_RELATION_TYPES)


# ------------------------------------------------------------------------ report-fed halves


def test_resolution_reads_the_resolve_report(tmp_path):
    (tmp_path / "resolve.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-07T06:12:31+00:00",
                "eval": {
                    "target": 0.85,
                    "note": "gold only",
                    "person": {
                        "gold_pairs": 633,
                        "through_tier_3": {"precision": 0.9792, "recall": 0.7439, "f1": 0.8455},
                    },
                },
                "person": {
                    "baseline": {"nodes": 2187},
                    "before": {"duplicate_rate": 0.3091},
                    "after": {"nodes": 1229, "duplicate_rate": 0.438, "identities": 2187},
                    "merges_by_tier": {"1": 268},
                },
            }
        ),
        encoding="utf-8",
    )
    out = cen.resolution_census(tmp_path)
    person = out["kinds"]["person"]
    assert out["available"] is True
    assert person["recall"] == 0.7439
    assert person["nodes_before"] == 2187
    assert person["nodes_after"] == 1229


def test_a_missing_resolve_report_is_reported_not_raised(tmp_path):
    out = cen.resolution_census(tmp_path)
    assert out["available"] is False
    assert "resolve.json" in out["source"]


def test_a_corrupt_report_is_reported_not_raised(tmp_path):
    (tmp_path / "load.json").write_text("{not json", encoding="utf-8")
    assert cen.dangling_census(tmp_path)["available"] is False


def test_dangling_refs_come_from_the_load_report(tmp_path):
    """The shape is `brain load`'s own: `dangling_refs` at the top level, ref totals and
    the formal-link tally under `loaders`."""
    (tmp_path / "load.json").write_text(
        json.dumps(
            {
                "dangling_refs": {"issue": 8217, "kip": 3},
                "loaders": {
                    "refs": {"refs_by_kind": {"issue": 12229}},
                    "workitem_edges": {"links": {"declared": 2988, "dangling": 276}},
                },
            }
        ),
        encoding="utf-8",
    )
    out = cen.dangling_census(tmp_path)
    assert out["total"] == 8220
    assert out["by_kind"]["issue"] == 8217
    assert out["refs_by_kind"]["issue"] == 12229
    assert out["links_dangling"] == 276


def test_the_real_load_report_still_has_the_keys_this_census_reads(tmp_path):
    """A guard against the upstream report moving under us: if `brain load` renames these
    keys the census would silently report an empty dangling section, which reads as 'none'."""
    path = Path("data/reports/load.json")
    if not path.is_file():
        pytest.skip("brain load has not written a report here")
    out = cen.dangling_census(path.parent)
    assert out["available"] is True
    assert out["by_kind"], "load.json no longer exposes dangling_refs where the census looks"


# ---------------------------------------------------------------------------------- versions


def test_a_dead_ollama_is_a_value_in_the_report(monkeypatch):
    def boom(self, url, **kw):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx.Client, "get", boom)
    out = cen._ollama_versions("http://localhost:11434", "bge-m3")
    assert out["available"] is False
    assert "nope" in out["error"]


def test_a_procedure_the_server_does_not_have_is_a_value_in_the_report():
    class Exploding(FakeClient):
        def read(self, cypher: str, **params: Any):
            if "gds.version" in cypher:
                raise RuntimeError("Unknown function 'gds.version'")
            return super().read(cypher, **params)

    c = GraphContext(Exploding(reads={"apoc.version": [{"v": "2026.06.0"}]}))
    out = cen.versions(c, "http://127.0.0.1:1", "bge-m3")
    assert out["gds"]["available"] is False
    assert out["apoc"]["version"] == "2026.06.0"


# ------------------------------------------------------------------------------- synthetic


def test_a_label_with_no_synthetic_property_is_its_own_bucket():
    rows = [{"bucket": "missing_flag", "c": 13846}]
    out = cen.synthetic_census(ctx(reads={"CASE WHEN n.synthetic IS NULL": rows}), {"Chunk"})
    assert out["by_label"]["Chunk"]["missing_flag"] == 13846
    assert out["by_label"]["Chunk"]["real"] == 0
    assert out["by_label"]["Document"] == {"present": False}


def test_the_census_never_writes():
    with pytest.raises(AssertionError):
        ctx().write("CREATE (n)")
