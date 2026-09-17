"""Mode-A answer batches: what a case carries, and what a batch is never allowed to carry.

Everything here runs against tiny synthetic run files, because the three properties worth
proving are properties of the packing and not of the corpus: the context arrives whole, two
cases of one question never share a batch, and a batch is measured rather than estimated
against the 40 KB budget.
"""

from __future__ import annotations

import json

import pytest

from brain.eval import answers_batches as ab
from brain.eval import casebatch


def run_record(qid="q001", strategy="s1", *, keys=("KAFKA-1",), status="ok", snippet="hello"):
    items = [
        {
            "kind": "Chunk",
            "key": f"{i:040x}",
            "title": f"{key} · description",
            "snippet": snippet,
            "score": 0.5,
            "props": {"parent_key": key, "parent_kind": "WorkItem", "label": "Chunk"},
            "provenance": [{"chunk_id": f"{i:040x}", "quote": snippet, "source": key}],
        }
        for i, key in enumerate(keys, start=1)
    ]
    return {
        "qid": qid,
        "strategy": strategy,
        "mode": "fixed",
        "question": f"who broke {qid}?",
        "type": "traceability",
        "lang": "en",
        "gold_evidence": list(keys),
        "status": status,
        "reason": None if status == "ok" else "did not apply",
        "result": {"strategy": strategy, "items": items, "cypher_used": [], "latency_ms": 1},
        "items": len(items),
        "context_tokens": 100,
        "cypher_count": 0,
    }


