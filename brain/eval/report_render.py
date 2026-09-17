"""`docs/report/eval-report.md` — the Hebrew report, rendered from the dict and nothing else.

Same contract as the Plan 1 census (`brain/index/render.py`) and the Plan 2 gate page
(`brain/eval/render.py`): every value printed here is read out of the dict `report_build`
assembled, so rerunning a step updates the document instead of leaving it quietly wrong. The
literals in this file are Hebrew column headings and explanations of what a column means —
keys, strategy names and metric names stay English because they are identifiers a reader has
to type (conventions rule 6).

Two things this renderer does that the earlier two do not:

* **A section with no input is a section, not a hole.** `_pending` prints `טרם נמדד` and the
  command that produces the missing file. A reader who opens the report between two steps
  learns what is missing and how to get it, which is the whole reason the report generator
  was built before the judge results landed.
* **Two planner blocks, not one.** Spec §5.6 asks for two closing sections written by a
  person after reading the numbers. Each is carried across regenerations by finding its
  heading and taking the `planner:start`/`planner:end` pair that follows it, so adding a
  generated section between them never loses the prose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DASH = "—"
PLANNER_START = "<!-- planner:start -->"
PLANNER_END = "<!-- planner:end -->"
#: The two sections spec §5.6 makes mandatory, in the order they close the document. Fixed
#: strings: the roadmap gate for Plan 3 names the first one by its exact heading.
PLANNER_HEADINGS: tuple[tuple[str, str], ...] = (
    (
        "מתי לא הייתי משתמש בגרף כאן",
        "איפה הגרף לא החזיר את ההשקעה — לפי המספרים שלמעלה, לא לפי תחושה.",
    ),
    (
        "מה הייתי משנה",
        "מה היה נעשה אחרת בסיבוב הבא — סכימה, אחזור, סט השאלות או המדידה עצמה.",
    ),
)
EMPTY_PLANNER = (
    "*(טרם נכתב. המתכנן כותב כאן אחרי קריאת המספרים; הטקסט בין שני הסימנים נשמר "
    "בכל יצירה מחדש של הדף.)*"
)

TYPE_HE: dict[str, str] = {
    "traceability": "עקיבוּת",
    "impact": "השפעה",
    "rationale": "נימוק",
    "global": "גלובלי",
    "temporal": "זמני",
}
METRIC_HE: dict[str, str] = {
    "faithfulness": "נאמנות להקשר",
    "correctness": "נכונוּת מול gold",
    "citation_validity": "תקינות ציטוט",
    "relevancy": "רלוונטיות",
}
LANG_HE: dict[str, str] = {"en": "אנגלית", "he": "עברית"}


# ------------------------------------------------------------------------------- helpers


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if isinstance(value, float):
        return f"{value:.1f}" if value.is_integer() else f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value) if value else DASH
    if isinstance(value, Mapping):
        return ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) if value else DASH
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    if not rows:
        return ["*(אין נתונים)*", ""]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out.extend("| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows)
    out.append("")
    return out


def _h(level: int, text: str) -> list[str]:
    return [f"{'#' * level} {text}", ""]


def _p(text: str) -> list[str]:
    return [text, ""]


def _short(sha: Any) -> str:
    return str(sha)[:8] if sha else DASH


def _stamp(state: Mapping[str, Any]) -> list[str]:
    """One line under a heading: where the numbers came from, when, and whether at HEAD."""
    bits = [f"מקור: `{state.get('path')}`"]
    if state.get("generated_at"):
        bits.append(f"נמדד ב-{state['generated_at']}")
    if state.get("sha"):
        bits.append(f"sha `{_short(state['sha'])}`")
    line = " · ".join(bits) + "."
    if state.get("stale") is True:
        line += f" **STALE** — {state.get('note') or 'נמדד לפני ה-HEAD הנוכחי'}."
    elif state.get("stale") is None and state.get("note"):
        line += f" ⚠️ {state['note']}."
    elif state.get("stale_sections"):
        line += f" סעיפים ישנים בקובץ: {', '.join(state['stale_sections'])}."
    return _p(line)


def _pending(state: Mapping[str, Any], *, what: str = "") -> list[str]:
    """What a section looks like before its input exists — including how to produce it.

    A file that exists without the section is not the same as a file that does not exist,
    and saying so is the difference between "run the step" and "the step ran and wrote
    nothing here yet".
    """
    where = (
        f"הקובץ `{state.get('path')}` קיים, אבל הסעיף הזה עדיין לא נכתב לתוכו"
        if state.get("file_present")
        else f"הקובץ `{state.get('path')}` עדיין לא נכתב"
    )
    out = _p(f"**טרם נמדד.** {what} {where}; הפקודה שמייצרת אותו: `{state.get('command')}`.")
    if state.get("note"):
        out += _p(f"> {state['note']}")
    return out


def _section(title: str, state: Mapping[str, Any], body, *, what: str = "", level: int = 2):
    """A heading, its stamp or its pending note, and the body only when there is one."""
    out = _h(level, title)
    if not state.get("present"):
        return out + _pending(state, what=what)
    out += _stamp(state)
    return out + body()


def _type_label(name: str) -> str:
    return f"{TYPE_HE.get(name, name)} (`{name}`)"


# ----------------------------------------------------------------------------- the header


def _header(doc: Mapping[str, Any]) -> list[str]:
    out = _h(1, "הערכה — דוח Plan 3")
    out += _p(
        f'נוצר אוטומטית ע"י `{doc.get("command")}` ב-{_fmt(doc.get("generated_at"))} '
        f"(HEAD `{_short(doc.get('sha'))}`) מתוך דוחות השלבים ב-`{doc.get('reports_dir')}`. "
        "**המספרים נלקחים מדוחות השלבים** ואין כאן מספר שהוקלד לתוך הדף; "
        "**הערות השלבים הן פרוזה של המהנדס/ת של השלב** (עמודות `הערות`/`note` שנגררות "
        'מה-JSON), ושני הסעיפים האחרונים נכתבים ביד ע"י המתכנן ונשמרים בכל יצירה מחדש.'
    )
    out += _p(
        "כל שורה בטבלה הבאה היא קלט של הדוח: מתי נמדד, באיזה commit, והאם ה-commit הזה "
        "הוא ה-HEAD. `STALE` = המספרים קיימים אבל נמדדו על קוד אחר; `חסר` = הסעיף שמתבסס "
        "עליו יופיע כ-**טרם נמדד** עם הפקודה שמייצרת אותו."
    )
    rows = []
    for row in doc["inputs"]:
        if not row["present"]:
            status = "חסר"
        elif row["stale"] is True:
            status = "STALE"
        elif row["stale"] is None:
            status = "בלי sha"
        else:
            status = "עדכני"
        rows.append(
            [
                f"`{row['path']}`",
                row["brings"],
                status,
                row.get("generated_at"),
                _short(row.get("sha")),
                f"`{row['command']}`",
                row.get("note"),
            ]
        )
    out += _table(["קובץ", "מה מביא", "מצב", "נמדד ב", "sha", "הפקודה שכותבת", "הערה"], rows)
    return out


def _questions(doc: Mapping[str, Any]) -> list[str]:
    part = doc["questions"]

    def body() -> list[str]:
        out = _p(
            f"הסט: `{part.get('path')}` (sha256 `{str(part.get('sha256') or '')[:16]}`), "
            f"{_fmt(part.get('questions'))} שאלות מתוך יעד {_fmt(part.get('target'))}. "
            f"רצפת העברית: {_fmt(part.get('hebrew_floor'))}."
        )
        out += _p(f"`{part.get('gold_source_line')}`.")
        if part.get("gold_source_gap"):
            out += _p(f"> {part['gold_source_gap']}")
        out += _table(
            ["חתך", "פירוט"],
            [
                ["לפי סוג", part.get("by_type")],
                ["לפי שפה", part.get("by_lang")],
                ["לפי מקור ה-gold", part.get("by_gold_source")],
                ["לפי מוצא", part.get("by_origin")],
            ],
        )
        checks = part.get("checks") or []
        if checks:
            out += _h(3, "בדיקות הקבלה של הסט")
            out += _table(
                ["בדיקה", "תוצאה", "פירוט"],
                [
                    [c.get("name"), "עבר" if c.get("ok") else "נכשל", c.get("detail")]
                    for c in checks
                ],
            )
        return out

    return _section("סט השאלות", part["state"], body, what="הסט לא נבנה.")


# ------------------------------------------------------------------------- layers 0 and 1


def _harvest(part: Mapping[str, Any]) -> list[str]:
    def body() -> list[str]:
        out = _p(
            "שכבה 0 של §5.2: מה בכלל נמשך מהמקורות. הספירות הן מה-checkpoint של כל קונקטור, "
            "כך שריצה חלקית נראית כחלקית ולא כקורפוס קטן."
        )
        out += _table(
            ["מקור", "רשומות", "עמודים", "הושלם", "שגיאות"],
            [
                [r["source"], r.get("records"), r.get("pages"), r.get("done"), r.get("errors")]
                for r in part.get("sources") or []
            ],
        )
        density = part.get("link_density") or {}
        if density:
            out += _h(3, "צפיפות קישורים (Jira)")
            out += _p(
                "מה שהצדיק את הקורפוס הזה: כמה מה-issues נושאים קישור פורמלי, אזכור KIP "
                "והיסטוריה — בלי אלה אין מה לחלץ ואין מה לשאול."
            )
            keys = sorted({k for v in density.values() if isinstance(v, Mapping) for k in v})
            out += _table(
                ["רכיב", *keys],
                [
                    [name, *[(value or {}).get(k) for k in keys]]
                    for name, value in density.items()
                    if isinstance(value, Mapping)
                ],
            )
        return out

    return _section("שכבה 0 — harvest", part["state"], body, what="הקורפוס לא נמשך.")


def _census(part: Mapping[str, Any]) -> list[str]:
    def body() -> list[str]:
        corpus = part.get("corpus") or {}
        gate = part.get("gate") or {}
        out = _p(
            f"הגרף שעליו נמדד הכול: **{_fmt(part.get('nodes_total'))}** צמתים ו-"
            f"**{_fmt(part.get('edges_total'))}** קשתות "
            f"({_fmt(part.get('edges_deterministic'))} דטרמיניסטיות, "
            f"{_fmt(part.get('edges_llm'))} מ-LLM). "
            f"שער Plan 1: {'עבר' if gate.get('ok') else 'לא עבר'} — "
            f"{_fmt(gate.get('passed'))}/{_fmt(gate.get('total'))}"
            + (f", נכשל: {_fmt(gate.get('failed_names'))}" if gate.get("failed_names") else "")
            + f". המפקד המלא: `{part.get('document')}`."
        )
        out += _table(
            ["מדד", "ערך"],
            [
                ["issues אמיתיים", corpus.get("real_issues")],
                ["work items סינתטיים", corpus.get("synthetic_workitems")],
                ["מסמכי KIP", corpus.get("kip_documents")],
                ["KIPs מוזכרים", corpus.get("kips_referenced")],
                ["commits", corpus.get("commits")],
                ["chunks", (part.get("chunks") or {}).get("chunks")],
                ["מהם חיים", (part.get("chunks") or {}).get("live")],
                ["מוטמעים", (part.get("chunks") or {}).get("embedded")],
                ["צמתים יתומים", (part.get("orphans") or {}).get("total")],
            ],
        )
        return out

    return _section("שכבה 1 — מפקד הגרף", part["state"], body, what="הגרף לא נמדד.")


def _resolution(part: Mapping[str, Any]) -> list[str]:
    def body() -> list[str]:
        out = _p(
            f"P/R/F1 מול זוגות הזהב בלבד (יעד {_fmt(part.get('target'))}); מיזוגים שהזהב "
            "לא אומר עליהם דבר נספרים כ-`ungraded` ולא נכנסים לאף צד."
        )
        out += _table(
            [
                "סוג",
                "זוגות זהב",
                "P",
                "R",
                "F1",
                "TP",
                "FP",
                "FN",
                "לפני",
                "אחרי",
                "ungraded (index.json)",
                "ungraded (resolve.json)",
            ],
            [
                [
                    r["kind"],
                    r.get("gold_pairs"),
                    r.get("precision"),
                    r.get("recall"),
                    r.get("f1"),
                    r.get("tp"),
                    r.get("fp"),
                    r.get("fn"),
                    r.get("nodes_before"),
                    r.get("nodes_after"),
                    r.get("ungraded_merges"),
                    r.get("ungraded_merges_resolve"),
                ]
                for r in part.get("rows") or []
            ],
        )
        out += _p(
            "שתי עמודות ה-ungraded אינן שגיאה: המפקד סופר את המיזוגים דרך tier אחד פחות "
            "מ-`brain resolve eval`, ולכן הן נבדלות ב-1 עבור `person`. שתיהן מודפסות עם "
            "הקובץ שלהן כדי שאיש לא יצטט את אחת מהן כ״המספר״."
        )
        if part.get("note"):
            out += _p(f"> {part['note']}")
        return out

    return _section(
        "שכבה 1 — איחוד ישויות", part["state"], body, what="`brain resolve eval` לא רץ.", level=3
    )


def _provenance(part: Mapping[str, Any]) -> list[str]:
    def body() -> list[str]:
        out = _p(
            f"כלל הבעלות: {part.get('rule')} — "
            f"{_fmt(part.get('edges_total'))} קשתות מ-LLM, "
            f"{_fmt(part.get('edges_pct'))}% מהן עם provenance."
        )
        out += _table(
            ["label", "צמתים", "מהם מ-LLM", "עם provenance", "חסרים", "%"],
            [
                [
                    r["label"],
                    r.get("total"),
                    r.get("llm_derived"),
                    r.get("with_provenance"),
                    r.get("missing"),
                    r.get("pct"),
                ]
                for r in part.get("nodes") or []
            ],
        )
        out += _table(
            ["סוג קשת", "קשתות", "עם provenance", "חסרים", "%"],
            [
                [
                    r["type"],
                    r.get("total"),
                    r.get("with_provenance"),
                    r.get("missing"),
                    r.get("pct"),
                ]
                for r in part.get("edges") or []
            ],
        )
        return out

    return _section("שכבה 1 — כיסוי provenance", part["state"], body, level=3)


def _communities(part: Mapping[str, Any]) -> list[str]:
    def body() -> list[str]:
        out = _p(
            f"**{_fmt(part.get('communities'))}** קהילות, מהן {_fmt(part.get('summarised'))} "
            f"מסוכמות ({_fmt(part.get('pct_summarised'))}%). "
            f"{_fmt(part.get('pct_members_in_a_summarised_community'))}% מהחברים נמצאים "
            f"בקהילה מסוכמת — זה הכיסוי ש-S5 (חיפוש גלובלי) יכול לראות בכלל."
        )
        levels = part.get("levels") or []
        if levels:
            out += _table(
                ["רמה", "קהילות", "גודל p50", "גודל p95", "חברים", "מסוכמות"],
                [
                    [
                        r.get("level"),
                        r.get("communities"),
                        r.get("size_p50"),
                        r.get("size_p95"),
                        r.get("members"),
                        r.get("summarised"),
                    ]
                    for r in levels
                ],
            )
        return out

    return _section("שכבה 1 — כיסוי קהילות", part["state"], body, what="הקהילות לא נבנו.", level=3)


# ------------------------------------------------------------------------------ layer 2


def _matrix_table(layer2: Mapping[str, Any]) -> list[str]:
    strategies = layer2.get("strategies") or []
    types = layer2.get("types") or []
    matrix = layer2.get("matrix") or {}
    rows = []
    for strategy in strategies:
        per_type = matrix.get(strategy) or {}
        for qtype in types:
            cell = per_type.get(qtype) or {}
            hit = (cell.get("hit_at") or {}).get("10")
            rows.append(
                [
                    strategy,
                    _type_label(qtype),
                    f"{_fmt(cell.get('ok'))}/{_fmt(cell.get('n'))}",
                    cell.get("recall"),
                    cell.get("recall_strict"),
                    cell.get("precision"),
                    hit,
                    cell.get("latency_p50_ms"),
                    cell.get("context_tokens_p50"),
                ]
            )
    return _table(
        [
            "אסטרטגיה",
            "סוג שאלה",
            'רץ/סה"כ',
            "recall",
            "recall_strict",
            "precision",
            "hit@10",
            "latency p50 (ms)",
            "tokens p50",
        ],
        rows,
    )


#: How many `n/a` reasons fit in a table cell before the cell stops being readable. S5 spells
#: the router rule into its reason, so one reason arrives as nine; the rest are counted in
#: full and named in `eval_retrieval.json`, which is where a reader who wants all nine goes.
NA_REASONS_SHOWN = 3


def _na_cell(reasons: Sequence[str]) -> str:
    if len(reasons) <= NA_REASONS_SHOWN:
        return _fmt(list(reasons))
    rest = len(reasons) - NA_REASONS_SHOWN
    return _fmt(list(reasons[:NA_REASONS_SHOWN])) + f" · ועוד {rest} סיבות ב-JSON"


#: The column, and the one line that has to sit under the table whenever it is empty.
#: `agent_time_ms` can only ever be filled by mode B — mode A answers from a packed context
#: and has no agent to time — and mode B was run per question without a stopwatch. An empty
#: column with an unqualified header reads as "the agent took no time", which is the one
#: thing it does not mean.
AGENT_TIME_HEADER = "זמן סוכן (מצב B בלבד)"
AGENT_TIME_NOTE = "זמן סוכן — לא נמדד ב-POC; ב-Plan 3 נמדד רק latency של כלים."


def _cost_table(layer2: Mapping[str, Any]) -> list[str]:
    rows = list(layer2.get("cost") or [])
    out = _table(
        [
            "אסטרטגיה",
            "ריצות",
            "n/a",
            "latency p50",
            "latency p95",
            "tokens p50",
            'tokens סה"כ',
            "tool calls",
            "Cypher",
            AGENT_TIME_HEADER,
            "סיבות ל-n/a",
        ],
        [
            [
                r["strategy"],
                r.get("runs"),
                r.get("na"),
                r.get("latency_p50_ms"),
                r.get("latency_p95_ms"),
                r.get("context_tokens_p50"),
                r.get("context_tokens_total"),
                r.get("tool_calls"),
                r.get("cypher_total"),
                r.get("agent_time_ms"),
                _na_cell(r.get("na_reasons") or []),
            ]
            for r in rows
        ],
    )
    if rows and all(r.get("agent_time_ms") in (None, "") for r in rows):
        out += _p(f"> {AGENT_TIME_NOTE}")
    return out


def _shared_code_path(questions: Mapping[str, Any]) -> list[str]:
    """The footnote under the matrix: which rows are not really measuring retrieval.

    Four `temporal` questions have a `gold_query` that *is* the call the strategy under test
    makes, so a `recall` of 1.0 on them says the function is deterministic. It stays in the
    table — dropping it would change the denominator quietly — and is named instead.
    """
    shared = list(questions.get("gold_shares_code_path") or [])
    if not shared:
        return []
    types = ", ".join(questions.get("gold_shares_code_path_types") or []) or "—"
    return _p(
        f"> **gold shares the strategy's code path** — {len(shared)} שאלות "
        f"({', '.join(f'`{q}`' for q in shared)}, סוג: {types}): ה-gold שלהן הוא הפלט של "
        "אותה קריאת `brain.retrieve.temporal.*` שהאסטרטגיה מריצה. השורות שלהן בטבלה "
        "שלמעלה מודדות דטרמיניזם של הקריאה, לא אחזור."
    )


def _cross_lingual(layer2: Mapping[str, Any]) -> list[str]:
    rows = []
    for pair in layer2.get("cross_lingual") or []:
        for strategy, part in sorted((pair.get("strategies") or {}).items()):
            en, he = part.get("en") or {}, part.get("he") or {}
            rows.append(
                [
                    pair.get("pair"),
                    strategy,
                    f"{en.get('qid')} / {he.get('qid')}",
                    f"{_fmt(en.get('recall'))} / {_fmt(he.get('recall'))}",
                    f"{_fmt(en.get('items'))} / {_fmt(he.get('items'))}",
                    part.get("key_jaccard"),
                    len(part.get("shared_keys") or []),
                ]
            )
    out = _h(3, "חוצה-שפות (EN מול HE)")
    out += _p(
        "אותה שאלה בשתי שפות, אותה אסטרטגיה. `key_jaccard` = חפיפת המפתחות שהוחזרו; "
        "ערך גבוה אומר שההטמעה הרב-לשונית מחזירה את אותו מקום בגרף, ולא רק ציון דומה."
    )
    return out + _table(
        [
            "זוג",
            "אסטרטגיה",
            "qid (EN/HE)",
            "recall EN/HE",
            "items EN/HE",
            "Jaccard",
            "מפתחות משותפים",
        ],
        rows,
    )


def _infra(part: Mapping[str, Any]) -> list[str]:
    if not part.get("present"):
        return []
    rerank, guard = part.get("rerank") or {}, part.get("guard") or {}
    out = _h(3, "עלות התשתית: reranker ו-guard")
    out += _p(
        "שני מספרים שלא תלויים בשאלה: כמה עולה ה-reranker שה-baseline נושא, וכמה הדוק "
        "ה-guard שמריץ את S4."
    )
    out += _table(
        ["מדד", "ערך"],
        [
            ["מודל rerank", rerank.get("model")],
            ["זמין", rerank.get("available")],
            ["top-1 השתנה", f"{_fmt(rerank.get('top1_changed'))}/{_fmt(rerank.get('questions'))}"],
            ["top-1 עבר למסמך אחר", rerank.get("top1_source_changed")],
            ["p50 בלי rerank (ms)", rerank.get("p50_without_ms")],
            ["p50 עם rerank (ms)", rerank.get("p50_with_ms")],
            ["תוספת p50 (ms)", rerank.get("cost_p50_ms")],
            ["guard: נחסמו", f"{_fmt(guard.get('refused'))}/{_fmt(guard.get('cases'))}"],
            ["guard: דלפו", guard.get("leaked")],
            [
                "guard: קריאות שעברו",
                f"{_fmt(guard.get('reads_passed'))}/{_fmt(guard.get('reads_cases'))}",
            ],
            ["guard: LIMIT הוזרק", guard.get("limit_injected")],
            ["guard: timeout נאכף (ms)", guard.get("timeout_ms")],
        ],
    )
    if rerank.get("note"):
        out += _p(f"> {rerank['note']}")
    return out


def _layer2(doc: Mapping[str, Any]) -> list[str]:
    part = doc["layer2"]

    def body() -> list[str]:
        coverage = part.get("coverage") or {}
        out = _p(
            f"מצב {_fmt(part.get('mode'))}, k={_fmt(part.get('k'))}, "
            f"תקציב הקשר {_fmt(part.get('budget_tokens'))} tokens לכל אסטרטגיה, "
            f"baseline = `{part.get('baseline')}`. "
            f"{_fmt(part.get('questions_total'))} שאלות; כיסוי "
            f"{_fmt(coverage.get('present'))}/{_fmt(coverage.get('expected'))}. "
            "כל התא נמדד דטרמיניסטית מול `gold_evidence` — אין כאן שופט."
        )
        missing = part.get("missing_strategies") or []
        if missing:
            out += _p(
                f"⚠️ אסטרטגיות שעדיין לא נסרקו: `{'`, `'.join(missing)}` — "
                "`brain eval run --mode fixed --strategies …` משלים אותן."
            )
        out += _p(
            f"`recall` סופר התאמה בכל סוג ({_fmt(part.get('match_kinds'))}); "
            f"`recall_strict` רק ({_fmt(part.get('strict_match_kinds'))})."
        )
        out += _h(3, "מטריצה: אסטרטגיה × סוג שאלה")
        out += _matrix_table(part)
        out += _shared_code_path(doc.get("questions") or {})
        out += _h(3, "עלות לפי אסטרטגיה")
        out += _p(
            "`n/a` = האסטרטגיה לא חלה על השאלה, עם סיבה — תא ריק שאיש לא יכול להסביר "
            "גרוע ממספר נמוך."
        )
        out += _cost_table(part)
        out += _cross_lingual(part)
        out += _infra(part.get("infra") or {})
        for note in part.get("notes") or []:
            out += _p(f"> {note}")
        return out

    return _section(
        "שכבה 2 — אחזור (דטרמיניסטי)",
        part["state"],
        body,
        what="הסריקה של מצב A לא רצה.",
    )


# ------------------------------------------------------------------------------ layer 3


def _judge_matrix(part: Mapping[str, Any]) -> list[str]:
    metrics = list(part.get("metrics") or [])
    rows = []
    for strategy in part.get("strategies") or []:
        per_type = (part.get("matrix") or {}).get(strategy) or {}
        for qtype in part.get("types") or []:
            cell = per_type.get(qtype)
            if not cell:
                continue
            rows.append(
                [
                    strategy,
                    _type_label(qtype),
                    cell.get("n", cell.get("cases")),
                    *[cell.get(m) for m in metrics],
                ]
            )
    return _table(["אסטרטגיה", "סוג שאלה", "n", *[METRIC_HE.get(m, m) for m in metrics]], rows)


def _by_strategy(part: Mapping[str, Any]) -> list[str]:
    """The four means per strategy, with the denominator each rests on.

    Printed because the denominators are not equal — `s1` was judged on 26 cases and `s3` on
    32 — and a column of means with no `n` invites exactly the comparison the drop list
    below it forbids.
    """
    metrics = list(part.get("metrics") or [])
    by_strategy = part.get("by_strategy") or {}
    rows = [
        [name, cell.get("n", cell.get("cases")), *[cell.get(m) for m in metrics]]
        for name in (part.get("strategies") or [])
        if (cell := by_strategy.get(name))
    ]
    out = _h(3, "ממוצעים לפי אסטרטגיה")
    out += _p(
        "`n` = כמה מקרים נשפטו לאסטרטגיה הזו. המכנים אינם שווים, ולכן הפרש בין שני "
        "ממוצעים אינו בהכרח הפרש בין שתי אסטרטגיות."
    )
    out += _table(["אסטרטגיה", "n", *[METRIC_HE.get(m, m) for m in metrics]], rows)
    return out + _dropped(part)


def _dropped(part: Mapping[str, Any]) -> list[str]:
    """Which answered cases never reached a judge, and why — the missing denominator."""
    dropped = part.get("dropped") or {}
    if not dropped.get("count"):
        return []
    out = _p(
        f"**{_fmt(dropped.get('count'))} מקרים שנענו לא הגיעו לשופט** "
        f"({_fmt(dropped.get('by_strategy'))}). זה ההפרש בין המכנים למעלה, והוא אינו "
        "מקרי: זרוע ה-baseline ספגה אותו הכי חזק."
    )
    return out + _table(
        ["מקרה", "אסטרטגיה", "סיבה", "פירוט"],
        [
            [f"`{r.get('case_id')}`", r.get("strategy"), f"`{r.get('reason')}`", r.get("why")]
            for r in dropped.get("rows") or []
        ],
    )


def _citations(part: Mapping[str, Any]) -> list[str]:
    """Citations per strategy, widened beside strict — the two are not the same number."""
    rows = part.get("citations") or []
    if not rows:
        return []
    out = _h(3, "ציטוטים לפי אסטרטגיה (רחב מול strict)")
    out += _p(
        "`valid` מקבל ציטוט שמצביע על ההורה של פריט ההקשר; `valid_strict` דורש את המזהה "
        "שההקשר באמת הכיל. ההפרש הוא הרגל של S4: לצטט את פריט העבודה של שורה במקום "
        "את השורה."
    )
    return out + _table(
        [
            "אסטרטגיה",
            "תשובות",
            "סירובים",
            "ציטוטים",
            "valid",
            "valid strict",
            "% בהקשר",
            "% בהקשר strict",
        ],
        [
            [
                r.get("strategy"),
                r.get("answers"),
                r.get("refused"),
                r.get("citations"),
                r.get("valid"),
                r.get("valid_strict"),
                r.get("in_context_pct"),
                r.get("in_context_strict_pct"),
            ]
            for r in rows
        ],
    )


def _pairwise(part: Mapping[str, Any]) -> list[str]:
    pairwise = part.get("pairwise") or {}
    out = _h(3, f"pairwise מול ה-baseline (`{pairwise.get('baseline') or DASH}`)")
    out += _p(
        "אותה שאלה, שתי תשובות, בסדר אקראי, בלי שם אסטרטגיה. **היחידה היא זוג ולא "
        f"הכרעה:** {_fmt(pairwise.get('verdicts'))} הכרעות על {_fmt(pairwise.get('pairs'))} "
        "זוגות, כי 20% מהזוגות נשפטו פעמיים. זוג ששני השופטים בחרו בו מנצחים שונים נספר "
        'כתיקו ומופיע ב"הכרעות מפוצלות" למטה. `win_rate` סופר תיקו כאי-ניצחון; '
        "`both_wrong` דורש שכל שופט שראה את הזוג יאמר זאת, ומדווח בנפרד כי שתי תשובות "
        "שגויות שמסכימות אינן תיקו."
    )
    out += _table(
        [
            "מתמודדת",
            "n (זוגות)",
            "הכרעות",
            "ניצחונות",
            "הפסדים",
            "תיקו",
            "מפוצלים",
            "שתיהן שגויות",
            "win rate",
            "בלי תיקו",
        ],
        [
            [
                name,
                row.get("n", row.get("cases")),
                row.get("verdicts"),
                row.get("wins"),
                row.get("losses"),
                row.get("ties"),
                row.get("split"),
                row.get("both_wrong"),
                row.get("win_rate"),
                row.get("win_rate_excluding_ties"),
            ]
            for name, row in (pairwise.get("by_challenger") or {}).items()
        ],
    )
    split = pairwise.get("split_verdicts") or []
    if split:
        out += _p("הכרעות מפוצלות — שני שופטים, שני מנצחים שונים, לכן תיקו:")
        out += _table(
            ["זוג", "שאלה", "מתמודדת", "מי ניצח לפי כל שופט", "shards"],
            [
                [
                    f"`{s.get('pair_id')}`",
                    s.get("qid"),
                    s.get("challenger"),
                    s.get("winners"),
                    s.get("shards"),
                ]
                for s in split
            ],
        )
    return out


def _by_lang(part: Mapping[str, Any]) -> list[str]:
    """Cross-lingual, at the answer layer: does a Hebrew question get a worse answer?

    Layer 2 answers that question about retrieval (`key_jaccard` over the same corpus);
    this is the other half — a set that is a third Hebrew is only balanced if the Hebrew
    third is not systematically scored lower.
    """
    metrics = list(part.get("metrics") or [])
    rows = [
        [
            LANG_HE.get(lang, lang),
            cell.get("n", cell.get("cases")),
            *[cell.get(m) for m in metrics],
        ]
        for lang, cell in sorted((part.get("by_lang") or {}).items())
    ]
    out = _h(3, "לפי שפת השאלה")
    return out + _table(["שפה", "n", *[METRIC_HE.get(m, m) for m in metrics]], rows)


def _agreement(part: Mapping[str, Any]) -> list[str]:
    agreement = part.get("agreement") or {}
    out = _h(3, "הסכמה בין השופטים")
    out += _p(
        f"נמדדת רק על המקרים ששני shards שונים ניקדו: "
        f"{_fmt(agreement.get('overlap_cases'))} מקרים חופפים."
    )
    pairwise = agreement.get("pairwise") or {}
    out += _table(
        ["מדד", "הושוו", "זהה", "בהפרש ≤1", "% זהה", "% ≤1", "הפרש ממוצע"],
        [
            [
                METRIC_HE.get(metric, metric),
                row.get("compared"),
                row.get("exact"),
                row.get("within_1"),
                row.get("exact_pct"),
                row.get("within_1_pct"),
                row.get("mean_abs_diff"),
            ]
            for metric, row in (agreement.get("by_metric") or {}).items()
        ]
        + [
            [
                "מנצח pairwise",
                pairwise.get("compared"),
                pairwise.get("exact"),
                DASH,
                pairwise.get("exact_pct"),
                DASH,
                DASH,
            ]
        ],
    )
    return out + _p(
        "השורה האחרונה היא הסולם השני, והיא זו שהטבלה שמעליה נשענת עליה: `win_rate` בנוי "
        'על "מי ניצח", שאין לו "בהפרש ≤1". ההסכמה עליו נמוכה מההסכמה על הרובריקה — '
        "כלומר דירוג של שתי תשובות פחות יציב מניקוד של אחת."
    )


def _crosscheck(part: Mapping[str, Any]) -> list[str]:
    cross = part.get("crosscheck") or {}
    code = part.get("code_check") or {}
    out = _h(3, "שופט מול בדיקת הקוד (ציטוטים)")
    out += _p(
        "בדיקת הקוד בינארית: כל סוגר מרובע מצביע על משהו שהאחזור החזיר ושהגרף מחזיק. "
        "הסתירה נרשמת רק בקצוות — 1 של השופט מתיישב עם שתי ההכרעות. **הגרף זז בין "
        "האחזור לבדיקה** (הפרוסה האינקרמנטלית עשתה re-partition לקהילות), ולכן סתירה "
        "שנובעת ממזהה שהאחזור באמת החזיר ושנמחק אחר כך מופרדת מטעות שופט אמיתית."
    )
    out += _table(
        ["פירוק", "מקרים"],
        [
            ["הושוו", cross.get("compared")],
            ["בהסכמה", cross.get("agreed")],
            ["בסתירה מול הגרף עכשיו", cross.get("disagree_now")],
            ["מתוכן: היו ב-snapshot של האחזור", cross.get("disagree_but_in_snapshot")],
            ["מתוכן: טעות שופט אמיתית", cross.get("real_judge_error")],
            ["אילו", cross.get("real_judge_errors")],
            ["% סתירה", cross.get("disagreement_pct")],
        ],
    )
    out += _table(
        ["מדד", "ערך"],
        [
            ["מקרים שנבדקו בקוד", code.get("cases")],
            ["ציטוטים תקפים (קוד)", code.get("code_valid")],
            ["ציטוטים פסולים (קוד)", code.get("code_invalid")],
            ["מהם תקפים מול ה-snapshot", code.get("code_valid_in_snapshot")],
            ["תשובות בלי ציטוט", code.get("no_citation")],
            ["ציטוט שלא היה בהקשר", code.get("not_in_context")],
            ["ציטוט שאין לו צומת בגרף עכשיו", code.get("not_in_graph")],
            ["ציטוט שגם ב-snapshot לא היה", code.get("not_in_snapshot")],
        ],
    )
    rows = [
        [
            d.get("case_id"),
            d.get("judge"),
            d.get("code"),
            "היה ב-snapshot" if d.get("in_snapshot") else "טעות שופט",
            d.get("why"),
        ]
        for d in (cross.get("disagreements") or [])
    ]
    if rows:
        out += _table(["מקרה", "ציון השופט", "הכרעת הקוד", "סיווג", "למה"], rows)
    return out


def _mode_b(part: Mapping[str, Any]) -> list[str]:
    mode_b = part.get("mode_b") or {}
    out = _h(3, "מצב B (אגנטי) לצד מצב A")
    if not mode_b.get("present"):
        return out + _p(
            f"*טרם נמדד — אין קבצי `<qid>.{mode_b.get('column')}.json` תחת "
            f"`{mode_b.get('dir')}`. `brain eval answers merge` מייצר אותם מתוך תשובות Plan 2.*"
        )
    gate = mode_b.get("gate") or {}
    out += _p(
        f"**{_fmt(mode_b.get('answers'))}** תשובות אגנטיות ב-`{mode_b.get('dir')}`, "
        f"עמודת `{mode_b.get('column')}` בטבלאות שלמעלה. הסוכן במצב B בחר כלים בעצמו ולכן "
        "לא נרשם לו הקשר ארוז — נאמנות (faithfulness) אינה מנוקדת עליהן, והיא מופיעה "
        "כחסרה ולא כאפס."
    )
    if gate.get("present"):
        out += _p("שער הציטוטים הדטרמיניסטי של Plan 2 על אותן תשובות:")
        out += _table(
            ["מדד", "ערך"],
            [
                ["שאלות", gate.get("questions")],
                ["נענו", gate.get("answered")],
                ["ציטוטים שונים", gate.get("citations_found")],
                ["מהם תקפים", gate.get("citations_valid")],
                ["מהם פסולים", gate.get("citations_invalid")],
                ["שיעור תקינות", gate.get("validity_rate")],
                ["תשובות עם ≥1 ציטוט תקף", gate.get("with_at_least_one_valid_citation")],
                ["שער Plan 2", f"{_fmt(gate.get('passed'))}/{_fmt(gate.get('total'))}"],
            ],
        )
    return out


def _layer3(doc: Mapping[str, Any]) -> list[str]:
    part = doc["layer3"]

    def body() -> list[str]:
        answers = part.get("answers") or {}
        judge = part.get("judge_merge") or {}
        judgments = judge.get("judgments") or {}
        dropped = (part.get("dropped") or {}).get("count") or 0
        out = _p(
            f"שיפוט עיוור לפי רובריקה 0–2 על ארבעה מדדים (§5.4). "
            f"{_fmt(judgments.get('total'))} שיפוטים על {_fmt(judgments.get('cases'))} מקרים, "
            f"{_fmt((part.get('pairwise') or {}).get('verdicts'))} הכרעות pairwise על "
            f"{_fmt((part.get('pairwise') or {}).get('pairs'))} זוגות, "
            f"{_fmt(judgments.get('rejected'))} נדחו. "
            f"**המסלול של מצב A:** {_fmt(answers.get('cases'))} מקרים נבנו, "
            f"{_fmt(answers.get('accepted'))} תשובות התקבלו במיזוג, "
            f"{_fmt(dropped)} נשמטו לפני השיפוט; "
            f"{_fmt(answers.get('judged'))} מקרים נשפטו בפועל (מצב A + מצב B יחד, "
            "שכן תשובות מצב B מוזגו בנפרד)."
        )
        out += _h(3, "מטריצה: אסטרטגיה × סוג שאלה (ממוצעי השופטים)")
        out += _judge_matrix(part)
        out += _by_strategy(part)
        out += _by_lang(part)
        out += _citations(part)
        out += _pairwise(part)
        out += _agreement(part)
        out += _crosscheck(part)
        out += _mode_b(part)
        if part.get("note"):
            out += _p(f"> {part['note']}")
        return out

    out = _section(
        "שכבה 3 — תשובות ושיפוט עיוור",
        part["state"],
        body,
        what="השופטים טרם החזירו batches.",
    )
    if not part["state"].get("present"):
        out += _mode_b(part)
    return out


# ------------------------------------------------------------------------- incremental


def _rollback(rollback: Mapping[str, Any]) -> list[str]:
    """What the reset dry run said it would delete, per label — counted, not quoted."""
    if not rollback.get("present"):
        return []
    out = _h(3, "rollback — הרצה יבשה")
    out += _p(
        f"`{rollback.get('command')}` — לא הופעל "
        f"(`applied={_fmt(rollback.get('applied'))}`). {rollback.get('note')}"
    )
    return out + _table(
        ["מה היה נמחק", "כמה"],
        [[label, count] for label, count in (rollback.get("counts") or {}).items()],
    )


def _incremental(doc: Mapping[str, Any]) -> list[str]:
    part = doc["incremental"]

    def body() -> list[str]:
        out = _p(
            f"פרוסה `{part.get('slice')}` ממקור `{part.get('source')}` מאז "
            f"{_fmt(part.get('since'))}: {_fmt(len(part.get('keys') or []))} פריטים, "
            f"{_fmt(part.get('duration_s'))} שניות בסך הכול. "
            "שורה לכל שלב בפייפליין — שלב בלי מדידה נשאר בטבלה ומסומן, ולא נעלם."
        )
        out += _table(
            ["שלב", "פקודה", "שניות", "נמדד", "הערות"],
            [
                [
                    r["step"],
                    r.get("command"),
                    r.get("duration_s"),
                    r.get("measured"),
                    r.get("notes"),
                ]
                for r in part.get("steps") or []
            ],
        )
        summary = part.get("summary") or {}
        out += _h(3, "מה נוסף לגרף")
        out += _p(
            "שורה לכל מדד, בשמו. הרשימה של 645 מזהי הקהילות שהשתנו נספרת כאן ולא מודפסת — "
            "היא ב-`data/reports/incremental.json`, וזה המקום לקרוא אותה."
        )
        out += _table(
            ["מדד", "ערך"],
            [
                ["צמתים שנוספו", summary.get("nodes_added")],
                ["קשתות שנוספו", summary.get("edges_added")],
                ["chunks שנוספו", summary.get("chunks_added")],
                ["ישויות חדשות", summary.get("entities_minted")],
                ["קהילות לפני", summary.get("communities_before")],
                ["קהילות אחרי", summary.get("communities_after")],
                ["קהילות שהשתנו (member_hash)", summary.get("communities_changed")],
                ["דוחות קהילה שנשמרו", summary.get("reports_kept")],
                ["דוחות קהילה שאבדו", summary.get("reports_lost")],
                ["כיסוי חברים בקהילה מסוכמת — לפני (%)", summary.get("coverage_before")],
                ["כיסוי חברים בקהילה מסוכמת — אחרי (%)", summary.get("coverage_after")],
            ],
        )
        out += _rollback(part.get("rollback") or {})
        questions = part.get("questions") or []
        out += _h(3, "חמש השאלות על הפריטים החדשים")
        out += _p(
            "מצב A (אחזור קבוע, בלי סוכן) מול מצב B (הסוכן בוחר כלים). `ציטוט תקף` הוא "
            "המדד הדטרמיניסטי — כל סוגר מרובע מצביע על צומת שקיים."
        )
        out += _table(
            [
                "שאלה",
                "סוג",
                "route",
                "אסטרטגיה מובילה",
                "recall",
                "A: ציטוט תקף",
                "B: ציטוטים תקפים",
                "B: ציטוט תקף",
            ],
            [
                [
                    q.get("qid"),
                    q.get("type"),
                    q.get("route"),
                    q.get("best_strategy"),
                    q.get("best_recall"),
                    q.get("mode_a_citation_valid"),
                    f"{_fmt(q.get('mode_b_citations_valid'))}/{_fmt(q.get('mode_b_citations'))}",
                    q.get("mode_b_citation_valid"),
                ]
                for q in questions
            ],
        )
        a_ok = sum(1 for q in questions if q.get("mode_a_citation_valid"))
        b_ok = sum(1 for q in questions if q.get("mode_b_citation_valid"))
        if questions:
            out += _p(f"סיכום נגזר: מצב A {a_ok}/{len(questions)} · מצב B {b_ok}/{len(questions)}.")
        problems = part.get("problems") or []
        if problems:
            out += _p("⚠️ הריצה אינה שלמה לפי `validate_incremental`:")
            out += [f"- {p}" for p in problems] + [""]
        for warning in part.get("warnings") or []:
            out += _p(f"> {warning}")
        return out

    return _section(
        "עדכון אינקרמנטלי (§5.5)",
        part["state"],
        body,
        what="ריצת האינקרמנט טרם בוצעה.",
    )


# ----------------------------------------------------------------------------- "מתי מה"


def _when_what(doc: Mapping[str, Any]) -> list[str]:
    part = doc["when_what"]
    out = _h(2, "מתי מה")
    out += _p(
        "הטבלה היחידה בדוח שנגזרת ולא מועתקת: לכל סוג שאלה, מי הובילה ב-recall (שכבה 2), "
        "מי הובילה בנכונוּת לפי השופט (שכבה 3), ומה המהלך הזה עלה מול ה-baseline "
        f"(`{part.get('baseline')}`). הפרש חיובי = יקר יותר מה-baseline."
    )
    if not part.get("present"):
        return out + _p("*טרם נמדד — אין מטריצת שכבה 2 לגזור ממנה.*")
    if not part.get("judged"):
        out += _p(
            "⚠️ עמודות הנכונוּת ריקות עד ש-`brain eval judge merge` ירוץ; עמודות ה-recall כבר נגזרות."
        )
    out += _p(f"**{part.get('divergence_line')}**")
    out += _p(
        "שלוש עמודות ה-Δ שייכות ל**מובילת ה-recall** ולא למובילת הנכונוּת — העמודה "
        '"עלות מיוחסת ל" אומרת למי בדיוק. `n` ליד כל מוביל הוא מספר השאלות שהתא נשען '
        f"עליהן; מתחת ל-{rb_min_leader_n()} השורה מסמנת `n<{rb_min_leader_n()}` ולא קוראת "
        'לאסטרטגיה "מובילה".'
    )
    out += _table(
        [
            "סוג שאלה",
            "מובילה ב-recall",
            "recall",
            "recall של baseline",
            "מובילה בנכונוּת",
            "נכונוּת",
            "נכונוּת baseline",
            "עלות מיוחסת ל",
            "Δ latency p50 (ms)",
            "Δ tokens p50",
            "Δ Cypher",
            "עלות מובילת הנכונוּת",
            "הערה",
        ],
        [
            [
                _type_label(row["type"]),
                _leader(row.get("best_recall")),
                (row.get("best_recall") or {}).get("value"),
                row.get("baseline_recall"),
                _leader(row.get("best_correctness")),
                (row.get("best_correctness") or {}).get("value"),
                row.get("baseline_correctness"),
                _code(row.get("cost_attributed_to")),
                row.get("latency_delta_ms"),
                row.get("tokens_delta"),
                row.get("cypher_delta"),
                _correctness_cost(row.get("correctness_cost") or {}),
                row.get("note"),
            ]
            for row in part.get("rows") or []
        ],
    )
    lines = [
        f"- {row['leaders_line']}" for row in part.get("rows") or [] if row.get("leaders_line")
    ]
    return out + (lines + [""] if lines else [])


def rb_min_leader_n() -> int:
    from brain.eval.report_build import MIN_LEADER_N

    return MIN_LEADER_N


def _code(name: Any) -> str:
    return f"`{name}`" if name else DASH


def _leader(best: Mapping[str, Any] | None) -> str:
    """A leader's name with its `n`, or a dash when the `n` is too small to mean anything."""
    if not best:
        return DASH
    if not best.get("enough"):
        return f"{DASH}, n<{rb_min_leader_n()} (`{best.get('strategy')}`, n={_fmt(best.get('n'))})"
    return f"`{best.get('strategy')}` (n={_fmt(best.get('n'))})"


