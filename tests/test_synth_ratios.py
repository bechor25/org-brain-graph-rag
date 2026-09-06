"""The noise contract, measured. A ratio here is reported, never enforced by the merge."""

from __future__ import annotations

import pytest

from brain.canon.models import WorkItem
from brain.synth.models import Truth
from brain.synth.ratios import PCT_TOLERANCE, classify_display, compute, summarize
from tests.synth_helpers import ado_item, real_item, xray_test


def by_name(ratios) -> dict:
    return {r.name: r for r in ratios}


def measure(*, real, synthetic, truth=None, persons_by_id=None):
    return by_name(
        compute(
            real=[WorkItem.model_validate(r) for r in real],
            synthetic=[WorkItem.model_validate(s) for s in synthetic],
            truth=Truth.model_validate(truth or {}),
            persons_by_id=persons_by_id or {},
        )
    )


# --------------------------------------------------------------------------- coverage


def test_coverage_counts_real_items_a_test_points_at_not_tests():
    real = [real_item(f"KAFKA-{i}") for i in range(1, 5)]
    synthetic = [
        xray_test("XT-10001", covers="KAFKA-1"),
        xray_test("XT-10002", covers="KAFKA-1"),  # same item, second test
        xray_test("XT-10003", covers="KAFKA-2"),
    ]

    ratios = measure(real=real, synthetic=synthetic)

    coverage = ratios["coverage_of_real_stories_and_bugs"]
    assert (coverage.numerator, coverage.denominator, coverage.actual) == (2, 4, 50.0)
    assert ratios["tests_per_covered_item"].actual == 1.5


def test_a_text_only_link_is_coverage_too_because_the_truth_records_it():
    """20% of Tests name the story only in prose; they still cover it."""
    real = [real_item("KAFKA-1"), real_item("KAFKA-2")]
    synthetic = [xray_test("XT-10001", covers=None)]
    truth = {"text_only_links": [{"from_key": "XT-10001", "to_key": "KAFKA-1"}]}

    ratios = measure(real=real, synthetic=synthetic, truth=truth)

    assert ratios["coverage_of_real_stories_and_bugs"].numerator == 1
    assert ratios["test_links_that_are_text_only"].actual == 100.0


def test_a_test_pointing_outside_the_slice_covers_nothing():
    ratios = measure(
        real=[real_item("KAFKA-1")], synthetic=[xray_test("XT-10001", covers="KAFKA-40404")]
    )

    assert ratios["coverage_of_real_stories_and_bugs"].numerator == 0


def test_a_covered_item_with_four_tests_is_flagged():
    real = [real_item("KAFKA-1")]
    synthetic = [xray_test(f"XT-1000{i}", covers="KAFKA-1") for i in range(1, 5)]

    ratios = measure(real=real, synthetic=synthetic)

    assert ratios["covered_items_outside_1_to_3_tests"].actual == 1
    assert ratios["covered_items_outside_1_to_3_tests"].ok is False


# --------------------------------------------------------------------------- runs


def execution(key: str, *, runs: list[str], defect: str | None = None) -> dict:
    return xray_test(
        key,
        covers=None,
        type="TestExecution",
        source="xray",
        id=f"xray:{key}",
        comments=[{"body": line} for line in runs],
        links=[{"type": "defect", "target": defect}] if defect else [],
    )


def test_fail_runs_are_read_from_the_execution_comment_lines():
    real = [real_item("KAFKA-1", type="Bug")]
    synthetic = [
        execution("XE-10001", runs=["XT-10001: FAIL (rebalance storm)"], defect="KAFKA-1"),
        execution("XE-10002", runs=["XT-10002: PASS", "XT-10003: FAIL (timeout)"]),
    ]

    ratios = measure(real=real, synthetic=synthetic)

    assert ratios["runs_recorded"].actual == 3  # 1 PASS + 2 FAIL
    naming = ratios["fail_runs_naming_a_real_bug"]
    assert (naming.numerator, naming.denominator, naming.actual) == (1, 2, 50.0)


