"""Measure the merged synthetic layer against `synthetic_spec.md`.

Every number here is *reported*, never enforced. A merge that refused a layer for being
six points off a 35% target would throw away hours of generation to protect a ratio the
planner may well accept — so `brain synth merge` prints PASS/FAIL and writes on.

Tolerance, stated once because "±5%" is ambiguous:

* a target expressed as a **percentage** (35% of Stories) is met within ±5 *percentage
  points* — 30–40%. Reading it as ±5% relative would mean 33.25–36.75%, which no
  hand-planted noise distribution hits.
* a target expressed as a **count or a mean** (one TestPlan per version; ~1.8 Tests per
  covered story) is met within ±5% *relative*, with a floor of 1 for counts so small
  expected numbers are not impossible by arithmetic.

What cannot be measured from the records is said so in `note` rather than guessed at.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from brain.canon.models import WorkItem
from brain.synth.models import Truth

PCT_TOLERANCE = 5.0  #: percentage points
REL_TOLERANCE = 0.05  #: relative, for counts and means

XRAY_TEST = "Test"
ADO_STORY = "User Story"

#: `"XT-12: FAIL (3 rebalances observed)"` — the run line the spec puts on an execution.
RUN_LINE = re.compile(r"\b(XT-\d+)\s*:\s*(PASS|FAIL)\b", re.IGNORECASE)

#: The three display forms the spec asks for, in the order it lists them.
FORM_TARGETS = {"last_first": 30.0, "username": 30.0, "initial": 40.0}


@dataclass
class Ratio:
    name: str
    unit: str  #: "pct" | "count" | "mean"
    actual: float
    target: float | None
    ok: bool | None
    numerator: float | None = None
    denominator: float | None = None
    tolerance: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(numerator: float, denominator: float) -> float:
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def _ratio_pct(name: str, num: float, den: float, target: float, note: str = "") -> Ratio:
    actual = _pct(num, den)
    return Ratio(
        name=name,
        unit="pct",
        actual=actual,
        target=target,
        ok=abs(actual - target) <= PCT_TOLERANCE if den else False,
        numerator=num,
        denominator=den,
        tolerance=PCT_TOLERANCE,
        note=note,
    )


def _ratio_count(name: str, actual: float, expected: float, note: str = "") -> Ratio:
    slack = max(1.0, expected * REL_TOLERANCE)
    return Ratio(
        name=name,
        unit="count",
        actual=actual,
        target=expected,
        ok=abs(actual - expected) <= slack,
        tolerance=round(slack, 2),
        note=note,
    )


def _ratio_mean(name: str, actual: float, target: float, note: str = "") -> Ratio:
    slack = target * REL_TOLERANCE
    return Ratio(
        name=name,
        unit="mean",
        actual=round(actual, 2),
        target=target,
        ok=abs(actual - target) <= slack,
        tolerance=round(slack, 3),
        note=note,
    )


@dataclass
class Layer:
    """The synthetic records split the way every ratio wants to read them."""

    tests: list[WorkItem] = field(default_factory=list)
    plans: list[WorkItem] = field(default_factory=list)
    sets: list[WorkItem] = field(default_factory=list)
    executions: list[WorkItem] = field(default_factory=list)
    epics: list[WorkItem] = field(default_factory=list)
    features: list[WorkItem] = field(default_factory=list)
    stories: list[WorkItem] = field(default_factory=list)
    tasks: list[WorkItem] = field(default_factory=list)
    bugs: list[WorkItem] = field(default_factory=list)

    @property
    def ado(self) -> list[WorkItem]:
        return [*self.epics, *self.features, *self.stories, *self.tasks, *self.bugs]


def split_layer(synthetic: Iterable[WorkItem]) -> Layer:
    layer = Layer()
    buckets = {
        ("xray", "Test"): layer.tests,
        ("xray", "TestPlan"): layer.plans,
        ("xray", "TestSet"): layer.sets,
        ("xray", "TestExecution"): layer.executions,
        ("ado", "Epic"): layer.epics,
        ("ado", "Feature"): layer.features,
        ("ado", "User Story"): layer.stories,
        ("ado", "Task"): layer.tasks,
        ("ado", "Bug"): layer.bugs,
    }
    for item in synthetic:
        bucket = buckets.get((item.source, item.type))
        if bucket is not None:
            bucket.append(item)
    return layer


def _formal_targets(item: WorkItem, link_type: str) -> set[str]:
    return {link.target for link in item.links if link.type.lower() == link_type}


def classify_display(display: str | None) -> str:
    """`"Rao, Jun"` → last_first, `"jrao"` → username, `"J. Rao"` → initial."""
    text = (display or "").strip()
    if not text:
        return "other"
    if re.match(r"^[^,]+,\s+\S", text):
        return "last_first"
    if re.match(r"^[A-Z]\.\s*\S", text):
        return "initial"
    if " " not in text and text == text.lower():
        return "username"
    return "other"


def compute(
    *,
    real: Sequence[WorkItem],
    synthetic: Sequence[WorkItem],
    truth: Truth,
    persons_by_id: dict[str, list[str]] | None = None,
) -> list[Ratio]:
    """Every ratio in `synthetic_spec.md` that the merged records can answer.

    `real` is the eligible slice `brain synth build` selected — the same denominator the
    coverage target means. `persons_by_id` maps a synthetic Person id to its display forms.
    """
    layer = split_layer(synthetic)
    real_keys = {w.key for w in real}
    by_type: Counter = Counter(w.type for w in real)

    text_only_from: dict[str, set[str]] = defaultdict(set)
    for link in truth.text_only_links:
        text_only_from[link.from_key].add(link.to_key)

    # ---------------------------------------------------------------- Xray coverage
    covered: dict[str, set[str]] = defaultdict(set)
    for test in layer.tests:
        targets = _formal_targets(test, "tests") | text_only_from.get(test.key, set())
        for target in targets & real_keys:
            covered[target].add(test.key)

    tests_per_covered = [len(v) for v in covered.values()]
    out: list[Ratio] = [
        _ratio_pct(
            "coverage_of_real_stories_and_bugs",
            len(covered),
            len(real),
            45.0,
            "a real item is covered by a formal `tests` link or a truth-recorded text-only one",
        ),
        _ratio_mean(
            "tests_per_covered_item",
            sum(tests_per_covered) / len(tests_per_covered) if tests_per_covered else 0.0,
            1.8,
        ),
        Ratio(
            name="covered_items_outside_1_to_3_tests",
            unit="count",
            actual=sum(1 for n in tests_per_covered if not 1 <= n <= 3),
            target=0,
            ok=all(1 <= n <= 3 for n in tests_per_covered),
            denominator=len(tests_per_covered),
        ),
    ]

    # ---------------------------------------------------------------- plans and runs
    versions_with_five = {
        v
        for v, n in Counter(
            v for item in real if item.key in covered for v in item.fix_versions
        ).items()
        if n >= 5
    }
    out.append(
        _ratio_count(
            "testplans",
            len(layer.plans),
            len(versions_with_five),
            "one per real fix version with >=5 covered issues",
        )
    )

    fails = defects = passes = 0
    for execution in layer.executions:
        runs = [m.group(2).upper() for c in execution.comments for m in RUN_LINE.finditer(c.body)]
        run_fails = sum(1 for r in runs if r == "FAIL")
        passes += sum(1 for r in runs if r == "PASS")
        fails += run_fails
        defects += min(run_fails, len(_formal_targets(execution, "defect") & real_keys))
    out.append(
        _ratio_pct(
            "fail_runs_naming_a_real_bug",
            defects,
            fails,
            70.0,
            "per execution, capped at its FAIL count: a run line carries no defect link of its own",
        )
    )
    out.append(
        Ratio(
            name="runs_recorded",
            unit="count",
            actual=passes + fails,
            target=None,
            ok=None,
            numerator=passes,
            denominator=fails,
            note="numerator=PASS, denominator=FAIL; informational",
        )
    )
    out.append(
        Ratio(
            name="executions_per_plan",
            unit="mean",
            actual=round(len(layer.executions) / len(layer.plans), 2) if layer.plans else 0.0,
            target=None,
            ok=None,
            note="spec asks for one per (plan, month with activity) — the month set is not "
            "recoverable from the records, so this is informational",
        )
    )

    # ---------------------------------------------------------------- ADO shape
    kips_referenced = {r.key for item in real for r in item.refs if r.kind == "kip"}
    out.append(
        _ratio_count(
            "ado_epics",
            len(layer.epics),
            len(kips_referenced),
            "one per KIP referenced by the slice",
        )
    )
    out.append(
        _ratio_pct(
            "ado_user_stories_per_improvement_or_feature",
            len(layer.stories),
            by_type["Improvement"] + by_type["New Feature"],
            60.0,
        )
    )
    out.append(_ratio_pct("ado_bugs_per_real_bug", len(layer.bugs), by_type["Bug"], 40.0))
    out.append(
        Ratio(
            name="ado_tasks_per_story",
            unit="mean",
            actual=round(len(layer.tasks) / len(layer.stories), 2) if layer.stories else 0.0,
            target=None,
            ok=(len(layer.tasks) / len(layer.stories) <= 2.0) if layer.stories else None,
            note="spec says 0-2 per Story; the check is the ceiling, not a target",
        )
    )
    out.append(
        Ratio(
            name="ado_items_total",
            unit="count",
            actual=len(layer.ado),
            target=300,
            ok=len(layer.ado) >= 300,
            note="acceptance criterion: >=300 ADO work items",
        )
    )
    out.append(
        Ratio(
            name="ado_features",
            unit="count",
            actual=len(layer.features),
            target=None,
            ok=None,
            note="one per group of 3-8 real issues sharing component + fix version; the "
            "grouping is the agent's, so only the count is checkable here",
        )
    )

    # ---------------------------------------------------------------- planted noise
    story_and_bug = {*(w.key for w in layer.stories), *(w.key for w in layer.bugs)}
    story_links = {
        w.key: _formal_targets(w, "related") & real_keys for w in (*layer.stories, *layer.bugs)
    }
    text_only_stories = sum(
        1 for key in story_and_bug if text_only_from.get(key, set()) & real_keys
    )
    formal_stories = sum(1 for key, targets in story_links.items() if targets)
    out.append(
        _ratio_pct(
            "ado_links_that_are_text_only",
            text_only_stories,
            text_only_stories + formal_stories,
            35.0,
            "denominator = ADO Stories/Bugs that point at a real key at all",
        )
    )

    text_only_tests = sum(1 for t in layer.tests if text_only_from.get(t.key, set()) & real_keys)
    formal_tests = sum(1 for t in layer.tests if _formal_targets(t, "tests") & real_keys)
    out.append(
        _ratio_pct(
            "test_links_that_are_text_only",
            text_only_tests,
            text_only_tests + formal_tests,
            20.0,
        )
    )

    linked_ado = len(
        {key for key in story_and_bug if story_links.get(key) or text_only_from.get(key)}
    )
    out.append(
        _ratio_pct("stale_states", len(truth.stale_states), linked_ado, 15.0),
    )
    out.append(_ratio_pct("renamed_tests", len(truth.renames), len(layer.tests), 30.0))
    out.append(
        _ratio_pct(
            "duplicate_tests",
            len(truth.duplicate_tests),
            len(layer.tests),
            5.0,
            "one truth pair per duplicated Test",
        )
    )

    # ---------------------------------------------------------------- identities
    used = {w.reporter for w in synthetic if w.reporter} | {
        w.assignee for w in synthetic if w.assignee
    }
    mapped_keys = {ident.split(":", 1)[1] for ident in truth.identity_map}
    out.append(
        _ratio_pct(
            "used_identities_that_are_new_and_mapped",
            len(used & mapped_keys),
            len(used),
            100.0,
            "every person the layer uses must be a new identity of a real person, in identity_map",
        )
    )
    forms: Counter = Counter()
    for person_id in truth.identity_map:
        for display in (persons_by_id or {}).get(person_id, []):
            forms[classify_display(display)] += 1
    total_forms = sum(forms.values())
    for form, target in FORM_TARGETS.items():
        out.append(
            _ratio_pct(
                f"identity_form_{form}", forms[form], total_forms, target, "display form mix"
            )
        )
    if forms.get("other"):
        out.append(
            Ratio(
                name="identity_form_unclassified",
                unit="count",
                actual=forms["other"],
                target=0,
                ok=False,
                note="display forms matching none of last_first / username / initial",
            )
        )
    return out


def summarize(ratios: Sequence[Ratio]) -> dict[str, Any]:
    checked = [r for r in ratios if r.ok is not None]
    failed = [r.name for r in checked if not r.ok]
    return {
        "checked": len(checked),
        "passed": len(checked) - len(failed),
        "failed": failed,
        "tolerance": {
            "pct": f"+/-{PCT_TOLERANCE} percentage points",
            "count_and_mean": f"+/-{REL_TOLERANCE:.0%} relative (counts floor at 1)",
        },
        "ratios": [r.as_dict() for r in ratios],
    }
