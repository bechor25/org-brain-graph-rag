"""`brain load` against the real Neo4j, on the mini fixture corpus.

The load runs in its own label namespace (`_Smoke…`), so `make smoke` never reads or
deletes the full graph a previous `brain load` built. Relationship types are shared, but
every query here reaches them through a namespaced label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.config import Settings
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_Smoke"


@pytest.fixture(scope="module")
def ctx():
    s = Settings()
    client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    client.verify()
    c = GraphContext(client, prefix=PREFIX)
    wipe(c)
    drop_schema(c)
    try:
        yield c
    finally:
        wipe(c)
        drop_schema(c)
        client.close()


@pytest.fixture(scope="module")
def loaded(ctx, tmp_path_factory):
    report, code = run_load(
        client=ctx.client,
        canonical_dir=MINI,
        reports_dir=tmp_path_factory.mktemp("reports"),
        prefix=PREFIX,
        write_report=False,
        echo=lambda _msg: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    return report


def q(ctx: GraphContext, cypher: str, **params):
    return ctx.read(cypher, **params)


def test_node_counts_match_the_fixture(loaded):
    nodes = loaded["census"]["nodes_by_label"]
    assert nodes["WorkItem"] == 6
    assert nodes["Document"] == 1
    assert nodes["Person"] == 3
    assert nodes["Commit"] == 3
    assert nodes["PullRequest"] == 1
    assert nodes["Component"] == 3
    assert nodes["Version"] == 1
    assert nodes["File"] == 4
    assert nodes["StatusChange"] == 11


def test_secondary_labels_separate_jira_and_xray_test_types(loaded, ctx):
    nodes = loaded["census"]["nodes_by_label"]
    assert nodes["Bug"] == 1 and nodes["Improvement"] == 1 and nodes["Task"] == 1
    assert nodes["Test"] == 2 and nodes["TestExecution"] == 1
    assert "JiraTest" not in nodes  # the fixture has no Jira `Test` issue
    rows = q(
        ctx,
        f"MATCH (t:{ctx.label('Test')}) RETURN t.key AS key, t.source AS source ORDER BY key",
    )
    assert rows == [
        {"key": "XT-1", "source": "xray"},
        {"key": "XT-2", "source": "xray"},
    ]


def test_edge_counts(loaded):
    edges = loaded["census"]["edges_by_type"]
    assert edges["LINKS_TO"] == 1  # KAFKA-100 blocks KAFKA-101, declared by both issues
    assert loaded["census"]["links_to_by_type"] == {"blocks": 1}
    assert edges["TESTS"] == 1
    assert edges["EXECUTED_IN"] == 2
    assert edges["RESOLVES"] == 2
    assert edges["IMPLEMENTS_KIP"] == 1
    assert edges["HAS_COMMIT"] == 1
    assert edges["HAS_CHANGE"] == 11
    assert edges["REPORTED_BY"] == 3
    assert edges["ASSIGNED_TO"] == 3
    assert edges["AUTHORED"] == 5
    assert edges["COMMENTED"] == 2
    assert edges["TOUCHES"] == 4
    assert edges["IN_COMPONENT"] == 6
    assert edges["FIX_VERSION"] == 3
    assert edges["AFFECTS_VERSION"] == 1


def test_a_test_and_a_commit_meet_on_the_work_item(ctx):
    """The traversal the whole structured layer exists for (Task 4 acceptance)."""
    rows = q(
        ctx,
        f"MATCH (t:{ctx.label('Test')})-[:TESTS]->(w:{ctx.label('WorkItem')})"
        f"<-[:RESOLVES]-(c:{ctx.label('Commit')}) "
        "RETURN t.key AS test, w.key AS work_item, c.sha AS commit, w.resolution AS resolution",
    )
    assert rows == [
        {
            "test": "XT-1",
            "work_item": "KAFKA-100",
            "commit": "a1b2c3d4",
            "resolution": "Fixed",
        }
    ]


def test_kafka_101_status_history_is_three_events_in_order(ctx):
    rows = q(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')} {{key: 'KAFKA-101'}})"
        f"-[:HAS_CHANGE]->(s:{ctx.label('StatusChange')} {{field: 'status'}}) "
        "RETURN s.`from` AS from_status, s.`to` AS to_status, toString(s.at) AS at "
        "ORDER BY s.at",
    )
    assert [(r["from_status"], r["to_status"]) for r in rows] == [
        ("Open", "In Progress"),
        ("In Progress", "Patch Available"),
        ("Patch Available", "Open"),
    ]
    assert rows[0]["at"].startswith("2024-03-10T08:00:00")


def test_remote_issue_link_noise_never_becomes_an_event(loaded, ctx):
    stats = loaded["loaders"]["workitem_edges"]["status_changes"]
    assert stats["ignored_fields"] == {"Link": 1, "RemoteIssueLink": 1}
    assert stats["id_collisions"] == 0
    rows = q(
        ctx,
        f"MATCH (s:{ctx.label('StatusChange')}) "
        "RETURN s.field AS field, count(s) AS c ORDER BY field",
    )
    assert {r["field"] for r in rows} == {"status", "assignee", "resolution", "Fix Version"}


def test_assigned_to_intervals_come_from_identity_keys(ctx):
    rows = q(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')})-[r:ASSIGNED_TO]->(p:{ctx.label('Person')}) "
        "RETURN w.key AS key, p.id AS person, toString(r.valid_from) AS valid_from, "
        "r.valid_to AS valid_to ORDER BY key",
    )
    by_key = {r["key"]: r for r in rows}
    # KAFKA-100: set by the changelog transition, still open
    assert by_key["KAFKA-100"]["person"] == "jira:dlee"
    assert by_key["KAFKA-100"]["valid_from"].startswith("2024-01-12T08:00:00")
    assert by_key["KAFKA-100"]["valid_to"] is None
    # KAFKA-101: the changelog only knows `JIRAUSER999`, so the open interval comes from
    # the issue's own assignee field, starting at the last assignee change.
    assert by_key["KAFKA-101"]["person"] == "jira:dlee"
    assert by_key["KAFKA-101"]["valid_from"].startswith("2024-03-06T08:00:00")
    # KAFKA-102: no assignee changelog at all, so the interval starts at `created`
    assert by_key["KAFKA-102"]["person"] == "jira:mrivera"
    assert by_key["KAFKA-102"]["valid_from"].startswith("2024-02-20T09:00:00")


def test_references_keep_via_and_do_not_invent_targets(loaded, ctx):
    assert loaded["dangling_refs"] == {"pr": 1}  # `#99999` in a KAFKA-101 comment
    rows = q(
        ctx,
        f"MATCH (c:{ctx.label('Commit')} {{sha: 'a1b2c3d4'}})-[r:REFERENCES]->(t) "
        "RETURN r.kinds[0] AS kind, r.via AS via, "
        "coalesce(t.key, toString(t.number)) AS target ORDER BY kind",
    )
    assert rows == [
        {"kind": "issue", "via": "text", "target": "KAFKA-100"},
        {"kind": "kip", "via": "text", "target": "KIP-5"},
        {"kind": "pr", "via": "text", "target": "14001"},
    ]
    assert q(
        ctx, f"MATCH (w:{ctx.label('WorkItem')} {{key: 'KAFKA-999'}}) RETURN count(w) AS c"
    ) == [{"c": 0}]


def test_pull_request_owns_its_commit_from_change_pr(ctx):
    rows = q(
        ctx,
        f"MATCH (p:{ctx.label('PullRequest')})-[:HAS_COMMIT]->(c:{ctx.label('Commit')}) "
        "RETURN p.number AS number, c.sha AS sha",
    )
    assert rows == [{"number": 14001, "sha": "a1b2c3d4"}]


def test_synthetic_records_are_flagged_and_carry_no_provenance_without_a_ledger(loaded, ctx):
    assert loaded["synthetic_provenance"]["status"] == "not_available"
    rows = q(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')}) WHERE w.synthetic RETURN w.key AS key ORDER BY key",
    )
    assert [r["key"] for r in rows] == ["XE-1", "XT-1", "XT-2"]


def test_second_run_creates_nothing(ctx, loaded, tmp_path_factory):
    report, code = run_load(
        client=ctx.client,
        canonical_dir=MINI,
        reports_dir=tmp_path_factory.mktemp("reports2"),
        prefix=PREFIX,
        write_report=False,
        echo=lambda _msg: None,
    )
    counters = report["last_run"]["counters"]
    assert counters["nodes_created"] == 0
    assert counters["relationships_created"] == 0
    assert code == 0
    assert report["census"]["nodes_by_label"] == loaded["census"]["nodes_by_label"]
    assert report["census"]["edges_by_type"] == loaded["census"]["edges_by_type"]
