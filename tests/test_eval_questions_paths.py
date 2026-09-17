"""The path shapes: what each one puts in front of the forger, and what it withholds.

Every builder here is a pure function from a Cypher row to nodes/edges/facts, which is the
whole reason the shapes are split in two. The queries need a database; the decision about
*which node is the answer and therefore may not appear in the question* does not, and that
decision is the one the leakage guard depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.common.jsonschema_mini import unsupported_keywords
from brain.eval import paths as P

SCHEMA = json.loads(Path("brain/eval/question_schema.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------- the registry


def test_the_validator_implements_every_keyword_the_question_schema_uses():
    """`jsonschema_mini` ignores a keyword it does not know, which would be a silent hole."""
    assert unsupported_keywords(SCHEMA) == set()


def test_every_question_type_the_schema_allows_has_at_least_one_shape_that_produces_it():
    for qtype in SCHEMA["$defs"]["type"]["enum"]:
        assert P.shapes_for(qtype), f"no path shape produces {qtype} questions"


def test_the_shape_registry_and_the_schema_agree_on_the_closed_sets():
    assert {s.question_type for s in P.SHAPES} == set(SCHEMA["$defs"]["type"]["enum"])
    assert {s.gold_source for s in P.SHAPES} <= set(SCHEMA["$defs"]["gold_source"]["enum"])
    assert {s.expected_strategy for s in P.SHAPES} <= set(SCHEMA["$defs"]["strategy"]["enum"])


def test_shape_names_are_unique_because_a_path_id_is_shape_plus_key():
    assert len({s.name for s in P.SHAPES}) == len(P.SHAPES)


def test_every_shape_declares_both_an_anchor_and_an_answer_side():
    """A shape with no answer side cannot leak, which means its leakage check is vacuous."""
    for shape in P.SHAPES:
        assert shape.anchor_roles, shape.name
        assert shape.answer_roles, shape.name
        assert not set(shape.anchor_roles) & set(shape.answer_roles), shape.name


def test_every_shape_can_actually_be_sampled_by_one_of_the_two_mechanisms():
    for shape in P.SHAPES:
        if shape.gold_source == "truth":
            assert shape.truth_section and shape.truth_keys and shape.truth_build, shape.name
        else:
            assert shape.select and shape.detail and shape.build, shape.name


def test_a_truth_shape_never_promises_the_graph_as_its_gold_source():
    for shape in P.SHAPES:
        if shape.truth_section:
            assert shape.gold_source == "truth", shape.name


# --------------------------------------------------------------------------- builders


def test_the_test_fix_shape_hands_over_the_issue_and_hides_the_tests_and_commits():
    nodes, edges, facts = P._build_test_fix(
        {
            "work_item": {"key": "KAFKA-1", "title": "t", "status": "Resolved", "type": "Bug"},
            "tests": [
                {
                    "key": "XT-1",
                    "title": "covers it",
                    "runs": [{"execution": "XE-1", "status": "FAIL", "at": "2024-01-02T00:00:00Z"}],
                }
            ],
            "commits": [{"sha": "a" * 40, "message": "fix\nbody", "at": "2024-01-03T00:00:00Z"}],
        }
    )
    assert P._roles(nodes, ("work_item",)) == ["KAFKA-1"]
    assert P._roles(nodes, ("test", "commit")) == ["XT-1", "a" * 40]
    assert {e["type"] for e in edges} == {"TESTS", "HAS_RUN", "RESOLVES"}
    assert facts["run_statuses"] == ["FAIL"]
    assert "XE-1 FAIL @ 2024-01-02" in nodes[1]["props"]["runs"]


def test_a_test_that_never_ran_says_so_instead_of_inventing_a_status():
    nodes, _, facts = P._build_test_fix(
        {
            "work_item": {"key": "KAFKA-1"},
            "tests": [{"key": "XT-1", "runs": [{"execution": None, "status": None, "at": None}]}],
            "commits": [],
        }
    )
    assert facts["run_statuses"] == []
    assert "runs" not in nodes[1]["props"]


def test_the_rationale_shape_offers_the_entity_evidence_chunks_it_will_be_quoted_from():
    nodes, edges, facts = P._build_decision_rejects(
        {
            "document": {"key": "KIP-1", "title": "d", "kind": "KIP"},
            "decisions": [
                {
                    "id": "Decision|x",
                    "name": "x",
                    "kind": "Decision",
                    "description": "why",
                    "evidence_chunk_ids": ["c1", "c2", "c3"],
                }
            ],
            "alternatives": [{"id": "Alternative|y", "name": "y", "kind": "Alternative"}],
        }
    )
    sample = P.PathSample(
        shape="decision_rejects",
        question_type="rationale",
        gold_source="graph",
        expected_strategy="s3",
        path_shape="x",
        path_key="KIP-1",
        nodes=nodes,
        edges=edges,
        anchors=P._roles(nodes, ("document",)),
        answers=P._roles(nodes, ("decision", "alternative")),
        facts=facts,
    )
    # Two per entity, not three: an entity's provenance is a pointer, not a corpus.
    assert sample.evidence_chunk_ids() == ["c1", "c2"]
    assert sample.anchors == ["KIP-1"]
    assert sample.answers == ["Decision|x", "Alternative|y"]


def test_the_timeline_shape_computes_the_dates_a_temporal_question_needs():
    nodes, edges, facts = P._build_ownership_timeline(
        {
            "work_item": {"key": "KAFKA-2", "title": "t"},
            "assignments": [
                {"person": "jira:a", "display": "A", "valid_from": "2024-01-01T00:00:00Z"},
                {"person": "jira:b", "display": "B", "valid_from": "2024-03-01T00:00:00Z"},
            ],
            "changes": [
                {
                    "id": "s1",
                    "field": "status",
                    "from": "Open",
                    "to": "In Progress",
                    "at": "2024-01-05T00:00:00Z",
                },
                {
                    "id": "s2",
                    "field": "status",
                    "from": "In Progress",
                    "to": "Resolved",
                    "at": "2024-04-05T00:00:00Z",
                },
            ],
        }
    )
    assert facts["first_change"] == "2024-01-05"
    assert facts["last_change"] == "2024-04-05"
    assert facts["midpoint_date"] == "2024-04-05"
    assert P._roles(nodes, ("assignee",)) == ["jira:a", "jira:b"]
    assert any(e["type"] == "ASSIGNED_TO" and e["props"]["valid_from"] for e in edges)


def test_the_truth_builders_carry_the_recorded_truth_as_facts_not_as_a_graph_claim():
    nodes, edges, facts = P._truth_stale_state(
        {
            "ado_key": "ADO-1",
            "jira_key": "KAFKA-1",
            "ado_status": "Active",
            "jira_status": "Resolved",
        },
        [{"key": "ADO-1", "label": "WorkItem", "title": "mirror", "status": "Active"}],
    )
    assert facts["ado_status_recorded"] == "Active"
    assert facts["jira_status_recorded"] == "Resolved"
    assert nodes[0]["props"]["in_graph"] is True
    assert nodes[1]["props"]["in_graph"] is False
    assert edges[0]["type"] == "REFERENCES"


def test_the_impact_facts_say_shown_and_total_apart_the_way_the_community_facts_do():
    """A forger read the old `open_items` as the component's real count and said so."""
    _, _, facts = P._build_blast_radius(
        {
            "component": "clients",
            "open_items_total": 716,
            "items": [{"key": "K-1", "title": "t", "tests": [], "documents": []}],
        }
    )
    assert facts["open_items_shown"] == 1
    assert facts["open_items_total"] == 716
    assert "open_items" not in facts, "the ambiguous name is gone, not aliased"


