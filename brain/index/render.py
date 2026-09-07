"""`docs/report/plan1-graph-census.md` — the Hebrew census, generated from the JSON.

Decision 4: the document is produced by code from `data/reports/index.json` and nothing in
it is typed by hand. That is not a style preference. A census whose prose is written by a
person drifts from the graph the moment anything reruns, and the drift is invisible — the
numbers still look like numbers. Everything below reads its values out of the report dict;
the only literals here are Hebrew column headings and the explanations of what a column
means.

Conventions rule 6: `docs/` is Hebrew. Code, keys and labels stay English, because they are
the identifiers a reader has to type into Cypher.
"""

from __future__ import annotations

from typing import Any


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if isinstance(value, float):
        # `.4g` turns 1.0 into "1", which reads as an integer count next to a column of
        # precisions. A whole float keeps one decimal so the column stays one kind of number.
        return f"{value:.1f}" if value.is_integer() else f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) if value else "—"
    return str(value)


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


# --------------------------------------------------------------------------------- sections


def _gate(report: dict[str, Any]) -> list[str]:
    gate = report.get("gate") or {}
    rows = [
        [c["name"], c["requirement"], c["value"], c["status"]] for c in gate.get("criteria", [])
    ]
    out = _h(2, "שער היציאה של Plan 1")
    out += _p(
        f"מקור הקריטריונים: `{gate.get('source')}`. "
        f"עברו {_fmt(gate.get('passed'))} מתוך {_fmt(gate.get('total'))}."
    )
    out += _table(["קריטריון", "דרישה", "ערך שנמדד", "תוצאה"], rows)
    notes = [c for c in gate.get("criteria", []) if c.get("note")]
    if notes:
        out += _h(3, "הערות לקריטריונים")
        out += [f"- **{c['name']}** ({c['status']}): {c['note']}" for c in notes]
        out += [""]
    return out


def _nodes(report: dict[str, Any]) -> list[str]:
    nodes = report.get("nodes") or {}
    synth = (report.get("synthetic") or {}).get("by_label") or {}
    rows = []
    for group, labels in (("מבני", "structural"), ("נגזר", "derived")):
        for label, info in (nodes.get(labels) or {}).items():
            s = synth.get(label) or {}
            rows.append(
                [
                    label,
                    group,
                    info.get("count", 0) if info.get("present") else "—",
                    s.get("real", "—") if s.get("present") else "—",
                    s.get("synthetic", "—") if s.get("present") else "—",
                    s.get("missing_flag", "—") if s.get("present") else "—",
                ]
            )
    out = _h(2, "צמתים")
    out += _p(
        f'סה"כ **{_fmt(nodes.get("total"))}** צמתים (כל צומת נספר פעם אחת). '
        "עמודת `בלי דגל` = צמתים שאין עליהם `synthetic` בכלל — לא אותו דבר כמו `synthetic=false`."
    )
    out += _table(["label", "מקור", "צמתים", "אמיתי", "סינתטי", "בלי דגל"], rows)
    subs = nodes.get("workitem_sublabels") or {}
    if subs:
        out += _h(3, "תוויות משנה של WorkItem")
        out += _p("תוויות שנייה על אותם צמתים; חיבור שלהן ל-`WorkItem` סופר פעמיים.")
        out += _table(["label", "צמתים"], sorted(subs.items(), key=lambda kv: -kv[1]))
    other = nodes.get("other_labels") or {}
    if other:
        out += _h(3, "תוויות אחרות")
        out += _table(["label", "צמתים"], sorted(other.items()))
    return out


def _edges(report: dict[str, Any]) -> list[str]:
    edges = report.get("edges") or {}
    llm = (edges.get("llm_derived") or {}).get("by_type") or {}
    det = (edges.get("deterministic") or {}).get("by_type") or {}
    out = _h(2, "קשתות")
    out += _p(
        f'סה"כ **{_fmt(edges.get("total"))}** קשתות: '
        f"{_fmt((edges.get('deterministic') or {}).get('total'))} דטרמיניסטיות "
        f"(קונקטורים, mentions ב-regex, GDS) ו-"
        f"{_fmt((edges.get('llm_derived') or {}).get('total'))} מ-LLM."
    )
    out += _h(3, "קשתות מ-LLM")
    out += _table(["סוג", "קשתות"], sorted(llm.items(), key=lambda kv: -kv[1]))
    out += _h(3, "קשתות דטרמיניסטיות")
    out += _table(["סוג", "קשתות"], sorted(det.items(), key=lambda kv: -kv[1]))
    return out


