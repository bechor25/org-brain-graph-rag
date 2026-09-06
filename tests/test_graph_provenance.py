"""Provenance for LLM-authored records (conventions rule 3), without a database."""

from __future__ import annotations

import json

from brain.graph.loaders.workitems import node_rows
from brain.graph.provenance import NOT_AVAILABLE, SYNTHETIC_MODEL, SyntheticProvenance
from tests.graph_helpers import work_item


def test_absent_ledger_stamps_nothing_and_says_so(tmp_path):
    prov = SyntheticProvenance.load(tmp_path)
    assert prov.available is False
    assert prov.props("XT-1") == {}
    assert prov.status() == NOT_AVAILABLE
    assert prov.report(stamped=0)["nodes_stamped"] == 0


def test_ledger_stamps_batch_model_and_extracted_at(tmp_path):
    (tmp_path / "synthetic_merged.json").write_text(
        json.dumps(
            {
                "XT-1": {
                    "batch_id": "synthetic/a/001",
                    "shard": "a",
                    "merged_at": "2026-09-06T10:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    prov = SyntheticProvenance.load(tmp_path)
    assert prov.available is True
    assert prov.props("XT-1") == {
        "batch_id": "synthetic/a/001",
        "model": SYNTHETIC_MODEL,
        "extracted_at": "2026-09-06T10:00:00Z",
        "shard": "a",
    }
    assert prov.props("KAFKA-100") == {}


def test_only_ledger_keys_get_provenance_on_their_node(tmp_path):
    (tmp_path / "synthetic_merged.json").write_text(
        json.dumps({"XT-1": {"batch_id": "b1", "merged_at": "2026-09-06T10:00:00Z"}}),
        encoding="utf-8",
    )
    prov = SyntheticProvenance.load(tmp_path)
    grouped, _types, _unknown = node_rows(
        [
            work_item(key="KAFKA-100", type="Bug"),
            work_item(key="XT-1", source="xray", type="Test", synthetic=True),
        ],
        prov,
    )
    props = {r["key"]: r["props"] for rows in grouped.values() for r in rows}
    assert props["XT-1"]["batch_id"] == "b1"
    assert props["XT-1"]["model"] == SYNTHETIC_MODEL
    assert props["XT-1"]["synthetic"] is True
    assert "batch_id" not in props["KAFKA-100"]
    assert props["KAFKA-100"]["synthetic"] is False