def test_the_rejected_alternative_offers_the_chunk_that_says_why_it_was_rejected():
    """The entity's evidence says where it was NAMED; the edge's says where it was TURNED DOWN."""
    nodes, edges, _ = P._build_decision_rejects(
        {
            "document": {"key": "KIP-1", "title": "d", "kind": "KIP"},
            "decisions": [],
            "alternatives": [
                {
                    "id": "Alternative|y",
                    "name": "y",
                    "kind": "Alternative",
                    "evidence_chunk_ids": ["named-in"],
                    "grounds_chunk_ids": ["the-grounds"],
                    "note": "too slow",
                }
            ],
        }
    )
    alternative = nodes[1]
    assert alternative["props"]["evidence_chunk_ids"][0] == "the-grounds"
    assert "named-in" in alternative["props"]["evidence_chunk_ids"]
    assert alternative["props"]["grounds_chunk_ids"] == ["the-grounds"]
    assert edges[0]["props"]["evidence_chunk_ids"] == ["the-grounds"]
    assert edges[0]["props"]["note"] == "too slow"


def test_the_rationale_facts_do_not_pass_off_a_capped_arm_as_a_total():
    _, _, facts = P._build_decision_rejects(
        {"document": {"key": "KIP-1"}, "decisions": [], "alternatives": []}
    )
    assert facts["decisions_shown"] == 0
    assert facts["shown_cap"] == 4
    assert "not totals" in facts["note"]