def _provenance(report: dict[str, Any]) -> list[str]:
    prov = report.get("provenance") or {}
    node_rows = [
        [
            label,
            info.get("total", "—"),
            info.get("llm_derived", "—"),
            info.get("with_provenance", "—"),
            info.get("missing", "—"),
            f"{info.get('pct')}%" if info.get("present") else "—",
            info.get("llm_derived_rule", "—"),
        ]
        for label, info in ((prov.get("nodes") or {}).get("by_label") or {}).items()
    ]
    edge_rows = [
        [rel, i["total"], i["with_provenance"], i["missing"], f"{i['pct']}%"]
        for rel, i in ((prov.get("edges") or {}).get("by_type") or {}).items()
    ]
    out = _h(2, "Provenance")
    out += _p(f"הכלל: {prov.get('rule')}")
    out += _h(3, "צמתים")
    out += _table(
        ["label", "צמתים", "מהם מ-LLM", "עם provenance", "חסרים", "%", "מתי נחשב LLM"], node_rows
    )
    out += _h(3, "קשתות")
    out += _table(["סוג", "קשתות", "עם provenance", "חסרים", "%"], edge_rows)
    same = prov.get("same_as") or {}
    if same.get("total"):
        out += _h(3, "SAME_AS")
        out += _p(str(same.get("note")))
        out += _table(
            ["tier", "כלל", "קשתות", "עם provenance"],
            [[r["tier"], r["rule"], r["edges"], r["with_provenance"]] for r in same["by_tier"]],
        )
    return out


def _resolution(report: dict[str, Any]) -> list[str]:
    res = report.get("resolution") or {}
    out = _h(2, "איחוד ישויות (resolution)")
    if not res.get("available"):
        return out + _p(f"*אין נתונים — `{res.get('source')}` לא קיים.*")
    out += _p(
        f"מקור: `{res.get('source')}` (נוצר {_fmt(res.get('generated_at'))}); "
        f"יעד {_fmt(res.get('target'))}."
    )
    rows = []
    for kind, s in (res.get("kinds") or {}).items():
        rows.append(
            [
                kind,
                s.get("nodes_before"),
                s.get("nodes_after"),
                s.get("identities"),
                s.get("identities_per_node"),
                s.get("duplicate_rate_after"),
                s.get("gold_pairs"),
                s.get("precision"),
                s.get("recall"),
                s.get("f1"),
            ]
        )
    out += _table(
        [
            "סוג",
            "צמתים לפני",
            "צמתים אחרי",
            "זהויות",
            "זהויות/צומת",
            "שיעור כפילות",
            "זוגות זהב",
            "P",
            "R",
            "F1",
        ],
        rows,
    )
    tiers = [[kind, _fmt(s.get("merges_by_tier"))] for kind, s in (res.get("kinds") or {}).items()]
    out += _h(3, "מיזוגים לפי tier")
    out += _table(["סוג", "מיזוגים"], tiers)
    if res.get("note"):
        out += _p(f"> {res['note']}")
    return out


def _communities(report: dict[str, Any]) -> list[str]:
    com = report.get("communities") or {}
    out = _h(2, "קהילות")
    if not com.get("present"):
        return out + _p(f"*הסעיף ריק: {com.get('note')}*")
    out += _p(
        f"**{_fmt(com.get('communities'))}** קהילות, מהן {_fmt(com.get('summarised'))} מסוכמות "
        f"({_fmt(com.get('pct_summarised'))}%). "
        f"{_fmt(com.get('distinct_members'))} חברים ייחודיים דרך "
        f"{_fmt(com.get('in_community_edges'))} קשתות `IN_COMMUNITY`; "
        f"{_fmt(com.get('pct_members_in_a_summarised_community'))}% "
        "מהחברים נמצאים בקהילה מסוכמת."
    )
    out += _table(
        ["רמה", "קהילות", "גודל p50", "גודל p95", "מינ׳", "מקס׳", "חברים", "מסוכמות"],
        [
            [
                r["level"],
                r["communities"],
                r["size_p50"],
                r["size_p95"],
                r["size_min"],
                r["size_max"],
                r["members"],
                r["summarised"],
            ]
            for r in com.get("levels", [])
        ],
    )
    return out


def _chunks(report: dict[str, Any]) -> list[str]:
    ch = report.get("chunks") or {}
    out = _h(2, "Chunks")
    if not ch.get("present"):
        return out + _p(f"*{ch.get('note')}*")
    out += _p(
        f"**{_fmt(ch.get('chunks'))}** chunks בסך הכל: {_fmt(ch.get('live'))} חיים ו-"
        f"{_fmt(ch.get('orphaned'))} יתומים (טקסט שהוחלף — נשמר כי `brain extract` מצטט אותו). "
        f"{_fmt(ch.get('embedded'))} מוטמעים ({_fmt(ch.get('pct_embedded'))}%), מהם "
        f"{_fmt(ch.get('live_embedded'))} חיים ({_fmt(ch.get('pct_live_embedded'))}% מהחיים)."
    )
    out += _h(3, "לפי סוג (חיים)")
    out += _table(
        ["סוג", "chunks"], sorted((ch.get("by_kind") or {}).items(), key=lambda kv: -kv[1])
    )
    out += _h(3, "לפי צומת-אב (חיים)")
    out += _table(
        ["אב", "chunks"], sorted((ch.get("by_parent_kind") or {}).items(), key=lambda kv: -kv[1])
    )
    return out


