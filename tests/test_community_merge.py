"""What a community report has to survive before it becomes a node.

Three gates: the JSON Schema, pydantic, and the two rules that need the batch *input* —
a community this batch never asked about, and a chunk id that was offered to a different
community. The last one is the important one: the agent has no database, so every id it can
legitimately cite is one it was handed, and a citation of somebody else's chunk is an
invented link dressed as provenance.

Nothing here touches Neo4j. The rows `node_rows` produces are where "every LLM-derived node
carries its evidence, its batch, its model and when" is either true or not.
"""

from __future__ import annotations

import json

from brain.community.graph import MODEL, PROVENANCE_PROPS, decode_findings, encode_findings
from brain.community.merge import (
    embed_hash,
    embed_text,
    findings_without_evidence,
    node_rows,
    report_is_current,
)
from brain.community.models import Report
from tests.community_helpers import (
    SUMMARY,
    A,
    B,
    C,
    batch_input,
    batch_output,
    community_context,
    evidence,
    report,
    screened,
    written_batch,
)


def one(tmp_path, *, inp=None, out=None, known=None):
    path = written_batch(tmp_path, inp=inp or batch_input(), out=out or batch_output())
    return screened(path, known)


# ------------------------------------------------------------------------ the happy path


def test_a_valid_report_is_accepted_with_its_findings(tmp_path):
    batch = one(tmp_path)
    assert batch.ok and not batch.rejections
    assert [r.community_id for r in batch.accepted] == ["L0-1"]
    assert batch.accepted[0].evidence_chunk_ids == [A]


def test_provenance_is_stamped_by_the_code_and_never_by_the_agent(tmp_path):
    batch = one(tmp_path)
    rows = node_rows([batch], "2026-09-07T02:00:00+00:00")

    assert len(rows) == 1
    props = rows[0]["props"]
    for prop in PROVENANCE_PROPS:
        assert props.get(prop), prop
    assert props["model"] == MODEL
    assert props["batch_id"] == "shard-01/001"
    assert props["extracted_at"] == "2026-09-07T01:00:00+00:00"
    assert props["reported_at"] == "2026-09-07T02:00:00+00:00"
    assert props["evidence_chunk_ids"] == [A]
    # …and nothing the agent wrote can set them
    assert "model" not in json.loads(json.dumps(batch_output()))["reports"][0]


def test_findings_survive_the_round_trip_through_neo4j_property_types(tmp_path):
    batch = one(tmp_path)
    props = node_rows([batch], "x")[0]["props"]

    assert all(isinstance(f, str) for f in props["findings"])
    assert props["finding_statements"] == [
        "Assignment computed by clients is what makes rebalances long."
    ]
    assert decode_findings(props["findings"]) == [
        {
            "statement": "Assignment computed by clients is what makes rebalances long.",
            "evidence_chunk_ids": [A],
        }
    ]


def test_two_batches_answering_the_same_community_produce_one_deterministic_node(tmp_path):
    first = one(tmp_path)
    second_in = batch_input(batch_id="shard-02/001", shard="shard-02")
    second = one(
        tmp_path,
        inp=second_in,
        out=batch_output(batch_id="shard-02/001", reports=[report(title="Another title")]),
    )
    rows = node_rows([second, first], "x")
    assert len(rows) == 1
    assert rows[0]["props"]["batch_id"] == "shard-01/001"


# ------------------------------------------------------------------- the input-aware rules


def test_a_report_for_a_community_this_batch_never_asked_about_is_rejected(tmp_path):
    batch = one(tmp_path, out=batch_output(reports=[report(community_id="L0-999")]))

    assert batch.accepted == []
    assert [r.reason for r in batch.rejections] == ["unknown_community", "not_answered"]


def test_evidence_offered_to_another_community_is_not_evidence_about_this_one(tmp_path):
    inp = batch_input(
        [
            community_context(community_id="L0-1", evidence=[evidence(A)]),
            community_context(community_id="L0-2", evidence=[evidence(B)]),
        ]
    )
    borrowed = [{"statement": "a claim about somebody else", "evidence_chunk_ids": [B]}]
    its_own = [{"statement": "a claim about its own members", "evidence_chunk_ids": [B]}]
    out = batch_output(
        reports=[
            report(community_id="L0-1", findings=borrowed),
            report(community_id="L0-2", findings=its_own),
        ]
    )
    batch = one(tmp_path, inp=inp, out=out)

    assert [r.community_id for r in batch.accepted] == ["L0-2"]
    rejected = {r.community_id: r.reason for r in batch.rejections}
    assert rejected["L0-1"] == "evidence_not_offered"


def test_a_chunk_that_left_the_graph_since_the_batch_was_built_is_rejected(tmp_path):
    batch = one(tmp_path, known=set())
    assert [r.reason for r in batch.rejections] == ["evidence_not_in_graph"]
    assert batch.accepted == []