def test_a_defect_link_cannot_claim_more_fails_than_the_execution_ran():
    """Two defect links on a one-FAIL execution is one FAIL that named a bug, not two."""
    real = [real_item("KAFKA-1", type="Bug"), real_item("KAFKA-2", type="Bug")]
    synthetic = [
        xray_test(
            "XE-10001",
            covers=None,
            type="TestExecution",
            id="xray:XE-10001",
            comments=[{"body": "XT-10001: FAIL (one)"}],
            links=[
                {"type": "defect", "target": "KAFKA-1"},
                {"type": "defect", "target": "KAFKA-2"},
            ],
        )
    ]

    ratios = measure(real=real, synthetic=synthetic)

    assert ratios["fail_runs_naming_a_real_bug"].numerator == 1


# --------------------------------------------------------------------------- ADO shape


def test_epics_are_counted_against_the_kips_the_slice_cites():
    real = [
        real_item("KAFKA-1", refs=[{"kind": "kip", "key": "KIP-5", "via": "text"}]),
        real_item("KAFKA-2", refs=[{"kind": "kip", "key": "KIP-6", "via": "text"}]),
        real_item("KAFKA-3", refs=[{"kind": "kip", "key": "KIP-5", "via": "text"}]),
    ]
    synthetic = [ado_item("ADO-1", "Epic"), ado_item("ADO-2", "Epic")]

    ratios = measure(real=real, synthetic=synthetic)

    assert ratios["ado_epics"].target == 2  # KIP-5 and KIP-6, not three citations
    assert ratios["ado_epics"].ok is True


def test_the_three_hundred_ado_items_criterion_is_a_floor_not_a_target():
    ratios = measure(real=[real_item("KAFKA-1")], synthetic=[ado_item("ADO-1")])

    assert ratios["ado_items_total"].ok is False
    assert ratios["ado_items_total"].target == 300


# --------------------------------------------------------------------------- identities


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("Rao, Jun", "last_first"),
        ("jrao", "username"),
        ("J. Rao", "initial"),
        ("Jun Rao", "other"),
        ("", "other"),
    ],
)
def test_display_forms_are_classified_the_way_the_spec_names_them(display, expected):
    assert classify_display(display) == expected


def test_a_reporter_that_is_not_a_mapped_new_identity_fails_the_hundred_percent_rule():
    synthetic = [
        xray_test("XT-10001", reporter="rao.jun"),
        xray_test("XT-10002", reporter="jrao"),  # the real Jira username, unmapped
    ]

    ratios = measure(
        real=[real_item("KAFKA-100")],
        synthetic=synthetic,
        truth={"identity_map": {"ado:rao.jun": "jira:jrao"}},
    )

    assert ratios["used_identities_that_are_new_and_mapped"].actual == 50.0
    assert ratios["used_identities_that_are_new_and_mapped"].ok is False


# --------------------------------------------------------------------------- tolerance


def test_a_percentage_target_is_met_within_five_percentage_points():
    """±5% is points, not relative: 40% and 50% both meet a 45% target, 51% does not."""
    real = [real_item(f"KAFKA-{i}") for i in range(1, 101)]

    def coverage(n: int) -> bool:
        synthetic = [xray_test(f"XT-1{i:04d}", covers=f"KAFKA-{i}") for i in range(1, n + 1)]
        return measure(real=real, synthetic=synthetic)["coverage_of_real_stories_and_bugs"].ok

    assert PCT_TOLERANCE == 5.0
    assert coverage(40) and coverage(50)
    assert not coverage(51)


# ------------------------------------------------------------------ a layer built to spec