def _correctness_cost(cost: Mapping[str, Any]) -> str:
    """The correctness leader's own Δ — or a dash and the reason nobody measured it."""
    if not cost.get("strategy"):
        return DASH
    if not cost.get("available"):
        return f"{DASH} ({cost.get('why')})"
    return (
        f"Δ latency {_fmt(cost.get('latency_delta_ms'))} · "
        f"Δ tokens {_fmt(cost.get('tokens_delta'))} · "
        f"Δ Cypher {_fmt(cost.get('cypher_delta'))}"
    )


# --------------------------------------------------------------- the planner's two blocks


def carry(previous: str, heading: str) -> str:
    """The prose a person wrote under `heading` last time, or `""`.

    Anchored on the heading rather than on the order of the markers, so inserting a
    generated section between the two planner blocks cannot swap their contents.
    """
    at = previous.find(f"## {heading}")
    if at == -1:
        return ""
    start = previous.find(PLANNER_START, at)
    end = previous.find(PLANNER_END, start + len(PLANNER_START)) if start != -1 else -1
    if start == -1 or end == -1:
        return ""
    # A `##` between the heading and the marker means those markers belong to a later
    # section — this one lost its block rather than inheriting somebody else's.
    if "\n## " in previous[at + len(heading) : start]:
        return ""
    block = previous[start + len(PLANNER_START) : end].strip("\n")
    return "" if block.strip() == EMPTY_PLANNER else block


