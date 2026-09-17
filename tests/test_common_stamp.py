"""Every step report says which commit measured it — the rule, and its five writers.

Conventions, "לקחים מסקירת סגירת Plan 1": a measurement carried forward without the sha it
was taken at is STALE, not evidence. `brain eval report` applies that per input, and an
input with no top-level `sha` lands in neither state — the header table prints "בלי sha",
which is an absence pretending to be a status. Five reports used to sit there.

The stamp is asserted twice over: once as a function, and once per writer. Once per writer
because the rule is worth nothing unless it holds at the moment the file is written, and the
five write very differently — a whole-file replace, a key merge, a `sections` index, a
section merge with a run history. A test of the helper alone would pass while a writer
quietly forgot to call it.
"""

from __future__ import annotations

import json
from pathlib import Path

from brain.common import stamp
from brain.eval import questions as questions_mod
from brain.harvest import report as harvest_report
from brain.index import runner as index_runner
from brain.resolve import evaluate as resolve_eval
from brain.resolve.gold import GOLD_NAME, write_gold
from brain.resolve.ledger import ResolutionLedger
from brain.retrieve import examples as examples_mod
from brain.retrieve import report as retrieve_report

HEAD = stamp.head_sha(default=stamp.UNKNOWN)


def stamped(path: Path) -> dict:
    """The written report, asserted to carry both halves of the stamp."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["sha"] == HEAD, f"{path} was written without the commit that produced it"
    assert data["generated_at"], f"{path} was written without a time"
    return data


# ---------------------------------------------------------------------------- the helper


def test_the_stamp_is_the_full_head_sha_and_a_utc_time():
    report = stamp.stamp_report({"step": "demo"})

    assert report["sha"] == HEAD
    assert report["generated_at"].endswith("+00:00")
    assert report["step"] == "demo", "stamping is additive, it does not rewrite the report"


def test_a_tree_without_git_stamps_unknown_rather_than_nothing(tmp_path, monkeypatch):
    """`"unknown"` reads as STALE downstream; a missing key reads as nothing at all."""
    assert stamp.head_sha(tmp_path / "nowhere", default=stamp.UNKNOWN) == stamp.UNKNOWN
    assert stamp.head_sha(tmp_path / "nowhere", default=None) is None

    monkeypatch.setattr(stamp, "head_sha", lambda *_a, **_k: None)
    assert stamp.stamp_report({})["sha"] == stamp.UNKNOWN


def test_a_writer_that_knows_its_own_commit_keeps_it():
    """A merge carries the sha the run was measured at, not the sha of the write."""
    assert stamp.stamp_report({}, sha="0" * 40)["sha"] == "0" * 40


def test_the_three_private_copies_are_now_one_helper():
    """`brain index`, `brain competency` and the example bank each had their own.

    They disagreed only about the value for "no git" — which is exactly the difference the
    `default` parameter carries now — so the same tree must give all three the same sha.
    """
    assert index_runner.head_sha(Path.cwd()) == HEAD
    assert retrieve_report.head_sha() == HEAD
    assert examples_mod.head_sha() == HEAD
    # And the fallbacks the callers were relying on are unchanged.
    assert index_runner.head_sha(Path("/nowhere-at-all")) is None
    assert examples_mod.head_sha.__module__ == "brain.retrieve.examples"


# --------------------------------------------------------------------------- the writers


def test_harvest_json_is_stamped(tmp_path):
    path = harvest_report.write_report(tmp_path / "harvest.json", {"step": "harvest"})
    assert stamped(path)["step"] == "harvest"


def test_index_json_is_stamped(tmp_path):
    path = index_runner.write_index_report(tmp_path, {"step": "index", "nodes": {"total": 1}})
    assert stamped(path)["nodes"] == {"total": 1}


def test_resolve_json_is_stamped(tmp_path):
    canonical, eval_dir, reports = (tmp_path / n for n in ("canonical", "eval", "reports"))
    for d in (canonical, eval_dir, reports):
        d.mkdir()
    write_gold(eval_dir / GOLD_NAME, [{"kind": "person", "a": "x", "b": "y", "label": "same"}])
    ResolutionLedger().write(canonical)

    resolve_eval.run_eval(
        canonical_dir=canonical,
        eval_dir=eval_dir,
        reports_dir=reports,
        kinds=["person"],
        echo=lambda _m: None,
    )
    assert stamped(reports / "resolve.json")["eval"]["person"]["gold_pairs"] == 1


def test_retrieve_json_is_stamped_at_the_top_and_not_listed_as_a_section(tmp_path):
    """Four steps merge into this one file; the stamp belongs to the file, not to a section."""
    path = retrieve_report.merge_sections({"mcp": {"transport": "stdio"}}, tmp_path / "r.json")
    written = stamped(path)

    assert written["mcp"] == {"transport": "stdio"}
    assert set(written["sections"]) == {"mcp"}, "`sha`/`generated_at` are the file's own stamp"


def test_eval_questions_json_is_stamped(tmp_path):
    path = questions_mod.write_section(tmp_path, "merge", {"complete": True})
    assert stamped(path)["merge"] == {"complete": True}