def _indexes(report: dict[str, Any]) -> list[str]:
    idx = report.get("indexes") or {}
    rows = [
        [
            r["name"],
            r.get("type"),
            r.get("state"),
            r.get("population_percent"),
            r.get("owner"),
            "כן" if r.get("managed") else "לא",
            _fmt(r.get("labels")),
            _fmt(r.get("properties")),
        ]
        for r in idx.get("status", [])
    ]
    out = _h(2, "אינדקסים")
    out += _p(
        f"`brain index` מנהל {_fmt(idx.get('managed'))} אינדקסים; "
        f"{_fmt(idx.get('online'))} מהם ONLINE. בריצה הזו נוצרו "
        f"{_fmt((idx.get('applied') or {}).get('created'))} חדשים "
        f"({_fmt((idx.get('applied') or {}).get('existing'))} כבר היו). "
        "עמודת `מנוהל` מבדילה בין מה שהשלב הזה יוצר לבין אינדקסים של שלבים אחרים."
    )
    out += _table(
        ["שם", "סוג", "מצב", "populationPercent", "בעלים", "מנוהל", "labels", "properties"], rows
    )
    meta = report.get("index_meta") or {}
    out += _h(3, "IndexMeta")
    out += _p(str(meta.get("note")))
    out += _table(
        ["אינדקס", "label", "מודל", "מימד", "similarity", "וקטורים חיים", "ספירה שמורה", "עודכן"],
        [
            [
                r["index"],
                r.get("label"),
                r.get("model"),
                r.get("dim") or r.get("index_dim"),
                r.get("similarity"),
                r.get("live_vectors"),
                r.get("stored_count"),
                r.get("updated_at"),
            ]
            for r in meta.get("rows", [])
        ],
    )
    return out


def _orphans(report: dict[str, Any]) -> list[str]:
    orph = report.get("orphans") or {}
    dang = report.get("dangling_refs") or {}
    out = _h(2, "יתומים והפניות מתות")
    out += _p(
        f"**{_fmt(orph.get('total'))}** צמתים בלי אף קשת "
        f"({_fmt(orph.get('pct'))}% מ-{_fmt(orph.get('of_nodes'))}). {orph.get('note')}"
    )
    out += _table(
        ["label", "יתומים", "מתוך", "%"],
        [[k, v["orphans"], v["of"], v["pct"]] for k, v in (orph.get("by_label") or {}).items()],
    )
    out += _h(3, "הפניות מחוץ לפרוסה (dangling)")
    if not dang.get("available"):
        out += _p("*אין נתונים — `load.json` חסר.*")
    else:
        out += _p(
            f"מקור: `{dang.get('source')}`. {dang.get('note')} "
            f"בנוסף: {_fmt(dang.get('links_dangling'))} מתוך "
            f"{_fmt(dang.get('links_declared'))} קישורים פורמליים הצביעו מחוץ לפרוסה."
        )
        refs_by_kind = dang.get("refs_by_kind") or {}
        out += _table(
            ["סוג", "הפניות מתות", 'מתוך סה"כ הפניות'],
            [
                [kind, n, refs_by_kind.get(kind, "—")]
                for kind, n in sorted((dang.get("by_kind") or {}).items(), key=lambda kv: -kv[1])
            ],
        )
    return out


def _corpus(report: dict[str, Any]) -> list[str]:
    c = report.get("corpus") or {}
    out = _h(2, "הקורפוס")
    out += _table(
        ["מדד", "ערך"],
        [
            ["issues אמיתיים (Jira)", c.get("real_issues")],
            ["work items סינתטיים (Xray/ADO)", c.get("synthetic_workitems")],
            ["מסמכים", c.get("documents")],
            ["מהם KIP", c.get("kip_documents")],
            ['KIPs שמוזכרים ע"י issue/commit', c.get("kips_referenced")],
            ["KIPs מוטמעים", c.get("kips_embedded")],
            ["KIPs מוזכרים שמוטמעים", c.get("kips_referenced_embedded")],
            ['KIPs שמצוטטים ע"י כל דבר (כולל KIP→KIP)', c.get("kips_cited_by_anything")],
            ["commits", c.get("commits")],
            ["commits עם מפתח (chunked)", c.get("commits_keyed")],
        ],
    )
    if c.get("kips_referenced_rule"):
        out += _p(f'> הגדרת "מוזכר": {c["kips_referenced_rule"]}')
    return out


