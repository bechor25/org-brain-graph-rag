# הערכה — דוח Plan 3

נוצר אוטומטית ע"י `brain eval report` ב-2026-09-17T09:35:16+00:00 (HEAD `9dba7252`) מתוך דוחות השלבים ב-`data/reports`. **אין כאן מספר שהוקלד ביד** — למעט שני הסעיפים האחרונים, שהמתכנן כותב אחרי קריאת המספרים ושנשמרים בכל יצירה מחדש.

כל שורה בטבלה הבאה היא קלט של הדוח: מתי נמדד, באיזה commit, והאם ה-commit הזה הוא ה-HEAD. `STALE` = המספרים קיימים אבל נמדדו על קוד אחר; `חסר` = הסעיף שמתבסס עליו יופיע כ-**טרם נמדד** עם הפקודה שמייצרת אותו.

| קובץ | מה מביא | מצב | נמדד ב | sha | הפקודה שכותבת | הערה |
|---|---|---|---|---|---|---|
| `data/reports/harvest.json` | שכבה 0 — מה נמשך מהמקורות | בלי sha | 2026-09-17T09:30:08+00:00 | — | `brain harvest` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/index.json` | שכבה 1 — מפקד, provenance, קהילות | בלי sha | 2026-09-17T09:35:09+00:00 | — | `brain index` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/resolve.json` | שכבה 1 — P/R/F1 של איחוד ישויות | בלי sha | 2026-09-07T18:26:35+00:00 | — | `brain resolve eval` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/retrieve.json` | תשתית האחזור — reranker ו-guard | בלי sha | 2026-09-17T07:33:52+00:00 | — | `brain cypher-examples check` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/eval_questions.json` | סט השאלות | בלי sha | 2026-09-17T09:23:22+00:00 | — | `brain eval questions merge` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/eval_retrieval.json` | שכבה 2 | STALE | 2026-09-17T09:32:23+00:00 | e1e28490 | `brain eval run --mode fixed` | נמדד ב-e1e28490, HEAD הוא 9dba7252 |
| `data/reports/eval_answers.json` | שכבה 3 — תשובות ושיפוט עיוור | STALE | — | — | `brain eval answers merge · brain eval judge merge` | סעיפים ישנים: answers_build |
| `data/reports/plan2_gate.json` | שער מצב B (ציטוטים) | STALE | 2026-09-17T09:18:15+00:00 | c15b8385 | `brain eval cite-check` | נמדד ב-c15b8385, HEAD הוא 9dba7252 |
| `data/reports/incremental.json` | §5.5 — ריצת העדכון האינקרמנטלי (כל הפייפליין) | עדכני | 2026-09-17T09:35:11+00:00 | 9dba725 | `brain harvest --since <date> --source jira` | — |

## סט השאלות

מקור: `data/reports/eval_questions.json` · נמדד ב-2026-09-17T09:23:22+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

הסט: `data/eval/questions.jsonl` (sha256 `68df8e48c68b9476`), 32 שאלות מתוך יעד 32. רצפת העברית: 11.

| חתך | פירוט |
|---|---|
| לפי סוג | traceability: 7, impact: 7, rationale: 6, global: 6, temporal: 6 |
| לפי שפה | he: 11, en: 21 |
| לפי מקור ה-gold | graph: 32 |
| לפי מוצא | competency: 19, forged: 13 |

### בדיקות הקבלה של הסט

| בדיקה | תוצאה | פירוט |
|---|---|---|
| every planned batch answered | עבר | 6/6 batches have an output |
| question count | עבר | 32 of 32 (19 competency + 13 forged) |
| per-type balance | עבר | {'traceability': 7, 'impact': 7, 'rationale': 6, 'global': 6, 'temporal': 6} — the most even split of 32 over 5 types is [7, 7, 6, 6, 6] |
| Hebrew >= 11 | עבר | 11 Hebrew of 32 (floor is a third, ceil(32/3) = 11) |
| every row has gold | עבר | 32/32 rows carry a gold answer |
| no failed batches | עבר | 0 batches could not be read |
| gold_evidence exists | עבר | 0 questions cited a key the graph does not hold (rejected) |
| no leakage | עבר | 0 questions leaked their answer (rejected) |

## שכבה 0 — harvest

מקור: `data/reports/harvest.json` · נמדד ב-2026-09-17T09:30:08+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

שכבה 0 של §5.2: מה בכלל נמשך מהמקורות. הספירות הן מה-checkpoint של כל קונקטור, כך שריצה חלקית נראית כחלקית ולא כקורפוס קטן.

| מקור | רשומות | עמודים | הושלם | שגיאות |
|---|---|---|---|---|
| confluence | 1,391 | 56 | כן | 0 |
| git | 6,107 | 7 | כן | 0 |
| jira | 1,416 | 3 | כן | 0 |

### צפיפות קישורים (Jira)

מה שהצדיק את הקורפוס הזה: כמה מה-issues נושאים קישור פורמלי, אזכור KIP והיסטוריה — בלי אלה אין מה לחלץ ואין מה לשאול.

| רכיב | avg_comments | n | pct_assignee | pct_fix_versions | pct_formal_links | pct_kip_mention | pct_with_history |
|---|---|---|---|---|---|---|---|
| streams | 2.9 | 509 | 76.4 | 50.3 | 30.8 | 28.1 | 98.2 |
| connect | 1.96 | 242 | 69.0 | 54.1 | 43.8 | 21.1 | 94.6 |
| clients | 2.51 | 686 | 81.6 | 62.4 | 37.0 | 16.9 | 98.3 |
| all | 2.55 | 1,416 | 77.8 | 57.1 | 36.0 | 21.3 | 97.6 |

## שכבה 1 — מפקד הגרף

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:35:09+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

הגרף שעליו נמדד הכול: **58,766** צמתים ו-**162,224** קשתות (147,462 דטרמיניסטיות, 14,762 מ-LLM). שער Plan 1: לא עבר — 11/13, נכשל: person_resolution_recall, make_smoke_green. המפקד המלא: `docs/report/plan1-graph-census.md`.

| מדד | ערך |
|---|---|
| issues אמיתיים | 1,426 |
| work items סינתטיים | 1,849 |
| מסמכי KIP | 1,334 |
| KIPs מוזכרים | 271 |
| commits | 6,107 |
| chunks | 13,917 |
| מהם חיים | 12,986 |
| מוטמעים | 13,917 |
| צמתים יתומים | 177 |

### שכבה 1 — איחוד ישויות

מקור: `data/reports/resolve.json` · נמדד ב-2026-09-07T18:26:35+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

P/R/F1 מול זוגות הזהב בלבד (יעד 0.85); מיזוגים שהזהב לא אומר עליהם דבר נספרים כ-`ungraded` ולא נכנסים לאף צד.

| סוג | זוגות זהב | P | R | F1 | TP | FP | FN | לפני | אחרי | ungraded |
|---|---|---|---|---|---|---|---|---|---|---|
| entity | 100 | 1.0 | 0.98 | 0.9899 | 49 | 0 | 1 | 9,237 | 9,038 | 176 |
| person | 633 | 0.9801 | 0.7789 | 0.868 | 444 | 9 | 126 | 2,187 | 1,214 | 1,656 |

> Precision and recall are computed over the labelled gold pairs only. Merges between identities the gold says nothing about (the real Jira / git / Confluence duplicates) are counted as `ungraded_merges` and left out of both, because calling them right or wrong would be a guess.

### שכבה 1 — כיסוי provenance

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:35:09+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

כלל הבעלות: conventions rule 3: every LLM-derived node and edge carries ['evidence_chunk_ids', 'batch_id', 'model', 'extracted_at'] with a non-empty evidence list. — 14,762 קשתות מ-LLM, 100.0% מהן עם provenance.

| label | צמתים | מהם מ-LLM | עם provenance | חסרים | % |
|---|---|---|---|---|---|
| Entity | 9,038 | 9,038 | 9,038 | 0 | 100.0 |
| Community | 1,297 | 186 | 186 | 0 | 100.0 |

| סוג קשת | קשתות | עם provenance | חסרים | % |
|---|---|---|---|---|
| MENTIONS | 9,375 | 9,375 | 0 | 100.0 |
| DECIDES | 2,698 | 2,698 | 0 | 100.0 |
| MOTIVATED_BY | 1,336 | 1,336 | 0 | 100.0 |
| REJECTS | 641 | 641 | 0 | 100.0 |
| DEPENDS_ON | 252 | 252 | 0 | 100.0 |
| IMPLEMENTS | 58 | 58 | 0 | 100.0 |
| INTRODUCES_RISK | 402 | 402 | 0 | 100.0 |

### שכבה 1 — כיסוי קהילות

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:35:09+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

**1,297** קהילות, מהן 186 מסוכמות (14.34%). 89.41% מהחברים נמצאים בקהילה מסוכמת — זה הכיסוי ש-S5 (חיפוש גלובלי) יכול לראות בכלל.

| רמה | קהילות | גודל p50 | גודל p95 | חברים | מסוכמות |
|---|---|---|---|---|---|
| 0 | 762 | 4 | 53 | 11,875 | 106 |
| 1 | 535 | 1 | 100 | 11,875 | 80 |

## שכבה 2 — אחזור (דטרמיניסטי)

מקור: `data/reports/eval_retrieval.json` · נמדד ב-2026-09-17T09:32:23+00:00 · sha `e1e28490`. **STALE** — נמדד ב-e1e28490, HEAD הוא 9dba7252.

מצב fixed, k=10, תקציב הקשר 4,000 tokens לכל אסטרטגיה, baseline = `s1r`. 32 שאלות; כיסוי 224/224. כל התא נמדד דטרמיניסטית מול `gold_evidence` — אין כאן שופט.

`recall` סופר התאמה בכל סוג (key, chunk, provenance, chunk_prefix, parent); `recall_strict` רק (chunk, chunk_prefix, key, provenance).

### מטריצה: אסטרטגיה × סוג שאלה

| אסטרטגיה | סוג שאלה | רץ/סה"כ | recall | recall_strict | precision | hit@10 | latency p50 (ms) | tokens p50 |
|---|---|---|---|---|---|---|---|---|
| s1 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0238 | 0.2292 | 0.6667 | 147 | 3,561 |
| s1 | השפעה (`impact`) | 7/7 | 0.0476 | 0.0238 | 0.0464 | 0.2857 | 144 | 3,646 |
| s1 | נימוק (`rationale`) | 6/6 | 0.0727 | 0.0238 | 0.0995 | 0.5 | 150 | 3,811 |
| s1 | זמני (`temporal`) | 6/6 | 0.0833 | 0.0 | 0.0167 | 0.1667 | 141 | 3,379 |
| s1 | עקיבוּת (`traceability`) | 7/7 | 0.0839 | 0.0 | 0.0623 | 0.4286 | 159 | 3,763 |
| s1r | גלובלי (`global`) | 6/6 | 0.0998 | 0.0238 | 0.2351 | 0.6667 | 2,086 | 3,562 |
| s1r | השפעה (`impact`) | 7/7 | 0.0476 | 0.0238 | 0.0464 | 0.2857 | 1,409 | 3,794 |
| s1r | נימוק (`rationale`) | 6/6 | 0.0727 | 0.0238 | 0.125 | 0.5 | 1,741 | 3,808 |
| s1r | זמני (`temporal`) | 6/6 | 0.0833 | 0.0 | 0.0167 | 0.1667 | 701 | 3,387 |
| s1r | עקיבוּת (`traceability`) | 7/7 | 0.0839 | 0.0 | 0.0623 | 0.4286 | 2,074 | 3,795 |
| s2 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0998 | 0.2472 | 0.6667 | 232 | 3,943 |
| s2 | השפעה (`impact`) | 7/7 | 0.0 | 0.0 | 0.0 | 0.0 | 233 | 3,856 |
| s2 | נימוק (`rationale`) | 6/6 | 0.0608 | 0.0608 | 0.25 | 0.5 | 237 | 3,807 |
| s2 | זמני (`temporal`) | 6/6 | 0.0833 | 0.0833 | 0.0208 | 0.1667 | 241 | 3,661 |
| s2 | עקיבוּת (`traceability`) | 7/7 | 0.0159 | 0.0159 | 0.0714 | 0.1429 | 245 | 3,940 |
| s3 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0575 | 0.2333 | 0.6667 | 240 | 2,674 |
| s3 | השפעה (`impact`) | 7/7 | 0.0974 | 0.0844 | 0.3286 | 0.5714 | 241 | 2,576 |
| s3 | נימוק (`rationale`) | 6/6 | 0.3337 | 0.3337 | 0.8833 | 1.0 | 238 | 2,794 |
| s3 | זמני (`temporal`) | 6/6 | 0.2463 | 0.2463 | 0.6167 | 0.8333 | 160 | 242 |
| s3 | עקיבוּת (`traceability`) | 7/7 | 0.3707 | 0.3707 | 0.7429 | 1.0 | 205 | 1,098 |
| s4 | גלובלי (`global`) | 6/6 | 0.0167 | 0.0167 | 0.0208 | 0.1667 | 6 | 1,098 |
| s4 | השפעה (`impact`) | 7/7 | 0.3636 | 0.3636 | 0.1852 | 0.4286 | 6 | 3,835 |
| s4 | נימוק (`rationale`) | 6/6 | 0.0 | 0.0 | 0.0 | 0.0 | 4 | 3,736 |
| s4 | זמני (`temporal`) | 6/6 | 0.1352 | 0.1352 | 0.5 | 0.5 | 5 | 564 |
| s4 | עקיבוּת (`traceability`) | 7/7 | 0.1869 | 0.1869 | 0.6429 | 0.7143 | 5 | 650 |
| s5 | גלובלי (`global`) | 6/6 | 0.1888 | 0.1888 | 0.2667 | 1.0 | 120 | 3,727 |
| s5 | השפעה (`impact`) | 0/7 | — | — | — | — | — | — |
| s5 | נימוק (`rationale`) | 0/6 | — | — | — | — | — | — |
| s5 | זמני (`temporal`) | 0/6 | — | — | — | — | — | — |
| s5 | עקיבוּת (`traceability`) | 0/7 | — | — | — | — | — | — |
| s6 | גלובלי (`global`) | 0/6 | — | — | — | — | — | — |
| s6 | השפעה (`impact`) | 1/7 | 0.0909 | 0.0909 | 0.0556 | 1.0 | 15 | 3,927 |
| s6 | נימוק (`rationale`) | 6/6 | 0.167 | 0.167 | 0.0979 | 1.0 | 4 | 3,718 |
| s6 | זמני (`temporal`) | 5/6 | 0.4667 | 0.4044 | 0.6 | 0.6 | 4 | 1,284 |
| s6 | עקיבוּת (`traceability`) | 6/7 | 0.3022 | 0.2837 | 0.1592 | 1.0 | 8 | 1,430 |

### עלות לפי אסטרטגיה

`n/a` = האסטרטגיה לא חלה על השאלה, עם סיבה — תא ריק שאיש לא יכול להסביר גרוע ממספר נמוך.

| אסטרטגיה | ריצות | n/a | latency p50 | latency p95 | tokens p50 | tokens סה"כ | tool calls | Cypher | זמן סוכן | סיבות ל-n/a |
|---|---|---|---|---|---|---|---|---|---|---|
| s1 | 32 | 0 | 147 | 179 | 3,761 | 117,299 | 32 | 95 | — | — |
| s1r | 32 | 0 | 1,792 | 3,586 | 3,794 | 118,652 | 32 | 95 | — | — |
| s2 | 32 | 0 | 241 | 253 | 3,891 | 127,510 | 32 | 96 | — | — |
| s3 | 32 | 0 | 234 | 266 | 2,648 | 62,862 | 32 | 180 | — | — |
| s4 | 32 | 0 | 6 | 18 | 1,279 | 69,109 | 32 | 32 | — | — |
| s5 | 6 | 26 | 120 | 136 | 3,727 | 22,483 | 6 | 6 | — | S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'keys') and is not typed `global` (14), S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'default') and is not typed `global` (4), S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'changed-in') and is not typed `global` (2) · ועוד 6 סיבות ב-JSON |
| s6 | 18 | 14 | 6 | 40 | 3,718 | 47,612 | 18 | 56 | — | temporal wording but no key/date/version pair to bind (14) |

### חוצה-שפות (EN מול HE)

אותה שאלה בשתי שפות, אותה אסטרטגיה. `key_jaccard` = חפיפת המפתחות שהוחזרו; ערך גבוה אומר שההטמעה הרב-לשונית מחזירה את אותו מקום בגרף, ולא רק ציון דומה.

| זוג | אסטרטגיה | qid (EN/HE) | recall EN/HE | items EN/HE | Jaccard | מפתחות משותפים |
|---|---|---|---|---|---|---|
| change | s1 | cq05 / cq17 | 0.0 / 0.1667 | 7 / 10 | 0.0 | 0 |
| change | s1r | cq05 / cq17 | 0.0 / 0.1667 | 8 / 10 | 0.0 | 0 |
| change | s2 | cq05 / cq17 | 0.0 / 0.0 | 6 / 3 | 0.0 | 0 |
| change | s3 | cq05 / cq17 | 0.0833 / 0.0833 | 1 / 1 | 1.0 | 1 |
| change | s4 | cq05 / cq17 | 0.0 / 0.0 | 18 / 18 | 1.0 | 10 |
| change | s5 | cq05 / cq17 | — / — | 0 / 0 | — | 0 |
| change | s6 | cq05 / cq17 | — / — | 0 / 0 | — | 0 |
| changed | s1 | cq13 / cq19 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| changed | s1r | cq13 / cq19 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| changed | s2 | cq13 / cq19 | 0.0 / 0.0 | 7 / 8 | 0.5 | 5 |
| changed | s3 | cq13 / cq19 | 0.0833 / 0.0833 | 1 / 1 | 1.0 | 1 |
| changed | s4 | cq13 / cq19 | 0.0 / 0.0 | 19 / 19 | 1.0 | 10 |
| changed | s5 | cq13 / cq19 | — / — | 0 / 0 | — | 0 |
| changed | s6 | cq13 / cq19 | 0.0 / 0.0 | 15 / 15 | 1.0 | 10 |
| tests | s1 | cq01 / cq16 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| tests | s1r | cq01 / cq16 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| tests | s2 | cq01 / cq16 | 0.0 / 0.0 | 8 / 7 | 0.25 | 3 |
| tests | s3 | cq01 / cq16 | 0.6667 / 0.6667 | 4 / 4 | 1.0 | 4 |
| tests | s4 | cq01 / cq16 | 0.1667 / 0.1667 | 3 / 3 | 1.0 | 1 |
| tests | s5 | cq01 / cq16 | — / — | 0 / 0 | — | 0 |
| tests | s6 | cq01 / cq16 | 0.1667 / 0.1667 | 7 / 7 | 1.0 | 7 |
| why | s1 | cq09 / cq18 | 0.1111 / 0.1111 | 9 / 9 | 0.8 | 8 |
| why | s1r | cq09 / cq18 | 0.1111 / 0.1111 | 8 / 8 | 0.6 | 6 |
| why | s2 | cq09 / cq18 | 0.1111 / 0.1111 | 4 / 4 | 0.6 | 3 |
| why | s3 | cq09 / cq18 | 0.2222 / 0.2222 | 10 / 10 | 0.8182 | 9 |
| why | s4 | cq09 / cq18 | 0.0 / 0.0 | 13 / 13 | 1.0 | 10 |
| why | s5 | cq09 / cq18 | — / — | 0 / 0 | — | 0 |
| why | s6 | cq09 / cq18 | 0.2222 / 0.2222 | 16 / 16 | 1.0 | 10 |

### עלות התשתית: reranker ו-guard

שני מספרים שלא תלויים בשאלה: כמה עולה ה-reranker שה-baseline נושא, וכמה הדוק ה-guard שמריץ את S4.

| מדד | ערך |
|---|---|
| מודל rerank | BAAI/bge-reranker-v2-m3 |
| זמין | כן |
| top-1 השתנה | 19/19 |
| top-1 עבר למסמך אחר | 19 |
| p50 בלי rerank (ms) | 167 |
| p50 עם rerank (ms) | 1,772 |
| תוספת p50 (ms) | 1,605 |
| guard: נחסמו | 56/56 |
| guard: דלפו | 0 |
| guard: קריאות שעברו | 21/21 |
| guard: LIMIT הוזרק | 9 |
| guard: timeout נאכף (ms) | 1,486 |

> measured, not improved: relevance is judged in Plan 3 (plan decision 5). A 100% top-1 change rate is what an RRF baseline invites: the fusion scores of ten chunks sit within ~0.002 of each other (1/(60+rank)), so any second opinion reorders them. `top1_source_changed` is the stricter number — how often the reranker moved the answer to a different document.

> The matrix is assembled from every run file on disk, not from the strategies of the last invocation, so a partial `--strategies` rerun refreshes one column and leaves the rest standing. Each column in `columns` carries the commit its own runs were measured at — not at HEAD: s1, s1r, s2, s3, s4, s5, s6.

> `recall` counts a chunk of the gold node as a hit (match kind `parent`); `recall_strict` does not. Both are reported so the vector baseline is not scored against an id scheme only the graph strategies emit.

## שכבה 3 — תשובות ושיפוט עיוור

**טרם נמדד.** השופטים טרם החזירו batches. הקובץ `data/reports/eval_answers.json` קיים, אבל הסעיף הזה עדיין לא נכתב לתוכו; הפקודה שמייצרת אותו: `brain eval judge merge`.

> התשובות נבנו (184 מקרים ב-43 batches, 184 מוזגו), אבל השופטים טרם החזירו ציונים.

### מצב B (אגנטי) לצד מצב A

**19** תשובות אגנטיות ב-`data/eval/answers/fixed`, עמודת `agentic` בטבלאות שלמעלה. הסוכן במצב B בחר כלים בעצמו ולכן לא נרשם לו הקשר ארוז — נאמנות (faithfulness) אינה מנוקדת עליהן, והיא מופיעה כחסרה ולא כאפס.

שער הציטוטים הדטרמיניסטי של Plan 2 על אותן תשובות:

| מדד | ערך |
|---|---|
| שאלות | 19 |
| נענו | 19 |
| ציטוטים שונים | 200 |
| מהם תקפים | 198 |
| מהם פסולים | 2 |
| שיעור תקינות | 99.0 |
| תשובות עם ≥1 ציטוט תקף | 19 |
| שער Plan 2 | 5/6 |

## עדכון אינקרמנטלי (§5.5)

מקור: `data/reports/incremental.json` · נמדד ב-2026-09-17T09:35:11+00:00 · sha `9dba725`.

פרוסה `incremental` ממקור `jira` מאז 2026-01-01: 10 פריטים, 29.64 שניות בסך הכול. שורה לכל שלב בפייפליין — שלב בלי מדידה נשאר בטבלה ומסומן, ולא נעלם.

| שלב | פקודה | שניות | נמדד | הערות |
|---|---|---|---|---|
| harvest | uv run brain harvest --since 2026-01-01 --source jira --limit 10 --slice incremental | 1.75 | כן | — |
| canon | uv run brain canon | 10.36 | כן | — |
| load | uv run brain load | 8.63 | כן | — |
| chunk | uv run brain chunk --kinds all | 7.63 | כן | — |
| extract build | uv run brain extract build --shards 1 --batch-size 25 --slice incremental | 1.27 | כן | — |
| extract merge | — | — | לא | — |
| resolve tier 1 | — | — | לא | — |
| communities build | — | — | לא | — |
| communities batches | — | — | לא | — |
| communities merge | — | — | לא | — |
| index | — | — | לא | — |

### מה נוסף לגרף

| חתך | ערך |
|---|---|
| גרף | slice_census: nodes_with_slice_by_label: Area:base: 24, Chunk:None: 13,846, Commit:base: 6,107, Community:None: 1,297, Component:base: 30, Document:base: 1,391, Entity:None: 9,038, File:None: 8,594, IndexMeta:None: 4, Person:base: 1,214, Person:incremental: 4, PullRequest:base: 6,026, Space:base: 1, Sprint:base: 92, StatusChange:base: 7,607, StatusChange:incremental: 59, Version:base: 86, WorkItem:base: 3,265, WorkItem:incremental: 10, incremental_nodes_by_label: Person: 4, StatusChange: 59, WorkItem: 10, nodes_without_slice_by_label: Chunk: 13,846, Community: 1,297, Entity: 9,038, File: 8,594, IndexMeta: 4, incremental_workitem_keys: KAFKA-20035, KAFKA-20040, KAFKA-20042, KAFKA-20043, KAFKA-20045, KAFKA-20046, KAFKA-20053, KAFKA-20056, KAFKA-20064, KAFKA-20065, chunks_by_slice: None: 13,846, census_after_part1: at: 2026-09-17T09:35:09+00:00, duration_s: 2.63, exit_code: 1, nodes_total: before: 58,622, after: 58,766, edges_total: before: 161,985, after: 162,224, nodes_by_label_delta: —, edges_by_type_delta: AFFECTS_VERSION: 3, ASSIGNED_TO: 10, COMMENTED: 33, FIX_VERSION: 10, HAS_CHANGE: 59, HAS_CHUNK: 71, IN_COMPONENT: 19, LINKS_TO: 2, MENTIONS_PERSON: 9, REFERENCES: 13, REPORTED_BY: 10, chunks: before: chunks: 13,846, live: 12,915, embedded: 13,846, missing_embedding: 0, orphaned: 931, orphaned_by_kind: description: 173, section: 758, without_has_chunk: 0, by_kind: comment: 3,629, description: 3,423, message: 4,150, section: 1,713, by_parent_kind: Commit: 4,150, Document: 1,713, WorkItem: 7,052, by_lang: en: 12,915, has_chunk_edges: Document: 2,471, WorkItem: 7,225, Commit: 4,150, has_chunk_total: 13,846, present: כן, pct_embedded: 100.0, live_embedded: 12,915, pct_live_embedded: 100.0, after: chunks: 13,917, live: 12,986, embedded: 13,917, missing_embedding: 0, orphaned: 931, orphaned_by_kind: description: 173, section: 758, without_has_chunk: 0, by_kind: comment: 3,662, description: 3,433, message: 4,150, section: 1,741, by_parent_kind: Commit: 4,150, Document: 1,741, WorkItem: 7,095, by_lang: en: 12,986, has_chunk_edges: Document: 2,499, WorkItem: 7,268, Commit: 4,150, has_chunk_total: 13,917, present: כן, pct_embedded: 100.0, live_embedded: 12,986, pct_live_embedded: 100.0, gate_before: issues: PASS, kips_embedded: PASS, kips_referenced_all_embedded: PASS, commits: PASS, person_resolution_precision: PASS, person_resolution_recall: FAIL, entity_resolution_precision: PASS, entity_resolution_recall: PASS, llm_edges_without_provenance: PASS, all_indexes_online: PASS, make_smoke_green: STALE, lessons_01_09: PASS, progress_complete: PASS, gate_after: issues: PASS, kips_embedded: PASS, kips_referenced_all_embedded: PASS, commits: PASS, person_resolution_precision: PASS, person_resolution_recall: FAIL, entity_resolution_precision: PASS, entity_resolution_recall: PASS, llm_edges_without_provenance: PASS, all_indexes_online: PASS, make_smoke_green: STALE, lessons_01_09: PASS, progress_complete: PASS, note: measured after `brain extract build`; the pipeline's own `index` step (12) has not run — the extractor agent, merge, resolve and communities come first. |
| chunks | — |
| ישויות | — |
| קהילות | — |

### חמש השאלות על הפריטים החדשים

*(אין נתונים)*

⚠️ הריצה אינה שלמה לפי `validate_incremental`:

- no row for step 'extract merge'
- no row for step 'resolve tier 1'
- no row for step 'communities build'
- no row for step 'communities batches'
- no row for step 'communities merge'
- no row for step 'index'
- `communities.member_hash_changed` is missing
- 0 questions, expected 5

## מתי מה

הטבלה היחידה בדוח שנגזרת ולא מועתקת: לכל סוג שאלה, מי הובילה ב-recall (שכבה 2), מי הובילה בנכונוּת לפי השופט (שכבה 3), ומה המהלך הזה עלה מול ה-baseline (`s1r`). הפרש חיובי = יקר יותר מה-baseline.

⚠️ עמודות הנכונוּת ריקות עד ש-`brain eval judge merge` ירוץ; עמודות ה-recall כבר נגזרות.

| סוג שאלה | מובילה ב-recall | recall | recall של baseline | מובילה בנכונוּת | נכונוּת | נכונוּת baseline | Δ latency p50 (ms) | Δ tokens p50 | Δ Cypher | הערה |
|---|---|---|---|---|---|---|---|---|---|---|
| גלובלי (`global`) | s5 | 0.1888 | 0.0998 | — | — | — | -1966.0 | 165.0 | -11.0 | אין שיפוט |
| השפעה (`impact`) | s4 | 0.3636 | 0.0476 | — | — | — | -1403.0 | 41.0 | -14.0 | אין שיפוט |
| נימוק (`rationale`) | s3 | 0.3337 | 0.0727 | — | — | — | -1503.0 | -1014.0 | 18.0 | אין שיפוט |
| זמני (`temporal`) | s6 | 0.4667 | 0.0833 | — | — | — | -697.0 | -2103.0 | -8.0 | אין שיפוט |
| עקיבוּת (`traceability`) | s3 | 0.3707 | 0.0839 | — | — | — | -1869.0 | -2697.0 | 19.0 | אין שיפוט |

---

מה הדוח הזה *לא* אומר: איזו ארכיטקטורה נכונה. הוא אומר מה נמדד, על איזה קורפוס, באיזו עלות. שני הסעיפים הבאים הם הפרשנות, והם היחידים בדף שנכתבו ביד.

## מתי לא הייתי משתמש בגרף כאן

*איפה הגרף לא החזיר את ההשקעה — לפי המספרים שלמעלה, לא לפי תחושה.*

<!-- planner:start -->
*(טרם נכתב. המתכנן כותב כאן אחרי קריאת המספרים; הטקסט בין שני הסימנים נשמר בכל יצירה מחדש של הדף.)*
<!-- planner:end -->

## מה הייתי משנה

*מה היה נעשה אחרת בסיבוב הבא — סכימה, אחזור, סט השאלות או המדידה עצמה.*

<!-- planner:start -->
*(טרם נכתב. המתכנן כותב כאן אחרי קריאת המספרים; הטקסט בין שני הסימנים נשמר בכל יצירה מחדש של הדף.)*
<!-- planner:end -->