def test_the_community_path_carries_the_reports_own_evidence_chunks():
    """A community whose members carry no chunks would otherwise ship with `snippets: []`."""
    nodes, _, _ = P._build_community_theme(
        {
            "community": {
                "id": "L1-489",
                "title": "t",
                "summary": "s",
                "rank": 7,
                "level": 1,
                "size": 40,
                "finding_statements": ["f"],
                "evidence_chunk_ids": ["c1", "c2", "c3", "c4", "c5"],
            },
            "members": [],
        }
    )
    sample = P.PathSample(
        shape="community_theme",
        question_type="global",
        gold_source="graph",
        expected_strategy="s5",
        path_shape="x",
        path_key="L1-489",
        nodes=nodes,
        edges=[],
        anchors=[],
        answers=[],
    )
    assert sample.evidence_chunk_ids() == ["c1", "c2", "c3", "c4"]


def test_a_community_member_keeps_the_projected_degree_it_was_ranked_by():
    nodes, _, _ = P._build_community_theme(
        {
            "community": {"id": "L0-1", "title": "t", "summary": "s", "size": 2},
            "members": [
                {"key": "KAFKA-1", "label": "WorkItem", "title": "a", "degree": 12.0},
                {"key": "KIP-9", "label": "Document", "title": "b", "degree": 3.0},
            ],
        }
    )
    assert [n["key"] for n in nodes[1:]] == ["KAFKA-1", "KIP-9"]
    assert nodes[1]["props"]["degree"] == 12.0


def test_the_community_query_ranks_members_by_projected_degree_not_by_label_then_key():
    """L1-489 shipped KIP-1, KIP-10, KIP-11 while its summary was about KIP-98 and KIP-724."""
    shape = P.SHAPES_BY_NAME["community_theme"]
    assert "ORDER BY coalesce(rel.degree, 0) DESC" in shape.detail
    assert "WHEN 'Document' THEN 0" not in shape.detail
    assert "evidence_chunk_ids: c.evidence_chunk_ids" in shape.detail


def test_a_node_key_is_always_a_string_so_an_evidence_id_can_be_compared_verbatim():
    assert P.node("pr", "PullRequest", 1234)["key"] == "1234"
    assert P.node("x", "WorkItem", None)["key"] == ""


def test_a_snippet_is_clipped_to_the_contract_and_keeps_its_chunk_id():
    snippet = P._snippet(
        {"chunk_id": "c" * 40, "parent_key": "KAFKA-1", "kind": "description", "text": "x " * 500}
    )
    assert snippet["chunk_id"] == "c" * 40
    assert len(snippet["text"]) <= P.SNIPPET_CHARS


# --------------------------------------------------------------------------- selection


def test_the_seeded_pick_is_reproducible_and_keeps_the_pool_order():
    pool = [f"k{i}" for i in range(20)]
    first = P._pick(pool, 5, seed=2026, salt="test_fix")
    assert first == P._pick(pool, 5, seed=2026, salt="test_fix")
    assert first == sorted(first, key=pool.index)
    assert len(set(first)) == 5


