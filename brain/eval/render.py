"""`docs/report/plan2-first-questions.md` — the Hebrew page, generated from the JSON.

Same rule as the Plan 1 census (`brain/index/render.py`): every number here is read out of
`data/reports/plan2_gate.json`, so a rerun updates the page instead of leaving it quietly
wrong. Conventions rule 6 puts `docs/` in Hebrew; keys, tool names and criterion names stay
English because they are identifiers a reader has to type.

The one exception is the planner's verdict paragraph. It is the only hand-written thing on
the page, so it lives between two markers and is carried across every regeneration — a
generated document that eats its author's paragraph gets regenerated exactly once.
"""

from __future__ import annotations

from typing import Any

PLANNER_HEADING = "## הכרעת המתכנן"
PLANNER_START = "<!-- planner:start -->"
PLANNER_END = "<!-- planner:end -->"
DEFAULT_PLANNER_BLOCK = (
    "*(כאן המתכנן כותב את פסקת ההכרעה: מה נכון, מה חלקי, מה שגוי ולמה. "
    "הטקסט בין שני הסימנים נשמר בכל יצירה מחדש של הדף — `planner_verdicts` "
    "ב-JSON הוא הטבלה, והפסקה הזו היא ההסבר.)*"
)

VERDICT_HE: dict[str, str] = {"correct": "נכון", "partial": "חלקי", "wrong": "שגוי"}
KIND_HE: dict[str, str] = {
    "workitem": "פריט עבודה",
    "document": "מסמך/KIP",
    "chunk": "chunk",
    "commit": "commit",
    "person": "אדם",
    "community": "קהילה",
}
DASH = "—"


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
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) if value else DASH
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
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


def _notes(row: dict[str, Any]) -> str:
    if not row["answered"]:
        return "אין תשובה"
    problems = list(row.get("problems") or [])
    if row["citations"]["invalid"]:
        problems.append(f"{row['citations']['invalid']} ציטוטים פסולים")
    return "; ".join(problems) if problems else ""


def _questions_table(report: dict[str, Any]) -> list[str]:
    verdicts = report.get("planner_verdicts") or {}
    rows = []
    for row in report["questions"]:
        citations = row["citations"]
        answered = row["answered"]
        rows.append(
            [
                row["id"],
                row.get("lang"),
                row.get("tools"),
                f"{citations['valid']}/{citations['found']}" if answered else None,
                row.get("latency_ms"),
                VERDICT_HE.get(verdicts.get(row["id"], ""), DASH),
                _notes(row),
            ]
        )
    out = _h(2, "שאלה אחרי שאלה")
    out += _p(
        "שורה לכל שאלה מתוך `competency.jsonl`. **ציטוטים** = כמה מהציטוטים השונים בתשובה "
        "נמצאו בגרף מתוך כמה נבדקו (אותו ציטוט פעמיים נספר פעם אחת). **latency** במילישניות, "
        "משורת `latency:` בקובץ התשובה או מהפרש `started_at`/`finished_at`. "
        '**הכרעה** נכתבת ביד ע"י המתכנן ב-`planner_verdicts` שב-JSON; '
        f"`{DASH}` = טרם הוכרע."
    )
    out += _table(["שאלה", "שפה", "כלים", "ציטוטים", "latency (ms)", "הכרעה", "הערות"], rows)
    return out


def _totals(report: dict[str, Any]) -> list[str]:
    totals = report["totals"]
    latency = totals.get("latency_ms") or {}
    out = _h(2, "סיכום")
    out += _table(
        ["מדד", "ערך"],
        [
            ["שאלות בסט", totals["questions"]],
            ["נענו", f"{totals['answered']}/{totals['questions']}"],
            ["חסרות", totals["missing"]],
            [
                "תשובות עם ≥1 ציטוט תקף",
                f"{totals['with_at_least_one_valid_citation']}/{totals['questions']}",
            ],
            ["ציטוטים שונים", totals["citations_found"]],
            ["מהם תקפים", totals["citations_valid"]],
            ["מהם פסולים", totals["citations_invalid"]],
            ["שיעור תקינות", f"{totals['validity_rate']}%"],
            [
                "משפטים עם ציטוט",
                f"{totals['sentences_with_citation']}/{totals['sentences_total']}",
            ],
            ["latency p50 (ms)", latency.get("p50")],
            ["latency min–max (ms)", f"{_fmt(latency.get('min'))}–{_fmt(latency.get('max'))}"],
            ["תשובות לפי שפה", totals.get("by_lang")],
        ],
    )
    out += _h(3, "ציטוטים לפי סוג")
    out += _table(
        ["סוג", "ציטוטים שונים"],
        [[KIND_HE.get(k, k), v] for k, v in (totals.get("by_kind") or {}).items()],
    )
    out += _h(3, "כלים שהסוכנים השתמשו בהם")
    out += _p("כמה תשובות הזכירו כל כלי בשורת ה-`strategy:` (תשובה נספרת פעם אחת לכלי).")
    out += _table(["כלי", "תשובות"], list((totals.get("tools_used") or {}).items()))
    return out


