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
        "**אין כאן מספר שהוקלד ביד** — למעט שני הסעיפים האחרונים, שהמתכנן כותב אחרי "
        "קריאת המספרים ושנשמרים בכל יצירה מחדש."
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
            ["סוג", "זוגות זהב", "P", "R", "F1", "TP", "FP", "FN", "לפני", "אחרי", "ungraded"],
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
                ]
                for r in part.get("rows") or []
            ],
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
                    cell.get("cases"),
                    *[cell.get(m) for m in metrics],
                ]
            )
    return _table(["אסטרטגיה", "סוג שאלה", "מקרים", *[METRIC_HE.get(m, m) for m in metrics]], rows)


def _pairwise(part: Mapping[str, Any]) -> list[str]:
    pairwise = part.get("pairwise") or {}
    out = _h(3, f"pairwise מול ה-baseline (`{pairwise.get('baseline') or DASH}`)")
    out += _p(
        "אותה שאלה, שתי תשובות, בסדר אקראי, בלי שם אסטרטגיה. `win_rate` סופר תיקו "
        "כאי-ניצחון; `both_wrong` מדווח בנפרד כי שתי תשובות שגויות שמסכימות אינן תיקו."
    )
    return out + _table(
        ["מתמודדת", "מקרים", "ניצחונות", "הפסדים", "תיקו", "שתיהן שגויות", "win rate", "בלי תיקו"],
        [
            [
                name,
                row.get("cases"),
                row.get("wins"),
                row.get("losses"),
                row.get("ties"),
                row.get("both_wrong"),
                row.get("win_rate"),
                row.get("win_rate_excluding_ties"),
            ]
            for name, row in (pairwise.get("by_challenger") or {}).items()
        ],
    )


def _by_lang(part: Mapping[str, Any]) -> list[str]:
    """Cross-lingual, at the answer layer: does a Hebrew question get a worse answer?

    Layer 2 answers that question about retrieval (`key_jaccard` over the same corpus);
    this is the other half — a set that is a third Hebrew is only balanced if the Hebrew
    third is not systematically scored lower.
    """
    metrics = list(part.get("metrics") or [])
    rows = [
        [LANG_HE.get(lang, lang), cell.get("cases"), *[cell.get(m) for m in metrics]]
        for lang, cell in sorted((part.get("by_lang") or {}).items())
    ]
    out = _h(3, "לפי שפת השאלה")
    return out + _table(["שפה", "מקרים", *[METRIC_HE.get(m, m) for m in metrics]], rows)


def _agreement(part: Mapping[str, Any]) -> list[str]:
    agreement = part.get("agreement") or {}
    out = _h(3, "הסכמה בין השופטים")
    out += _p(
        f"נמדדת רק על המקרים ששני shards שונים ניקדו: "
        f"{_fmt(agreement.get('overlap_cases'))} מקרים חופפים."
    )
    return out + _table(
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
        ],
    )


def _crosscheck(part: Mapping[str, Any]) -> list[str]:
    cross = part.get("crosscheck") or {}
    code = part.get("code_check") or {}
    out = _h(3, "שופט מול בדיקת הקוד (ציטוטים)")
    out += _p(
        "בדיקת הקוד בינארית: כל סוגר מרובע מצביע על משהו שהאחזור החזיר ושהגרף מחזיק. "
        f"מתוך {_fmt(cross.get('compared'))} מקרים שהושוו, {_fmt(cross.get('agreed'))} "
        f"בהסכמה ו-{_fmt(len(cross.get('disagreements') or []))} בסתירה "
        f"({_fmt(cross.get('disagreement_pct'))}%). הסתירה נרשמת רק בקצוות — "
        "1 של השופט מתיישב עם שתי ההכרעות."
    )
    out += _table(
        ["מדד", "ערך"],
        [
            ["מקרים שנבדקו בקוד", code.get("cases")],
            ["ציטוטים תקפים (קוד)", code.get("code_valid")],
            ["ציטוטים פסולים (קוד)", code.get("code_invalid")],
            ["תשובות בלי ציטוט", code.get("no_citation")],
            ["ציטוט שלא היה בהקשר", code.get("not_in_context")],
            ["ציטוט שאין לו צומת בגרף", code.get("not_in_graph")],
        ],
    )
    rows = [
        [d.get("case_id"), d.get("judge"), d.get("code"), d.get("why")]
        for d in (cross.get("disagreements") or [])
    ]
    if rows:
        out += _table(["מקרה", "ציון השופט", "הכרעת הקוד", "למה"], rows)
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
        out = _p(
            f"שיפוט עיוור לפי רובריקה 0–2 על ארבעה מדדים (§5.4). "
            f"{_fmt(judgments.get('total'))} שיפוטים על {_fmt(judgments.get('cases'))} מקרים, "
            f"{_fmt(judgments.get('pairs'))} השוואות pairwise, "
            f"{_fmt(judgments.get('rejected'))} נדחו. "
            f"התשובות: {_fmt(answers.get('accepted'))} התקבלו מתוך "
            f"{_fmt(answers.get('cases'))} מקרים שנבנו."
        )
        out += _h(3, "מטריצה: אסטרטגיה × סוג שאלה (ממוצעי השופטים)")
        out += _judge_matrix(part)
        out += _by_lang(part)
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
        out += _h(3, "מה נוסף לגרף")
        out += _table(
            ["חתך", "ערך"],
            [
                ["גרף", part.get("graph")],
                ["chunks", part.get("chunks")],
                ["ישויות", part.get("entities")],
                ["קהילות", part.get("communities")],
            ],
        )
        questions = part.get("questions") or []
        out += _h(3, "חמש השאלות על הפריטים החדשים")
        out += _table(
            ["שאלה", "ציטוט תקף", "פירוט"],
            [
                [
                    q.get("qid") or q.get("id") or q.get("question"),
                    q.get("citation_valid"),
                    q.get("note") or q.get("detail"),
                ]
                for q in questions
            ],
        )
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
    out += _table(
        [
            "סוג שאלה",
            "מובילה ב-recall",
            "recall",
            "recall של baseline",
            "מובילה בנכונוּת",
            "נכונוּת",
            "נכונוּת baseline",
            "Δ latency p50 (ms)",
            "Δ tokens p50",
            "Δ Cypher",
            "הערה",
        ],
        [
            [
                _type_label(row["type"]),
                (row.get("best_recall") or {}).get("strategy"),
                (row.get("best_recall") or {}).get("value"),
                row.get("baseline_recall"),
                (row.get("best_correctness") or {}).get("strategy"),
                (row.get("best_correctness") or {}).get("value"),
                row.get("baseline_correctness"),
                row.get("latency_delta_ms"),
                row.get("tokens_delta"),
                row.get("cypher_delta"),
                row.get("note"),
            ]
            for row in part.get("rows") or []
        ],
    )
    return out


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
