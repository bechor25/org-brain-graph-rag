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
    assert nodes["WorkItem"] == 7
    assert nodes["Document"] == 1
    assert nodes["Person"] == 3
    assert nodes["Commit"] == 3
    assert nodes["PullRequest"] == 1
    assert nodes["Component"] == 3
    assert nodes["Version"] == 1
    assert nodes["Space"] == 1
    assert nodes["File"] == 4
    assert nodes["StatusChange"] == 11


def test_secondary_labels_separate_jira_and_xray_test_types(loaded, ctx):
    nodes = loaded["census"]["nodes_by_label"]
    assert nodes["Bug"] == 1 and nodes["Improvement"] == 1 and nodes["Task"] == 1
    assert nodes["Test"] == 2 and nodes["TestExecution"] == 1
    # XP-1 is a TestPlan *work item*; the fixture has no TestPlan *container*
    assert nodes["TestPlan"] == 1
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
    # XT-1 by the plan's own `tests` link, XT-2 by its `parent`
    assert edges["IN_PLAN"] == 2
    assert edges["HAS_RUN"] == 2
    assert edges["IN_SPACE"] == 1
    assert edges["PARENT_OF"] == 1
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
    assert edges["FIX_VERSION"] == 4  # KAFKA-100, KAFKA-102, XE-1, XP-1
    assert edges["AFFECTS_VERSION"] == 1


def test_a_run_is_read_from_the_executions_comment(ctx):
    """The synthetic layer states runs as `"<XT-n>: PASS|FAIL (reason)"` lines; reading
    that stated format is parsing, not extraction, so no LLM is involved."""
    rows = q(
        ctx,
        f"MATCH (e:{ctx.label('TestExecution')})-[r:HAS_RUN]->(t:{ctx.label('Test')}) "
        "RETURN e.key AS execution, t.key AS test, r.status AS status, r.reason AS reason "
        "ORDER BY test",
    )
    assert rows == [
        {"execution": "XE-1", "test": "XT-1", "status": "PASS", "reason": None},
        {"execution": "XE-1", "test": "XT-2", "status": "FAIL", "reason": "3 rebalances observed"},
    ]


def test_a_plan_collects_its_tests_from_a_link_and_from_the_hierarchy(ctx):
    rows = q(
        ctx,
        f"MATCH (t:{ctx.label('WorkItem')})-[:IN_PLAN]->(p:{ctx.label('WorkItem')}) "
        "RETURN t.key AS test, p.key AS plan ORDER BY test",
    )
    assert rows == [{"test": "XT-1", "plan": "XP-1"}, {"test": "XT-2", "plan": "XP-1"}]


def test_a_document_sits_in_its_space(ctx):
    rows = q(
        ctx,
        f"MATCH (d:{ctx.label('Document')})-[:IN_SPACE]->(s:{ctx.label('Space')}) "
        "RETURN d.key AS doc, s.name AS space",
    )
    assert rows == [{"doc": "KIP-5", "space": "KAFKA"}]


def test_every_assigned_to_edge_says_where_its_identity_came_from(loaded, ctx):
    rows = q(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')})-[r:ASSIGNED_TO]->(:{ctx.label('Person')}) "
        "RETURN w.key AS key, r.source AS source ORDER BY key",
    )
    assert {r["source"] for r in rows} <= {"changelog", "field"}
    assert dict((r["key"], r["source"]) for r in rows) == {
        "KAFKA-100": "changelog",
        "KAFKA-101": "field",
        "KAFKA-102": "field",
    }
    assert loaded["loaders"]["workitem_edges"]["assignments_zero_length_dropped"] == 0
    zero = q(
        ctx,
        f"MATCH ()-[r:ASSIGNED_TO]->(:{ctx.label('Person')}) "
        "WHERE r.valid_to IS NOT NULL AND r.valid_to <= r.valid_from RETURN count(r) AS c",
    )
    assert zero == [{"c": 0}]
    # the displaced identity is offered to `brain resolve`, never merged here
    assert loaded["alias_candidates"] == [
        {
            "changelog_identity": "jira:JIRAUSER999",
            "field_identity": "jira:dlee",
            "work_items": 1,
            "examples": ["KAFKA-101"],
        }
    ]


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
    assert [r["key"] for r in rows] == ["XE-1", "XP-1", "XT-1", "XT-2"]


def test_the_report_records_what_it_read_and_what_it_could_not_explain(loaded):
    inputs = loaded["inputs"]
    assert inputs["workitems.jsonl"]["records"] == 7
    assert len(inputs["workitems.jsonl"]["sha256"]) == 64
    assert loaded["invariants"]["links_to_same_type_both_directions"] == 0
    assert loaded["invariants"]["extra_edges"] == {}


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