def spec_shaped_layer() -> tuple[list[dict], list[dict], dict, dict]:
    """A hand-made layer sized to hit every target in `synthetic_spec.md`.

    400 real items (200 Improvement, 200 Bug, 5 KIPs cited) and the exact counts the
    contract asks for. It is the arithmetic of the spec table, written out — so a change
    to a target that nobody meant to make breaks here.
    """
    real = [
        real_item(
            f"KAFKA-{i}",
            type="Improvement" if i <= 200 else "Bug",
            refs=[{"kind": "kip", "key": f"KIP-{i}", "via": "text"}] if i <= 5 else [],
        )
        for i in range(1, 401)
    ]
    covered = [f"KAFKA-{i}" for i in range(1, 181)]  # 45% of 400

    # 324 tests over 180 covered items: 144 with two, 36 with one — mean exactly 1.8.
    plan: list[str] = [k for k in covered[:144] for _ in range(2)] + covered[144:]
    persons = {f"ado:p{i}": f"jira:p{i}" for i in range(10)}
    forms = ["Rao, Jun"] * 3 + ["jrao"] * 3 + ["J. Rao"] * 4
    persons_by_id = {f"ado:p{i}": [forms[i]] for i in range(10)}

    tests: list[dict] = []
    text_only: list[dict] = []
    for n, target in enumerate(plan, start=1):
        key = f"XT-{10000 + n}"
        prose_only = n <= 65  # 20% of 324
        tests.append(xray_test(key, covers=None if prose_only else target, reporter=f"p{n % 10}"))
        if prose_only:
            text_only.append({"from_key": key, "to_key": target})

    synthetic = [*tests, xray_test("XP-10001", covers=None, type="TestPlan", id="xray:XP-10001")]
    for n in range(20):  # 20 FAIL runs, 14 of them naming a real bug — 70%
        synthetic.append(
            execution(
                f"XE-{10001 + n}",
                runs=[f"XT-{10001 + n}: FAIL (flaky)"],
                defect="KAFKA-300" if n < 14 else None,
            )
        )

    ado: list[dict] = [ado_item(f"ADO-{i}", "Epic") for i in range(1, 6)]
    ado += [ado_item(f"ADO-{1000 + i}", "Feature") for i in range(67)]
    ado += [ado_item(f"ADO-{2000 + i}", "Task") for i in range(120)]
    for i in range(200):  # 120 Stories (60% of 200 Improvements) + 80 Bugs (40% of 200 Bugs)
        key = f"ADO-{3000 + i}"
        target = f"KAFKA-{i + 1}"
        prose_only = i < 70  # 35% of the 200 that point at a real key
        ado.append(
            ado_item(
                key,
                "User Story" if i < 120 else "Bug",
                links=[] if prose_only else [{"type": "related", "target": target}],
                reporter=f"p{i % 10}",
            )
        )
        if prose_only:
            text_only.append({"from_key": key, "to_key": target})
    # 15% of the 200 linked ADO items still say Active while Jira says Resolved
    stale = [
        {
            "ado_key": f"ADO-{3000 + i * 6}",
            "jira_key": f"KAFKA-{i * 6 + 1}",
            "ado_status": "Active",
            "jira_status": "Resolved",
        }
        for i in range(30)
    ]

    truth = {
        "identity_map": persons,
        "text_only_links": text_only,
        "stale_states": stale,
        "renames": [
            {
                "test_key": f"XT-{10000 + n}",
                "jira_key": plan[n - 1],
                "test_phrase": "GroupCoordinator",
                "jira_phrase": "group coordinator",
            }
            for n in range(1, 98)  # 30% of 324
        ],
        "duplicate_tests": [
            {"a": f"XT-{10000 + n}", "b": f"XT-{10100 + n}"}
            for n in range(1, 17)  # 5%
        ],
    }
    layer = [*synthetic, *ado]
    # 100% of the people the layer uses are new identities of real people (spec, noise table)
    for n, record in enumerate(layer):
        record["reporter"] = f"p{n % 10}"
    return real, layer, truth, persons_by_id


def test_a_layer_built_to_the_contract_passes_every_ratio():
    real, synthetic, truth, persons_by_id = spec_shaped_layer()

    ratios = compute(
        real=[WorkItem.model_validate(r) for r in real],
        synthetic=[WorkItem.model_validate(s) for s in synthetic],
        truth=Truth.model_validate(truth),
        persons_by_id=persons_by_id,
    )
    result = summarize(ratios)

    assert result["failed"] == []
    assert result["checked"] == result["passed"]


def test_the_summary_names_exactly_the_ratio_that_missed():
    """Drop the 120 ADO Tasks: 272 items left, so only the >=300 floor should complain."""
    real, synthetic, truth, persons_by_id = spec_shaped_layer()
    synthetic = [s for s in synthetic if s["type"] != "Task"]

    result = summarize(
        compute(
            real=[WorkItem.model_validate(r) for r in real],
            synthetic=[WorkItem.model_validate(s) for s in synthetic],
            truth=Truth.model_validate(truth),
            persons_by_id=persons_by_id,
        )
    )

    assert result["failed"] == ["ado_items_total"]