def test_a_different_shape_picks_differently_from_the_same_pool():
    pool = [f"k{i}" for i in range(20)]
    assert P._pick(pool, 5, 2026, "test_fix") != P._pick(pool, 5, 2026, "story_kip")


def test_a_pool_smaller_than_the_ask_is_returned_whole_rather_than_padded():
    assert P._pick(["a", "b"], 5, 1, "x") == ["a", "b"]


class PoolCtx:
    """Answers a shape's `select` with a fixed pool and its `detail` with one row."""

    prefix = ""

    def __init__(self, keys):
        self.keys = list(keys)
        self.detail_keys: list[str] = []

    def label(self, name):
        return f"`{name}`"

    def read(self, cypher, **params):
        if "path_key" in cypher:
            return [{"path_key": k, "richness": 1} for k in self.keys]
        self.detail_keys.append(params["key"])
        return [{"document": {"key": params["key"], "title": "t", "kind": "KIP"}, "items": []}]


def test_a_sampler_never_hands_back_a_path_the_caller_already_took():
    """The spare phase excludes phase one's picks, or a batch gets its own path as a spare."""
    shape = P.SHAPES_BY_NAME["story_kip"]
    ctx = PoolCtx([f"KIP-{i}" for i in range(12)])
    got = P.sample_graph_shape(ctx, shape, 3, exclude={"KIP-0", "KIP-1", "KIP-2"})
    assert {p.path_key for p in got}.isdisjoint({"KIP-0", "KIP-1", "KIP-2"})
    assert len(got) == 3


def test_an_excluded_truth_row_is_skipped_by_the_key_it_would_have_produced():
    shape = P.SHAPES_BY_NAME["duplicate_test"]
    truth = {"duplicate_tests": [{"a": f"XT-{i}", "b": f"XT-{i + 1}"} for i in range(0, 10, 2)]}

    class LookupCtx(PoolCtx):
        def read(self, cypher, **params):
            return [
                {
                    "key": k,
                    "label": "Test",
                    "title": "t",
                    "status": None,
                    "type": None,
                    "source": "xray",
                }
                for k in params.get("keys", [])
            ]

    got = P.sample_truth_shape(LookupCtx([]), shape, 2, truth, exclude={"XT-0-XT-1"})
    assert "XT-0-XT-1" not in {p.path_key for p in got}


def test_round_robin_spreads_the_remainder_over_the_first_shapes():
    assert P._round_robin(7, 3) == [3, 2, 2]
    assert P._round_robin(2, 3) == [1, 1, 0]
    assert P._round_robin(5, 0) == []


def test_the_path_id_is_the_shape_and_the_key_so_two_shapes_can_share_an_anchor():
    sample = P.PathSample(
        shape="motivation",
        question_type="rationale",
        gold_source="graph",
        expected_strategy="s3",
        path_shape="x",
        path_key="KIP-848",
        nodes=[],
        edges=[],
        anchors=[],
        answers=[],
    )
    assert sample.path_id == "motivation:KIP-848"
    assert sample.spare is True
    sample.asks = [{"lang": "he", "questions": 1}]
    assert sample.spare is False


def test_the_context_it_ships_names_every_field_the_forger_contract_promises():
    sample = P.PathSample(
        shape="test_fix",
        question_type="traceability",
        gold_source="graph",
        expected_strategy="s3",
        path_shape="x",
        path_key="KAFKA-1",
        nodes=[P.node("work_item", "WorkItem", "KAFKA-1")],
        edges=[],
        anchors=["KAFKA-1"],
        answers=[],
    )
    ctx = sample.context()
    for field in ("path_id", "type", "nodes", "edges", "snippets", "truth", "anchors", "answers"):
        assert field in ctx


def test_loading_a_truth_file_that_is_not_there_says_what_to_run_instead_of_raising_oserror(
    tmp_path: Path,
):
    with pytest.raises(P.PathError, match="brain synth merge"):
        P.load_truth(tmp_path / "nope.json")