def _planner(previous: str) -> list[str]:
    out: list[str] = []
    for heading, hint in PLANNER_HEADINGS:
        block = carry(previous, heading) or EMPTY_PLANNER
        out += _h(2, heading)
        out += _p(f"*{hint}*")
        out += [PLANNER_START, block, PLANNER_END, ""]
    return out


# --------------------------------------------------------------------------- the document


def render(doc: Mapping[str, Any], previous: str = "") -> str:
    """The whole report. Everything but the two planner blocks is derived from `doc`."""
    layer01 = doc["layer01"]
    lines: list[str] = []
    lines += _header(doc)
    lines += _questions(doc)
    lines += _harvest(layer01["harvest"])
    lines += _census(layer01["census"])
    lines += _resolution(layer01["resolution"])
    lines += _provenance(layer01["provenance"])
    lines += _communities(layer01["communities"])
    lines += _layer2(doc)
    lines += _layer3(doc)
    lines += _incremental(doc)
    lines += _when_what(doc)
    lines += _p("---")
    lines += _p(
        "מה הדוח הזה *לא* אומר: איזו ארכיטקטורה נכונה. הוא אומר מה נמדד, על איזה קורפוס, "
        "באיזו עלות. שני הסעיפים הבאים הם הפרשנות, והם היחידים בדף שנכתבו ביד."
    )
    lines += _planner(previous)
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["EMPTY_PLANNER", "PLANNER_END", "PLANNER_HEADINGS", "PLANNER_START", "carry", "render"]
