"""Provenance for LLM-authored records (conventions rule 3), without a database.

The ledger fixture is not hand-written: `brain synth merge` runs for real and the file it
leaves behind is the one these tests read. A hand-rolled shape would have kept passing
while the real ledger moved underneath it — which is exactly what happened once.
"""

from __future__ import annotations

import json

import pytest

from brain.graph.loaders.containers import node_rows as container_rows
from brain.graph.loaders.workitems import node_rows as workitem_rows
from brain.graph.provenance import (
    LEDGER_FILENAME,
    NOT_AVAILABLE,
    SYNTHETIC_MODEL,
    ProvenanceError,
    SyntheticProvenance,
)
from tests.graph_helpers import container, work_item
from tests.synth_helpers import batch_output, write_output
from tests.test_synth_merge import merge


@pytest.fixture
def real_ledger(tmp_path):
    """The ledger `brain synth merge` actually writes, for one merged batch."""
    write_output(tmp_path / "batches", batch_output())
    _report, code = merge(tmp_path)
    assert code == 0
    path = tmp_path / "canonical" / LEDGER_FILENAME
    assert path.is_file()
    return tmp_path / "canonical"


def test_absent_ledger_stamps_nothing_and_says_so(tmp_path):
    prov = SyntheticProvenance.load(tmp_path)
    assert prov.available is False
    assert prov.props("xray:XT-10001") == {}
    assert prov.status() == NOT_AVAILABLE
    assert prov.report(stamped=0)["nodes_stamped"] == 0


def test_the_ledger_synth_merge_writes_is_keyed_by_canonical_id(real_ledger):
    raw = json.loads((real_ledger / LEDGER_FILENAME).read_text(encoding="utf-8"))
    assert set(raw) >= {"step", "updated_at", "provenance", "batches", "failed"}

    prov = SyntheticProvenance.load(real_ledger)
    assert prov.available is True
    assert prov.props("xray:XT-10001") == {
        "batch_id": "shard-01/001",
        "model": SYNTHETIC_MODEL,
        "extracted_at": raw["updated_at"],
        "shard": "shard-01",
    }
    # the id, not the key: `XT-10001` alone is not what the ledger is keyed by
    assert prov.props("XT-10001") == {}
    assert prov.props("jira:KAFKA-100") == {}


def test_persons_and_containers_are_stamped_through_the_same_id_space(real_ledger):
    prov = SyntheticProvenance.load(real_ledger)
    assert prov.props("ado:rao.jun")["batch_id"] == "shard-01/001"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("[]", "expected a JSON object"),
        ("{not json", "not valid JSON"),
        ('{"step": "synth.merge"}', "no `provenance` block"),
        ('{"provenance": []}', "must be an object keyed by canonical id"),
        ('{"provenance": {"xray:XT-1": "shard-01/001"}}', "must be an object"),
    ],
)
def test_a_ledger_that_is_not_the_real_shape_fails_loudly(tmp_path, payload, expected):
    (tmp_path / LEDGER_FILENAME).write_text(payload, encoding="utf-8")
    with pytest.raises(ProvenanceError) as exc:
        SyntheticProvenance.load(tmp_path)
    assert expected in str(exc.value)


def test_only_ledger_ids_get_provenance_on_their_node(real_ledger):
    prov = SyntheticProvenance.load(real_ledger)
    grouped, _types, _unknown = workitem_rows(
        [
            work_item(key="KAFKA-100", type="Bug"),
            work_item(key="XT-10001", source="xray", type="Test", synthetic=True),
        ],
        prov,
    )
    props = {r["key"]: r["props"] for rows in grouped.values() for r in rows}
    assert props["XT-10001"]["batch_id"] == "shard-01/001"
    assert props["XT-10001"]["model"] == SYNTHETIC_MODEL
    assert props["XT-10001"]["synthetic"] is True
    assert "batch_id" not in props["KAFKA-100"]
    assert props["KAFKA-100"]["synthetic"] is False


def test_a_container_is_stamped_by_its_id_not_its_name(tmp_path):
    (tmp_path / LEDGER_FILENAME).write_text(
        json.dumps(
            {
                "step": "synth.merge",
                "updated_at": "2026-09-06T10:00:00Z",
                "provenance": {
                    "ado:sprint:Sprint 2024-03": {
                        "batch_id": "shard-02/004",
                        "shard": "shard-02",
                        "merged_at": "2026-09-06T10:00:00Z",
                    }
                },
                "batches": {},
                "failed": {},
            }
        ),
        encoding="utf-8",
    )
    prov = SyntheticProvenance.load(tmp_path)
    sprint = container("sprint", "Sprint 2024-03", synthetic=True)
    sprint.id = "ado:sprint:Sprint 2024-03"
    sprint.source = "ado"
    grouped, skipped, _unknown = container_rows([sprint], prov)
    (row,) = grouped["Sprint"]
    assert row["key"] == "Sprint 2024-03"
    assert row["props"]["batch_id"] == "shard-02/004"
    assert skipped == {}
