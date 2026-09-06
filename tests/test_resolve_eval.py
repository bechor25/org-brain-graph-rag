from __future__ import annotations

from brain.resolve.evaluate import TARGET, predicted, run_eval, score
from brain.resolve.gold import GOLD_NAME, write_gold
from brain.resolve.ledger import ResolutionLedger


def gold_rows():
    return [
        {"kind": "person", "a": "ado:a", "b": "jira:x", "label": "same"},
        {"kind": "person", "a": "xray:b", "b": "jira:x", "label": "same"},
        {"kind": "person", "a": "ado:a", "b": "xray:b", "label": "same"},
        {"kind": "person", "a": "ado:c", "b": "jira:x", "label": "different"},
    ]


def ledger_with(*rows):
    ledger = ResolutionLedger()
    for identity, canonical, tier in rows:
        ledger.record("person", [(identity, canonical, {"tier": tier, "rule": "r", "score": 1.0})])
    ledger.available = True
    return ledger


def test_a_perfect_ledger_scores_one():
    ledger = ledger_with(("ado:a", "jira:x", 1), ("xray:b", "jira:x", 1))
    stats = score(gold_rows(), predicted(ledger, "person", max_tier=3))

    assert (stats["tp"], stats["fp"], stats["fn"]) == (3, 0, 1 - 1)
    assert stats["precision"] == 1.0 and stats["recall"] == 1.0 and stats["f1"] == 1.0
    assert stats["meets_target"] is True


def test_a_wrong_merge_of_a_hard_negative_costs_precision():
    ledger = ledger_with(("ado:a", "jira:x", 1), ("xray:b", "jira:x", 1), ("ado:c", "jira:x", 2))
    stats = score(gold_rows(), predicted(ledger, "person", max_tier=3))

    assert stats["fp"] == 1
    assert stats["precision"] == round(3 / 4, 4)
    assert stats["false_positives"][0]["a"] == "ado:c"


def test_a_missing_merge_costs_recall_and_is_named():
    ledger = ledger_with(("ado:a", "jira:x", 1))
    stats = score(gold_rows(), predicted(ledger, "person", max_tier=3))

    assert stats["tp"] == 1 and stats["fn"] == 2
    assert stats["recall"] == round(1 / 3, 4)
    assert {r["a"] for r in stats["false_negatives"]} == {"xray:b", "ado:a"}


def test_scoring_a_tier_replays_only_that_tiers_hops():
    """Tier 1 linked ado:a to jira:x; tier 2 later moved jira:x. Tier 1 keeps its credit."""
    ledger = ledger_with(("ado:a", "jira:x", 1), ("jira:x", "jira:z", 2))
    through_1 = score(gold_rows(), predicted(ledger, "person", max_tier=1))
    through_2 = score(gold_rows(), predicted(ledger, "person", max_tier=2))

    assert through_1["tp"] == 1  # ado:a - jira:x, and nothing tier 2 did
    assert through_1["merged_pairs"] == 1
    assert through_2["merged_pairs"] == 3  # ado:a, jira:x and jira:z are one node


def test_merges_the_gold_says_nothing_about_are_counted_not_graded():
    ledger = ledger_with(("git:someone", "jira:elsewhere", 1))
    stats = score(gold_rows(), predicted(ledger, "person", max_tier=3))

    assert stats["ungraded_merges"] == 1
    assert stats["fp"] == 0 and stats["tp"] == 0


def test_an_empty_ledger_scores_recall_zero_and_precision_undefined():
    stats = score(gold_rows(), predicted(ResolutionLedger(), "person", max_tier=3))
    assert stats["recall"] == 0.0 and stats["precision"] is None and stats["f1"] is None
    assert stats["meets_target"] is False


def test_run_eval_writes_a_section_per_tier(tmp_path):
    canonical, eval_dir, reports = (tmp_path / n for n in ("canonical", "eval", "reports"))
    for d in (canonical, eval_dir, reports):
        d.mkdir()
    write_gold(eval_dir / GOLD_NAME, gold_rows())
    ledger_with(("ado:a", "jira:x", 1), ("xray:b", "jira:x", 2)).write(canonical)

    section, code = run_eval(
        canonical_dir=canonical,
        eval_dir=eval_dir,
        reports_dir=reports,
        kinds=["person"],
        echo=lambda _m: None,
    )

    assert code == 0
    person = section["eval"]["person"]
    assert person["gold_pairs"] == 4 and person["positives"] == 3 and person["negatives"] == 1
    assert person["through_tier_1"]["tp"] == 1
    assert person["through_tier_2"]["tp"] == 3
    assert section["eval"]["target"] == TARGET
    assert (reports / "resolve.json").is_file()