def test_a_community_the_batch_asked_about_and_the_output_skipped_is_counted(tmp_path):
    inp = batch_input(
        [community_context(community_id="L0-1"), community_context(community_id="L0-2")]
    )
    batch = one(tmp_path, inp=inp)
    assert [r.community_id for r in batch.accepted] == ["L0-1"]
    assert [(r.community_id, r.reason) for r in batch.rejections] == [("L0-2", "not_answered")]


def test_the_same_community_answered_twice_is_written_once(tmp_path):
    batch = one(tmp_path, out=batch_output(reports=[report(), report(title="second go")]))
    assert len(batch.accepted) == 1
    assert [r.reason for r in batch.rejections] == ["duplicate_report"]


# ---------------------------------------------------------------------- the schema gates


def test_a_finding_with_no_evidence_never_gets_past_the_schema(tmp_path):
    out = batch_output(
        reports=[report(findings=[{"statement": "unsupported claim", "evidence_chunk_ids": []}])]
    )
    batch = one(tmp_path, out=out)

    assert not batch.ok
    assert any("evidence_chunk_ids" in e for e in batch.errors)
    # the acceptance criterion counts what actually reached a node: nothing did
    assert findings_without_evidence([batch]) == 0


def test_a_report_with_no_findings_at_all_is_refused(tmp_path):
    batch = one(tmp_path, out=batch_output(reports=[report(findings=[])]))
    assert not batch.ok
    assert any("findings" in e for e in batch.errors)


def test_a_rank_outside_zero_to_ten_is_refused(tmp_path):
    batch = one(tmp_path, out=batch_output(reports=[report(rank=11)]))
    assert not batch.ok
    assert any("maximum" in e for e in batch.errors)


def test_a_summary_too_short_to_be_a_summary_is_refused(tmp_path):
    batch = one(tmp_path, out=batch_output(reports=[report(summary="It is about Kafka.")]))
    assert not batch.ok


def test_a_batch_id_that_does_not_match_its_filename_is_refused(tmp_path):
    batch = one(tmp_path, out=batch_output(batch_id="shard-09/009"))
    assert not batch.ok
    assert any("batch_id" in e for e in batch.errors)


def test_an_output_with_no_input_beside_it_cannot_be_screened(tmp_path):
    path = written_batch(tmp_path, inp=batch_input(), out=batch_output())
    path.with_name("001.in.json").unlink()
    batch = screened(path)
    assert not batch.ok
    assert any("no input beside it" in e for e in batch.errors)


def test_an_unknown_top_level_field_is_refused(tmp_path):
    batch = one(tmp_path, out={**batch_output(), "confidence": 0.9})
    assert not batch.ok
    assert any("confidence" in e for e in batch.errors)


# ---------------------------------------------------------------------------- embedding


def test_the_embedded_text_is_the_title_then_the_summary():
    r = Report.model_validate(report())
    assert embed_text(r) == f"{r.title}\n\n{SUMMARY}"


def test_the_embed_hash_changes_with_the_text_and_not_with_anything_else():
    r = Report.model_validate(report())
    same = Report.model_validate(report(rank=1.0))
    other = Report.model_validate(report(title="A different theme"))
    assert embed_hash(embed_text(r)) == embed_hash(embed_text(same))
    assert embed_hash(embed_text(r)) != embed_hash(embed_text(other))


def test_encoded_findings_are_stable_so_a_re_merge_writes_the_same_bytes():
    findings = [{"statement": "s", "evidence_chunk_ids": [C, A]}]
    assert encode_findings(findings) == encode_findings(findings)
    assert json.loads(encode_findings(findings)[0])["evidence_chunk_ids"] == [A, C]


# --------------------------------------------------------------- writing only what changed


def props_of(tmp_path) -> dict:
    return node_rows([one(tmp_path)], "2026-09-07T15:00:00+00:00")[0]["props"]


def test_a_report_already_on_the_node_word_for_word_is_not_written_again(tmp_path):
    """A second merge over the same outputs must leave the graph alone, not rewrite it."""
    props = props_of(tmp_path)
    # `reported_at` is when merge ran, not what it says: it differs on every run and is
    # the one property that must not make a report look changed.
    stored = {**props, "reported_at": "2026-09-01T00:00:00+00:00"}
    assert report_is_current(stored, props) is True


def test_a_community_with_no_report_yet_is_not_current(tmp_path):
    assert report_is_current(None, props_of(tmp_path)) is False
    assert report_is_current({}, props_of(tmp_path)) is False


def test_any_changed_property_makes_the_report_stale(tmp_path):
    props = props_of(tmp_path)
    for field, value in (
        ("title", "A different theme"),
        ("rank", 1.0),
        ("summary", "rewritten"),
        ("batch_id", "shard-02/009"),
        ("extracted_at", "2026-09-08T00:00:00+00:00"),
        ("evidence_chunk_ids", [C]),
    ):
        assert report_is_current({**props, field: value}, props) is False, field


def test_a_reordered_findings_list_is_a_real_change_not_a_false_alarm(tmp_path):
    """Order is meaning here: the agent puts the most important finding first."""
    props = props_of(tmp_path)
    stored = {**props, "findings": list(reversed(props["findings"]))}
    assert report_is_current(stored, props) is (len(props["findings"]) == 1)