def _gate(report: dict[str, Any]) -> list[str]:
    gate = report.get("gate") or {}
    out = _h(2, "שער Plan 2")
    out += _p(
        f"**מצב:** {'עבר' if gate.get('ok') else 'לא עבר'} — "
        f"{_fmt(gate.get('passed'))}/{_fmt(gate.get('total'))} קריטריונים. "
        "רק הקריטריונים המסומנים `כן` בעמודת **מחייב** קובעים את קוד היציאה של "
        "`brain eval cite-check`; השאר נמדדים ומדווחים."
    )
    out += _table(
        ["קריטריון", "דרישה", "ערך", "תוצאה", "מחייב", "פירוט"],
        [
            [
                c["name"],
                c["requirement"],
                c["value"],
                "עבר" if c["ok"] else "נכשל",
                c.get("gate", True),
                c.get("detail"),
            ]
            for c in gate.get("criteria", [])
        ],
    )
    return out


def _invalid(report: dict[str, Any]) -> list[str]:
    rows = [
        [row["id"], bad["text"], KIND_HE.get(bad["kind"], bad["kind"]), bad["reason"]]
        for row in report["questions"]
        for bad in row["citations"]["invalid_list"]
    ]
    out = _h(2, "ציטוטים פסולים")
    if not rows:
        return out + _p("*אין — כל ציטוט בכל תשובה נמצא בגרף.*")
    out += _p(
        "ציטוט פסול = מפתח או מזהה שהסוכן כתב ושאין לו צומת בגרף. זו בדיקה דטרמיניסטית "
        "(`STARTS WITH` ל-chunk ול-sha, השוואה מדויקת למפתחות), לא שיפוט של התשובה."
    )
    out += _table(["שאלה", "הציטוט", "סוג", "למה נפסל"], rows)
    return out


def _planner(previous: str) -> list[str]:
    start = previous.find(PLANNER_START)
    end = previous.find(PLANNER_END)
    block = (
        previous[start + len(PLANNER_START) : end].strip("\n")
        if start != -1 and end > start
        else DEFAULT_PLANNER_BLOCK
    )
    return [PLANNER_HEADING, "", PLANNER_START, block, PLANNER_END, ""]


def render(report: dict[str, Any], previous: str = "") -> str:
    """The whole document. Everything but the planner's block is derived from `report`."""
    totals = report["totals"]
    lines: list[str] = []
    lines += _h(1, "שאלות ראשונות — שער Plan 2")
    lines += _p(
        f'נוצר אוטומטית ע"י `brain eval gate-report` מתוך '
        f"`{report.get('report_path', 'data/reports/plan2_gate.json')}` "
        f"(נמדד ב-{_fmt(report.get('generated_at'))}, sha `{str(report.get('sha') or '')[:8]}`). "
        "**אין כאן מספר שהוקלד ביד** — למעט פסקת ההכרעה של המתכנן, שנשמרת בין יצירות."
    )
    lines += _p(
        f"הסוכנים ענו על {_fmt(totals['answered'])} מתוך {_fmt(totals['questions'])} שאלות "
        f"מ-`{report.get('questions_path')}`; התשובות נמצאות ב-`{report.get('answers_dir')}`. "
        "כל תשובה נכתבה במצב B (הסוכן בוחר כלים דרך MCP), ו-`cite-check` בדק כל ציטוט "
        "מול הגרף בקריאה בלבד."
    )
    for section in (_gate, _questions_table, _totals, _invalid):
        lines += section(report)
    lines += _planner(previous)
    lines += _p("---")
    lines += _p(
        "מה הדף הזה *לא* אומר: האם התשובה נכונה. `cite-check` יודע רק שהציטוט קיים בגרף — "
        "השאלה אם הסוכן השתמש בראיה נכון היא הכרעת המתכנן כאן, ושיפוט עיוור לפי רובריקה "
        "הוא Plan 3."
    )
    return "\n".join(lines).rstrip() + "\n"