def _versions(report: dict[str, Any]) -> list[str]:
    v = report.get("versions") or {}
    ollama = v.get("ollama") or {}
    detail = ollama.get("model_detail") or {}
    out = _h(2, "גרסאות")
    out += _table(
        ["רכיב", "גרסה", "פרטים"],
        [
            ["Neo4j", (v.get("neo4j") or {}).get("version"), (v.get("neo4j") or {}).get("edition")],
            ["GDS", (v.get("gds") or {}).get("version"), (v.get("gds") or {}).get("error", "")],
            ["APOC", (v.get("apoc") or {}).get("version"), (v.get("apoc") or {}).get("error", "")],
            ["Ollama", ollama.get("version"), ollama.get("url")],
            [
                "מודל הטמעה",
                detail.get("name") or ollama.get("model"),
                f"{detail.get('parameter_size', '—')} · {detail.get('quantization', '—')} · "
                f"digest {detail.get('digest', '—')}",
            ],
        ],
    )
    return out


def _canonical(report: dict[str, Any]) -> list[str]:
    canon = report.get("canonical") or {}
    out = _h(2, "טביעת אצבע של הקורפוס הקנוני")
    out += _p(
        f'מ-`{canon.get("dir")}`. המפקד עונה על "מה יש בגרף"; הטבלה הזו עונה על "ממה" — '
        "בלעדיה שתי ריצות עם אותם מספרים יכולות היו לקרוא קבצים שונים."
    )
    out += _table(
        ["קובץ", "sha256", "בייטים", "רשומות"],
        [
            [name, info.get("sha256", "")[:16], info.get("bytes"), info.get("records")]
            for name, info in sorted((canon.get("files") or {}).items())
        ],
    )
    return out


def _warnings(report: dict[str, Any]) -> list[str]:
    warns = report.get("warnings") or []
    kips = (report.get("sanity") or {}).get("kip_entity_coverage") or {}
    out = _h(2, "ספי שפיות")
    if kips.get("measured"):
        out += _p(
            f"KIPs שעברו chunking: **{_fmt(kips.get('chunked_kips'))}**, מהם "
            f"**{_fmt(kips.get('without_an_entity'))}** בלי אף ישות "
            f"({_fmt(kips.get('pct_without_an_entity'))}%). {kips.get('rule')}"
        )
    else:
        out += _p(f"*לא נמדד: {kips.get('reason', '—')}*")
    out += _h(2, "אזהרות")
    if not warns:
        return out + _p("*אין.*")
    out += _p("ספי שפיות מדווחים ולא נאכפים: מספר שנראה חשוד מופיע כאן, אף אחד לא מסונן בשקט.")
    out += _table(
        ["חומרה", "נושא", "פירוט"],
        [[w.get("severity"), w.get("name"), w.get("detail")] for w in warns],
    )
    return out


# ------------------------------------------------------------------------------- the page


def render(report: dict[str, Any]) -> str:
    """The whole document. Every number in it comes out of `report`."""
    lines: list[str] = []
    lines += _h(1, "מפקד הגרף — שער Plan 1")
    lines += _p(
        f'נוצר אוטומטית ע"י `brain index` ב-{_fmt(report.get("generated_at"))} מתוך '
        f"`{report.get('report_path', 'data/reports/index.json')}`. "
        "**אין כאן מספר שהוקלד ביד** — כל טבלה נגזרת מה-JSON, כך שריצה חוזרת של השלב "
        "מעדכנת את המסמך במקום להשאיר אותו מיושן."
    )
    gate = report.get("gate") or {}
    lines += _p(
        f"**מצב השער:** {'עבר' if gate.get('ok') else 'לא עבר'} — "
        f"{_fmt(gate.get('passed'))}/{_fmt(gate.get('total'))} קריטריונים."
    )
    for section in (
        _gate,
        _corpus,
        _nodes,
        _edges,
        _provenance,
        _resolution,
        _communities,
        _chunks,
        _indexes,
        _orphans,
        _canonical,
        _versions,
        _warnings,
    ):
        lines += section(report)
    lines += _p("---")
    lines += _p(
        "מה המפקד הזה *לא* יכול לענות: הוא סופר מבנה, לא איכות תשובות. "
        "האם קשת `DECIDES` נכונה, האם קהילה מספרת סיפור אמיתי, והאם אחזור מוצא את הראיה "
        "הנכונה — כל אלה נמדדים ב-Plan 3, לא כאן."
    )
    return "\n".join(lines).rstrip() + "\n"
