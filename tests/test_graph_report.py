"""What `data/reports/load.json` claims, checked without a database."""

from __future__ import annotations

from brain.canon.models import Link
from brain.graph.report import build_checks, formal_links_by_origin
from tests.graph_helpers import corpus, work_item

CANON = {"report": "data/reports/canon.json", "formal_links": 884}


def a_corpus():
    """Two real work items declaring one link each, one synthetic declaring three."""
    return corpus(
        workitems=[
            work_item(key="KAFKA-100", links=[Link(type="blocker", target="KAFKA-101")]),
            work_item(
                key="KAFKA-101", links=[Link(type="blocker", target="KAFKA-100", direction="in")]
            ),
            work_item(
                key="XT-1",
                source="xray",
                type="Test",
                synthetic=True,
                links=[
                    Link(type="tests", target="KAFKA-100"),
                    Link(type="defect", target="KAFKA-101"),
                    Link(type="related", target="KAFKA-100"),
                ],
            ),
        ]
    )


def formal_links_check(declared: int):
    stats = {"workitem_edges": {"links": {"declared": declared}}, "refs": {}}
    checks = build_checks(
        a_corpus(),
        nodes={},
        edges={},
        via={},
        stats=stats,
        counters={"nodes_created": 0, "relationships_created": 0},
        canon=CANON,
    )
    return next(c for c in checks if c["name"].startswith("formal_links"))


def test_formal_links_are_counted_by_who_declared_them():
    assert formal_links_by_origin(a_corpus()) == {"real": 2, "synthetic": 3, "total": 5}


def test_the_expectation_is_canons_real_count_plus_the_synthetic_records():
    """canon.json is written before `brain synth merge` runs, so it can only ever know
    the real half; comparing every declared link against it fails the day the layer lands.
    """
    check = formal_links_check(declared=884 + 3)
    assert check["expected"] == 887
    assert check["ok"] is True
    assert "884" in check["note"] and "3 links declared by" in check["note"]


def test_a_real_link_that_went_missing_still_fails_the_check():
    check = formal_links_check(declared=880 + 3)
    assert check["ok"] is False
    assert (check["expected"], check["actual"]) == (887, 883)