def write_runs(tmp_path, qids, strategies, **kwargs):
    runs_dir = tmp_path / "runs" / "fixed"
    runs_dir.mkdir(parents=True)
    for qid in qids:
        for strategy in strategies:
            record = run_record(qid, strategy, **kwargs)
            (runs_dir / f"{qid}.{strategy}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
    return runs_dir


def rows(qids):
    return [
        {"id": q, "type": "traceability", "lang": "en", "question": f"who broke {q}?"} for q in qids
    ]


# --------------------------------------------------------------------------- the context


def test_context_item_keeps_the_citable_ids_and_drops_the_duplicate_quote():
    item = run_record(keys=("KAFKA-1",))["result"]["items"][0]
    projected = ab.context_item(item)
    assert projected["kind"] == "Chunk"
    assert projected["props"]["parent_key"] == "KAFKA-1"
    # The quote equalled the snippet, so only the id is shipped — the text is right there.
    assert projected["provenance"][0]["chunk_id"] == f"{1:040x}"
    assert "quote" not in projected["provenance"][0]


def test_context_item_keeps_a_quote_that_says_something_the_snippet_does_not():
    item = run_record()["result"]["items"][0]
    item["provenance"][0]["quote"] = "a different sentence entirely"
    assert ab.context_item(item)["provenance"][0]["quote"] == "a different sentence entirely"


def test_context_keys_cover_the_item_its_parent_and_its_chunks():
    context = ab.context_of(run_record(keys=("KAFKA-1",)))
    assert ab.context_keys(context) == {f"{1:040x}", "KAFKA-1"}


@pytest.mark.parametrize(
    "cited,expected",
    [
        ("KAFKA-1", "KAFKA-1"),
        ("chunk:" + f"{1:040x}", f"{1:040x}"),
        (f"{1:040x}"[:12], f"{1:040x}"),  # a truncated chunk prefix still resolves
        ("KAFKA-9", None),
        ("", None),
    ],
)
def test_cited_key_matches_accepts_the_spellings_an_agent_actually_writes(cited, expected):
    keys = ab.context_keys(ab.context_of(run_record(keys=("KAFKA-1",))))
    assert ab.cited_key_matches(cited, keys) == expected


def test_context_sha_changes_when_the_context_does():
    one = ab.context_of(run_record(snippet="a"))
    two = ab.context_of(run_record(snippet="b"))
    assert ab.context_sha(one) != ab.context_sha(two)
    assert ab.context_sha(one) == ab.context_sha(ab.context_of(run_record(snippet="a")))


# ----------------------------------------------------------------------------- the build


def test_build_writes_one_case_per_ok_run_and_skips_the_rest(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001", "q002"], ["s1", "s3"])
    bad = json.loads((runs_dir / "q002.s3.json").read_text(encoding="utf-8"))
    bad["status"] = "n/a"
    bad["result"] = None
    (runs_dir / "q002.s3.json").write_text(json.dumps(bad), encoding="utf-8")

    manifest = ab.run_build(
        rows=rows(["q001", "q002"]),
        strategies=("s1", "s3"),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=2,
        echo=lambda _: None,
    )
    assert manifest["totals"]["cases"] == 3
    assert manifest["totals"]["skipped"] == 1
    assert manifest["skipped"][0]["why"] == "n/a"
    assert manifest["totals"]["by_strategy"] == {"s1": 2, "s3": 1}


def test_a_retrieval_that_returned_nothing_is_still_a_case(tmp_path):
    """Dropping it would average the strategy over one question fewer than its rivals."""
    runs_dir = write_runs(tmp_path, ["q001"], ["s1"])
    record = json.loads((runs_dir / "q001.s1.json").read_text(encoding="utf-8"))
    record["result"]["items"] = []
    record["items"] = 0
    (runs_dir / "q001.s1.json").write_text(json.dumps(record), encoding="utf-8")

    manifest = ab.run_build(
        rows=rows(["q001"]),
        strategies=("s1",),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=1,
        echo=lambda _: None,
    )
    assert manifest["totals"]["cases"] == 1
    assert manifest["totals"]["empty_context"] == 1
    assert manifest["empty_context"] == ["q001.s1"]
    payload = json.loads(
        (tmp_path / "batches" / "answers" / "shard-01" / "001.in.json").read_text(encoding="utf-8")
    )
    assert payload["cases"][0]["context"] == []


def test_a_batch_never_carries_two_cases_of_one_question(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001"], ["s1", "s1r", "s2", "s3"])
    manifest = ab.run_build(
        rows=rows(["q001"]),
        strategies=("s1", "s1r", "s2", "s3"),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=1,  # one shard, so only the packer can keep them apart
        echo=lambda _: None,
    )
    assert manifest["batches_with_a_repeated_question"] == []
    for batch in manifest["batches"]:
        qids = [ab.split_case_id(c)[0] for c in batch["case_ids"]]
        assert len(qids) == len(set(qids)), batch


def test_every_batch_is_inside_the_forty_kilobyte_budget(tmp_path):
    long_snippet = "x " * 4000
    runs_dir = write_runs(
        tmp_path, [f"q{i:03d}" for i in range(6)], ["s1", "s3"], snippet=long_snippet
    )
    manifest = ab.run_build(
        rows=rows([f"q{i:03d}" for i in range(6)]),
        strategies=("s1", "s3"),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=2,
        echo=lambda _: None,
    )
    assert manifest["sizes"]["over_budget"] == []
    assert manifest["sizes"]["max_bytes"] <= casebatch.MAX_BATCH_BYTES
    assert manifest["sizes"]["max_line_bytes"] <= casebatch.MAX_LINE_BYTES


def test_the_batch_tells_the_agent_the_rules_and_names_the_schema(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001"], ["s1"])
    ab.run_build(
        rows=rows(["q001"]),
        strategies=("s1",),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=1,
        echo=lambda _: None,
    )
    payload = json.loads(
        (tmp_path / "batches" / "answers" / "shard-01" / "001.in.json").read_text(encoding="utf-8")
    )
    assert payload["schema_path"] == ab.SCHEMA_REF
    assert payload["agent"] == ab.AGENT_REF
    assert payload["cases"][0]["case_id"] == "q001.s1"
    assert payload["cases"][0]["instructions"].startswith("Answer the question using only")
    assert any("no tools in this mode" in rule for rule in payload["rules"])
    status = json.loads(
        (tmp_path / "batches" / "answers" / "shard-01" / "status.json").read_text(encoding="utf-8")
    )
    assert status == {"shard": "shard-01", "done": [], "failed": []}


def test_a_rebuild_over_a_working_agent_is_refused(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001"], ["s1"])
    kwargs = dict(
        rows=rows(["q001"]),
        strategies=("s1",),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=1,
        echo=lambda _: None,
    )
    ab.run_build(**kwargs)
    shard = tmp_path / "batches" / "answers" / "shard-01"
    (shard / "001.out.json").write_text('{"batch_id": "shard-01/001"}', encoding="utf-8")
    with pytest.raises(ab.AnswersError, match="refusing to rebuild"):
        ab.run_build(**kwargs)
    ab.run_build(**{**kwargs, "force": True})  # --force is the way out


def test_a_build_with_nothing_ok_says_so_rather_than_writing_an_empty_batch(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001"], ["s1"], status="n/a")
    with pytest.raises(ab.AnswersError, match="no `ok` run files"):
        ab.run_build(
            rows=rows(["q001"]),
            strategies=("s1",),
            runs_dir=runs_dir,
            batches_dir=tmp_path / "batches",
            reports_dir=tmp_path / "reports",
            echo=lambda _: None,
        )


def test_the_build_is_idempotent(tmp_path):
    runs_dir = write_runs(tmp_path, ["q001", "q002"], ["s1", "s3"])
    kwargs = dict(
        rows=rows(["q001", "q002"]),
        strategies=("s1", "s3"),
        runs_dir=runs_dir,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        shards=2,
        echo=lambda _: None,
    )
    first = ab.run_build(**kwargs)
    second = ab.run_build(**{**kwargs, "force": True})
    assert [b["sha256"] for b in first["batches"]] == [b["sha256"] for b in second["batches"]]
    assert all(b["rewritten"] is False for b in second["batches"])
