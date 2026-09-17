# הערכה — דוח Plan 3

נוצר אוטומטית ע"י `brain eval report` ב-2026-09-17T10:13:27+00:00 (HEAD `4cf28f9a`) מתוך דוחות השלבים ב-`data/reports`. **אין כאן מספר שהוקלד ביד** — למעט שני הסעיפים האחרונים, שהמתכנן כותב אחרי קריאת המספרים ושנשמרים בכל יצירה מחדש.

כל שורה בטבלה הבאה היא קלט של הדוח: מתי נמדד, באיזה commit, והאם ה-commit הזה הוא ה-HEAD. `STALE` = המספרים קיימים אבל נמדדו על קוד אחר; `חסר` = הסעיף שמתבסס עליו יופיע כ-**טרם נמדד** עם הפקודה שמייצרת אותו.

| קובץ | מה מביא | מצב | נמדד ב | sha | הפקודה שכותבת | הערה |
|---|---|---|---|---|---|---|
| `data/reports/harvest.json` | שכבה 0 — מה נמשך מהמקורות | בלי sha | 2026-09-17T09:30:08+00:00 | — | `brain harvest` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/index.json` | שכבה 1 — מפקד, provenance, קהילות | בלי sha | 2026-09-17T09:53:19+00:00 | — | `brain index` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/resolve.json` | שכבה 1 — P/R/F1 של איחוד ישויות | בלי sha | 2026-09-17T09:48:08+00:00 | — | `brain resolve eval` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/retrieve.json` | תשתית האחזור — reranker ו-guard | בלי sha | 2026-09-17T07:33:52+00:00 | — | `brain cypher-examples check` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/eval_questions.json` | סט השאלות | בלי sha | 2026-09-17T09:23:22+00:00 | — | `brain eval questions merge` | הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד |
| `data/reports/eval_retrieval.json` | שכבה 2 | STALE | 2026-09-17T09:35:39+00:00 | 9dba7252 | `brain eval run --mode fixed` | נמדד ב-9dba7252, HEAD הוא 4cf28f9a |
| `data/reports/eval_answers.json` | שכבה 3 — תשובות ושיפוט עיוור | STALE | 2026-09-17T10:13:04+00:00 | 4cf28f9a | `brain eval answers merge · brain eval judge merge` | סעיפים ישנים: answers_build, answers_merge, judge_build |
| `data/reports/plan2_gate.json` | שער מצב B (ציטוטים) | STALE | 2026-09-17T09:18:15+00:00 | c15b8385 | `brain eval cite-check` | נמדד ב-c15b8385, HEAD הוא 4cf28f9a |
| `data/reports/incremental.json` | §5.5 — ריצת העדכון האינקרמנטלי (כל הפייפליין) | עדכני | 2026-09-17T10:12:44+00:00 | 4cf28f9 | `brain harvest --since <date> --source jira` | — |

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

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:53:19+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

הגרף שעליו נמדד הכול: **58,560** צמתים ו-**162,330** קשתות (147,534 דטרמיניסטיות, 14,796 מ-LLM). שער Plan 1: לא עבר — 11/13, נכשל: person_resolution_recall, make_smoke_green. המפקד המלא: `docs/report/plan1-graph-census.md`.

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
| צמתים יתומים | 176 |

### שכבה 1 — איחוד ישויות

מקור: `data/reports/resolve.json` · נמדד ב-2026-09-17T09:48:08+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

P/R/F1 מול זוגות הזהב בלבד (יעד 0.85); מיזוגים שהזהב לא אומר עליהם דבר נספרים כ-`ungraded` ולא נכנסים לאף צד.

| סוג | זוגות זהב | P | R | F1 | TP | FP | FN | לפני | אחרי | ungraded |
|---|---|---|---|---|---|---|---|---|---|---|
| entity | 100 | 1.0 | 0.98 | 0.9899 | 49 | 0 | 1 | 9,237 | 9,064 | 176 |
| person | 633 | 0.9801 | 0.7789 | 0.868 | 444 | 9 | 126 | 2,187 | 1,217 | 1,656 |

> Precision and recall are computed over the labelled gold pairs only. Merges between identities the gold says nothing about (the real Jira / git / Confluence duplicates) are counted as `ungraded_merges` and left out of both, because calling them right or wrong would be a guess.

### שכבה 1 — כיסוי provenance

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:53:19+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

כלל הבעלות: conventions rule 3: every LLM-derived node and edge carries ['evidence_chunk_ids', 'batch_id', 'model', 'extracted_at'] with a non-empty evidence list. — 14,796 קשתות מ-LLM, 100.0% מהן עם provenance.

| label | צמתים | מהם מ-LLM | עם provenance | חסרים | % |
|---|---|---|---|---|---|
| Entity | 9,064 | 9,064 | 9,064 | 0 | 100.0 |
| Community | 1,066 | 124 | 124 | 0 | 100.0 |

| סוג קשת | קשתות | עם provenance | חסרים | % |
|---|---|---|---|---|
| MENTIONS | 9,404 | 9,404 | 0 | 100.0 |
| DECIDES | 2,699 | 2,699 | 0 | 100.0 |
| MOTIVATED_BY | 1,338 | 1,338 | 0 | 100.0 |
| REJECTS | 641 | 641 | 0 | 100.0 |
| DEPENDS_ON | 252 | 252 | 0 | 100.0 |
| IMPLEMENTS | 58 | 58 | 0 | 100.0 |
| INTRODUCES_RISK | 404 | 404 | 0 | 100.0 |

### שכבה 1 — כיסוי קהילות

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T09:53:19+00:00. ⚠️ הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד.

**1,066** קהילות, מהן 124 מסוכמות (11.63%). 47.5% מהחברים נמצאים בקהילה מסוכמת — זה הכיסוי ש-S5 (חיפוש גלובלי) יכול לראות בכלל.

| רמה | קהילות | גודל p50 | גודל p95 | חברים | מסוכמות |
|---|---|---|---|---|---|
| 0 | 533 | 1 | 100 | 11,911 | 62 |
| 1 | 533 | 1 | 100 | 11,911 | 62 |

## שכבה 2 — אחזור (דטרמיניסטי)

מקור: `data/reports/eval_retrieval.json` · נמדד ב-2026-09-17T09:35:39+00:00 · sha `9dba7252`. **STALE** — נמדד ב-9dba7252, HEAD הוא 4cf28f9a.

מצב fixed, k=10, תקציב הקשר 4,000 tokens לכל אסטרטגיה, baseline = `s1r`. 32 שאלות; כיסוי 224/224. כל התא נמדד דטרמיניסטית מול `gold_evidence` — אין כאן שופט.

`recall` סופר התאמה בכל סוג (key, chunk, provenance, chunk_prefix, parent); `recall_strict` רק (chunk, chunk_prefix, key, provenance).

### מטריצה: אסטרטגיה × סוג שאלה

| אסטרטגיה | סוג שאלה | רץ/סה"כ | recall | recall_strict | precision | hit@10 | latency p50 (ms) | tokens p50 |
|---|---|---|---|---|---|---|---|---|
| s1 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0238 | 0.2292 | 0.6667 | 147 | 3,561 |
| s1 | השפעה (`impact`) | 7/7 | 0.0476 | 0.0238 | 0.0464 | 0.2857 | 146 | 3,646 |
| s1 | נימוק (`rationale`) | 6/6 | 0.0727 | 0.0238 | 0.0995 | 0.5 | 150 | 3,811 |
| s1 | זמני (`temporal`) | 6/6 | 0.0833 | 0.0 | 0.0167 | 0.1667 | 143 | 3,379 |
| s1 | עקיבוּת (`traceability`) | 7/7 | 0.0839 | 0.0 | 0.0623 | 0.4286 | 160 | 3,763 |
| s1r | גלובלי (`global`) | 6/6 | 0.0998 | 0.0238 | 0.2351 | 0.6667 | 2,086 | 3,562 |
| s1r | השפעה (`impact`) | 7/7 | 0.0476 | 0.0238 | 0.0464 | 0.2857 | 1,409 | 3,794 |
| s1r | נימוק (`rationale`) | 6/6 | 0.0727 | 0.0238 | 0.125 | 0.5 | 1,741 | 3,808 |
| s1r | זמני (`temporal`) | 6/6 | 0.0833 | 0.0 | 0.0167 | 0.1667 | 701 | 3,387 |
| s1r | עקיבוּת (`traceability`) | 7/7 | 0.0839 | 0.0 | 0.0623 | 0.4286 | 2,074 | 3,795 |
| s2 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0998 | 0.2472 | 0.6667 | 241 | 3,943 |
| s2 | השפעה (`impact`) | 7/7 | 0.0 | 0.0 | 0.0 | 0.0 | 243 | 3,856 |
| s2 | נימוק (`rationale`) | 6/6 | 0.0608 | 0.0608 | 0.25 | 0.5 | 237 | 3,807 |
| s2 | זמני (`temporal`) | 6/6 | 0.0833 | 0.0833 | 0.0208 | 0.1667 | 243 | 3,661 |
| s2 | עקיבוּת (`traceability`) | 7/7 | 0.0159 | 0.0159 | 0.0714 | 0.1429 | 245 | 3,951 |
| s3 | גלובלי (`global`) | 6/6 | 0.0998 | 0.0575 | 0.2333 | 0.6667 | 247 | 2,674 |
| s3 | השפעה (`impact`) | 7/7 | 0.0974 | 0.0844 | 0.3286 | 0.5714 | 241 | 2,576 |
| s3 | נימוק (`rationale`) | 6/6 | 0.3337 | 0.3337 | 0.8833 | 1.0 | 238 | 2,794 |
| s3 | זמני (`temporal`) | 6/6 | 0.2407 | 0.2407 | 0.6167 | 0.8333 | 197 | 242 |
| s3 | עקיבוּת (`traceability`) | 7/7 | 0.3707 | 0.3707 | 0.7429 | 1.0 | 205 | 1,098 |
| s4 | גלובלי (`global`) | 6/6 | 0.0167 | 0.0167 | 0.0208 | 0.1667 | 9 | 1,098 |
| s4 | השפעה (`impact`) | 7/7 | 0.3636 | 0.3636 | 0.1852 | 0.4286 | 6 | 3,835 |
| s4 | נימוק (`rationale`) | 6/6 | 0.0 | 0.0 | 0.0 | 0.0 | 4 | 3,736 |
| s4 | זמני (`temporal`) | 6/6 | 0.4241 | 0.4241 | 0.7281 | 0.8333 | 5 | 564 |
| s4 | עקיבוּת (`traceability`) | 7/7 | 0.1869 | 0.1869 | 0.6429 | 0.7143 | 5 | 650 |
| s5 | גלובלי (`global`) | 6/6 | 0.1888 | 0.1888 | 0.2667 | 1.0 | 120 | 3,727 |
| s5 | השפעה (`impact`) | 0/7 | — | — | — | — | — | — |
| s5 | נימוק (`rationale`) | 0/6 | — | — | — | — | — | — |
| s5 | זמני (`temporal`) | 0/6 | — | — | — | — | — | — |
| s5 | עקיבוּת (`traceability`) | 0/7 | — | — | — | — | — | — |
| s6 | גלובלי (`global`) | 0/6 | — | — | — | — | — | — |
| s6 | השפעה (`impact`) | 1/7 | 0.0909 | 0.0909 | 0.0556 | 1.0 | 15 | 3,927 |
| s6 | נימוק (`rationale`) | 6/6 | 0.167 | 0.167 | 0.0979 | 1.0 | 4 | 3,718 |
| s6 | זמני (`temporal`) | 5/6 | 0.8133 | 0.7511 | 0.9467 | 1.0 | 4 | 1,284 |
| s6 | עקיבוּת (`traceability`) | 6/7 | 0.3022 | 0.2837 | 0.1592 | 1.0 | 8 | 1,430 |

### עלות לפי אסטרטגיה

`n/a` = האסטרטגיה לא חלה על השאלה, עם סיבה — תא ריק שאיש לא יכול להסביר גרוע ממספר נמוך.

| אסטרטגיה | ריצות | n/a | latency p50 | latency p95 | tokens p50 | tokens סה"כ | tool calls | Cypher | זמן סוכן (מצב B בלבד) | סיבות ל-n/a |
|---|---|---|---|---|---|---|---|---|---|---|
| s1 | 32 | 0 | 150 | 243 | 3,761 | 117,301 | 32 | 95 | — | — |
| s1r | 32 | 0 | 1,792 | 3,855 | 3,794 | 118,653 | 32 | 95 | — | — |
| s2 | 32 | 0 | 243 | 320 | 3,892 | 127,662 | 32 | 96 | — | — |
| s3 | 32 | 0 | 240 | 447 | 2,648 | 62,862 | 32 | 180 | — | — |
| s4 | 32 | 0 | 5 | 18 | 1,279 | 69,187 | 32 | 32 | — | — |
| s5 | 6 | 26 | 120 | 197 | 3,727 | 22,483 | 6 | 6 | — | S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'keys') and is not typed `global` (14), S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'default') and is not typed `global` (4), S5 reduces community reports into a theme; this question carries no thematic signal (router rule 'changed-in') and is not typed `global` (2) · ועוד 6 סיבות ב-JSON |
| s6 | 18 | 14 | 6 | 52 | 3,718 | 47,612 | 18 | 56 | — | temporal wording but no key/date/version pair to bind (14) |

> זמן סוכן — לא נמדד ב-POC; ב-Plan 3 נמדד רק latency של כלים.

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
| changed | s3 | cq13 / cq19 | 0.0667 / 0.0667 | 1 / 1 | 1.0 | 1 |
| changed | s4 | cq13 / cq19 | 0.8667 / 0.8667 | 19 / 19 | 1.0 | 10 |
| changed | s5 | cq13 / cq19 | — / — | 0 / 0 | — | 0 |
| changed | s6 | cq13 / cq19 | 0.8667 / 0.8667 | 15 / 15 | 1.0 | 10 |
| tests | s1 | cq01 / cq16 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| tests | s1r | cq01 / cq16 | 0.0 / 0.0 | 10 / 10 | 0.4286 | 6 |
| tests | s2 | cq01 / cq16 | 0.0 / 0.0 | 9 / 7 | 0.3333 | 4 |
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

מקור: `data/reports/eval_answers.json` · נמדד ב-2026-09-17T10:13:04+00:00 · sha `4cf28f9a`. **STALE** — סעיפים ישנים: answers_build, answers_merge, judge_build.

שיפוט עיוור לפי רובריקה 0–2 על ארבעה מדדים (§5.4). 229 שיפוטים על 190 מקרים, 143 השוואות pairwise, 0 נדחו. התשובות: 184 התקבלו מתוך 184 מקרים שנבנו.

### מטריצה: אסטרטגיה × סוג שאלה (ממוצעי השופטים)

| אסטרטגיה | סוג שאלה | מקרים | נאמנות להקשר | נכונוּת מול gold | תקינות ציטוט | רלוונטיות |
|---|---|---|---|---|---|---|
| s1 | גלובלי (`global`) | 5 | 2.0 | 1.0 | 2.0 | 1.6 |
| s1 | השפעה (`impact`) | 6 | 2.0 | 0.667 | 2.0 | 1.333 |
| s1 | נימוק (`rationale`) | 5 | 2.0 | 0.2 | 2.0 | 0.9 |
| s1 | זמני (`temporal`) | 4 | 2.0 | 0.0 | 2.0 | 0.375 |
| s1 | עקיבוּת (`traceability`) | 6 | 2.0 | 0.5 | 2.0 | 0.833 |
| s1r | גלובלי (`global`) | 6 | 2.0 | 1.0 | 2.0 | 1.917 |
| s1r | השפעה (`impact`) | 7 | 1.857 | 0.429 | 1.857 | 1.286 |
| s1r | נימוק (`rationale`) | 6 | 2.0 | 0.5 | 2.0 | 1.083 |
| s1r | זמני (`temporal`) | 4 | 2.0 | 0.0 | 2.0 | 0.25 |
| s1r | עקיבוּת (`traceability`) | 6 | 2.0 | 0.5 | 2.0 | 1.0 |
| s2 | גלובלי (`global`) | 6 | 2.0 | 1.0 | 2.0 | 1.833 |
| s2 | השפעה (`impact`) | 6 | 2.0 | 0.417 | 2.0 | 1.333 |
| s2 | נימוק (`rationale`) | 6 | 2.0 | 0.5 | 2.0 | 0.833 |
| s2 | זמני (`temporal`) | 6 | 2.0 | 0.333 | 2.0 | 0.5 |
| s2 | עקיבוּת (`traceability`) | 6 | 2.0 | 0.25 | 2.0 | 0.667 |
| s3 | גלובלי (`global`) | 6 | 2.0 | 0.667 | 2.0 | 1.667 |
| s3 | השפעה (`impact`) | 7 | 2.0 | 0.286 | 2.0 | 0.5 |
| s3 | נימוק (`rationale`) | 6 | 2.0 | 0.667 | 2.0 | 1.5 |
| s3 | זמני (`temporal`) | 6 | 2.0 | 0.167 | 2.0 | 0.583 |
| s3 | עקיבוּת (`traceability`) | 7 | 2.0 | 0.571 | 2.0 | 1.0 |
| s4 | גלובלי (`global`) | 6 | 2.0 | 0.0 | 2.0 | 0.167 |
| s4 | השפעה (`impact`) | 6 | 2.0 | 1.167 | 2.0 | 2.0 |
| s4 | נימוק (`rationale`) | 6 | 2.0 | 1.0 | 2.0 | 1.667 |
| s4 | זמני (`temporal`) | 6 | 2.0 | 1.417 | 2.0 | 1.667 |
| s4 | עקיבוּת (`traceability`) | 6 | 2.0 | 1.167 | 2.0 | 1.667 |
| s5 | גלובלי (`global`) | 6 | 2.0 | 1.0 | 2.0 | 2.0 |
| s6 | השפעה (`impact`) | 1 | 2.0 | 0.0 | 2.0 | 1.0 |
| s6 | נימוק (`rationale`) | 6 | 2.0 | 0.0 | 2.0 | 0.417 |
| s6 | זמני (`temporal`) | 5 | 2.0 | 1.8 | 2.0 | 2.0 |
| s6 | עקיבוּת (`traceability`) | 6 | 2.0 | 0.5 | 2.0 | 1.167 |
| agentic | גלובלי (`global`) | 1 | — | 2.0 | 1.0 | 2.0 |
| agentic | השפעה (`impact`) | 5 | — | 1.0 | 1.2 | 1.8 |
| agentic | נימוק (`rationale`) | 4 | — | 1.25 | 1.25 | 2.0 |
| agentic | זמני (`temporal`) | 4 | — | 1.875 | 1.375 | 2.0 |
| agentic | עקיבוּת (`traceability`) | 5 | — | 1.4 | 1.6 | 2.0 |

### לפי שפת השאלה

| שפה | מקרים | נאמנות להקשר | נכונוּת מול gold | תקינות ציטוט | רלוונטיות |
|---|---|---|---|---|---|
| אנגלית | 128 | 1.991 | 0.664 | 1.922 | 1.223 |
| עברית | 62 | 2.0 | 0.742 | 1.944 | 1.306 |

### pairwise מול ה-baseline (`s1r`)

אותה שאלה, שתי תשובות, בסדר אקראי, בלי שם אסטרטגיה. `win_rate` סופר תיקו כאי-ניצחון; `both_wrong` מדווח בנפרד כי שתי תשובות שגויות שמסכימות אינן תיקו.

| מתמודדת | מקרים | ניצחונות | הפסדים | תיקו | שתיהן שגויות | win rate | בלי תיקו |
|---|---|---|---|---|---|---|---|
| s2 | 40 | 4 | 16 | 20 | 25 | 0.1 | 0.2 |
| s3 | 36 | 10 | 16 | 10 | 20 | 0.278 | 0.385 |
| s4 | 40 | 25 | 14 | 1 | 4 | 0.625 | 0.641 |
| s5 | 7 | 6 | 1 | 0 | 0 | 0.857 | 0.857 |
| s6 | 20 | 6 | 7 | 7 | 10 | 0.3 | 0.462 |

### הסכמה בין השופטים

נמדדת רק על המקרים ששני shards שונים ניקדו: 39 מקרים חופפים.

| מדד | הושוו | זהה | בהפרש ≤1 | % זהה | % ≤1 | הפרש ממוצע |
|---|---|---|---|---|---|---|
| נאמנות להקשר | 31 | 31 | 31 | 100.0 | 100.0 | 0.0 |
| נכונוּת מול gold | 39 | 35 | 39 | 89.74 | 100.0 | 0.103 |
| תקינות ציטוט | 39 | 34 | 39 | 87.18 | 100.0 | 0.128 |
| רלוונטיות | 39 | 32 | 39 | 82.05 | 100.0 | 0.179 |

### שופט מול בדיקת הקוד (ציטוטים)

בדיקת הקוד בינארית: כל סוגר מרובע מצביע על משהו שהאחזור החזיר ושהגרף מחזיק. מתוך 190 מקרים שהושוו, 182 בהסכמה ו-8 בסתירה (4.21%). הסתירה נרשמת רק בקצוות — 1 של השופט מתיישב עם שתי ההכרעות.

| מדד | ערך |
|---|---|
| מקרים שנבדקו בקוד | 203 |
| ציטוטים תקפים (קוד) | 194 |
| ציטוטים פסולים (קוד) | 9 |
| תשובות בלי ציטוט | 24 |
| ציטוט שלא היה בהקשר | 0 |
| ציטוט שאין לו צומת בגרף | 8 |

| מקרה | ציון השופט | הכרעת הקוד | למה |
|---|---|---|---|
| cq07.s1 | 2.0 | invalid | not in the graph: 67c951b7a1ccdb02840f4479b6e524366e084074 |
| cq12.s5 | 2.0 | invalid | not in the graph: community:L0-1436 |
| q001.s6 | 2.0 | invalid | not in the graph: 5ae2682d21ea04b3b51ef100fe578899a54a889a, f594551e9aa0262cf02487ddae1f1fe0d08b7e2c, 4ce0cad10259d6a221244349dd55201819725d8f, 5c14e151415eb066752bd8fa497aed9a02475403, 1cc8287579ecaaf87f2a7e0280f785033cb29943 |
| q101.s6 | 2.0 | invalid | not in the graph: b87ae266dc88cffaed59d93ecd91a5fb0a8fa68e |
| q107.s5 | 2.0 | invalid | not in the graph: community:L1-377 |
| q004.s5 | 2.0 | invalid | not in the graph: community:L0-1352 |
| cq06.s4 | 2.0 | invalid | the answer carries no citation |
| cq08.s6 | 2.0 | invalid | not in the graph: ff6c22e1e8a2b38c524283d4232232ae7c0dfb5c, e8ec9fd6862ff845ceeab3ba4438ca905f359c4d, aeae135ded42a7e59705cf2a75648b23587c257c, badb10e3f86aac918af287a3421c6d19d2abca35 |

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

> Layer 3 of spec §5.2. A case judged twice contributes the mean of its two judgments, once. `*_n` is how many cases carried that metric — faithfulness is null on agentic cases, which recorded no context.

## עדכון אינקרמנטלי (§5.5)

מקור: `data/reports/incremental.json` · נמדד ב-2026-09-17T10:12:44+00:00 · sha `4cf28f9`.

פרוסה `incremental` ממקור `jira` מאז 2026-01-01: 10 פריטים, 39.65 שניות בסך הכול. שורה לכל שלב בפייפליין — שלב בלי מדידה נשאר בטבלה ומסומן, ולא נעלם.

| שלב | פקודה | שניות | נמדד | הערות |
|---|---|---|---|---|
| harvest | uv run brain harvest --since 2026-01-01 --source jira --limit 10 --slice incremental | 1.75 | כן | exactly the ten keys the probe chose; the base pull's checkpoint (signature 5da028abe2be, 1416 records, stamped 2026-09-03) was not touched. Jira now matches 433 rather than the probe's 432 — one issue was created in between; ordering by created ASC is what keeps the ten oldest the same ten. |
| canon | uv run brain canon | 10.36 | כן | ran over all three sources (`brain canon`), not `--source jira` as the runbook line says — a superset, and the stronger byte-identity proof. All five base-row sha1 equal data/reports/modularity.json's baseline; the whole-file sha1 changed only for workitems.jsonl (+10) and persons.jsonl (+4). Second run = same bytes in all five. |
| load | uv run brain load | 8.63 | כן | 73 nodes created (10 WorkItem + 59 StatusChange + 4 Person), 168 relationships. The first load after the slice work also SET `slice` on the 25,843 existing loader-owned nodes, which is most of the 332,627 properties_set. Second run created 0 nodes and 0 relationships. 8 new dangling issue refs (8217 -> 8225): the ten point at issues outside the corpus. |
| chunk | uv run brain chunk --kinds all | 7.63 | כן | 71 new chunks, all embedded in 5.18s (13.7 chunks/s, 5297 real tokens/s, batch 64, timeout 60s). Only 43 of them are incremental (10 descriptions + 33 comments); the other 28 are KIP sections of 4 base Documents the ten issues pulled into scope (kip_keys_referenced 267 -> 272). Second run: 0 requested, 0 written, 12,986 skipped. |
| extract build | uv run brain extract build --shards 1 --batch-size 25 --slice incremental | 1.27 | כן | 7 of the 10 descriptions qualify for Phase A: KAFKA-20042 (Task, 42 chars) and KAFKA-20056 (New Feature, 118 chars) are under the 300-character floor, and KAFKA-20045 is a Test that names no KIP. 0 KIP sections, because no Document is in the incremental slice. One batch, 8,020 bytes, indented. Rerun left the file untouched. |
| extract merge | uv run brain extract merge --slice incremental | 0.91 | כן | 29 entities / 29 mentions / 6 relations, 0 rejected, provenance complete on all 26 minted entities and all 35 edges. 26 entities were minted and 3 attached to entities the base corpus already had — Technology\|AdminApiDriver, Technology\|Kafka-clients and Technology\|lz4-java — which is the increment joining the corpus rather than sitting beside it. Rerun: 0 nodes, 0 relationships. The corpus's 8 shards, their ledger and the `merge` section of data/reports/extract.json were not touched. Re-run after the provenance fix (4cf28f9): same 29/29/6, 0 rejected, and the three base entities now carry Plan 1 provenance AND the increment's. The rollback dry run lists 26 entities, not 29. |
| resolve tier 1 | uv run brain resolve --kinds person --tier 1 ; uv run brain resolve --kinds entity --tier 1 | 2.39 | כן | person tier 1 merged 1 of the 4 new identities (Person 1,218 -> 1,217, 3 left carrying slice=incremental: jira:leejiyo, jira:shubest, jira:ssikka). entity tier 1 merged 0 — it links on KIP titles and no new KIP arrived. Ledger now 974 person rows and 199 entity rows; the one merge this step made is in `runs[]`, which is where a rerun cannot overwrite it. Tiers 2 and 3 deliberately not run. |
| communities build | uv run brain communities build | 5.07 | כן | the number of this step, and it is not the one the runbook expected. Same command, same seed 42, same gamma 1.0, on a graph 0.3% larger (+36 projected nodes): 1,297 communities became 1,066, 645 member_hash changed (129 at level 0, 516 at level 1), 652 communities were deleted and 421 created. The carry rule saved 124 reports (82 by hash + 42 copied from the other level) and dropped 62. Leiden's own hierarchy flattened: levels 0 and 1 now hold 533 communities each and the build warns they are 0.0% apart. A read-only probe of --level-indices 2,3 and 2,4 does not bring Plan 1's 762/535 shape back (544/533), so this is the partition of the new graph, not a level-selection artifact. |
| communities batches | uv run brain communities batches --shards 1 | 0.26 | כן | refused with exit 1 and no --force: Plan 1's two shards hold 47 finished batches each, and repacking would repoint them. Measured read-only instead — 33 communities (16 at level 0, 17 at level 1) are >= 25 members and carry no report, which is what --force would pack. Two orders below the 148 the runbook feared, but not the single digit it hoped for, and the repartition is why. |
| communities merge | (not run) | 0.0 | כן | deliberately not run: the planner held the summarizers back until the partition above is a decision rather than a side effect. |
| index | uv run brain index --check-gate | 0.9 | כן | 58,622 -> 58,560 nodes (+169 new, -231 communities) and 161,985 -> 162,330 edges. 12/12 indexes ONLINE, 0 LLM edges without provenance, gate unchanged at 11/13 with the same two non-passes. Community report coverage fell from 14.34% of communities / 89.41% of members to 11.63% / 47.5% — the cost of the repartition, paid in Plan 1 agent work. |

### מה נוסף לגרף

| חתך | ערך |
|---|---|
| גרף | slice_census: nodes_with_slice_by_label: Area:base: 24, Chunk:None: 13,846, Commit:base: 6,107, Community:None: 1,297, Component:base: 30, Document:base: 1,391, Entity:None: 9,038, File:None: 8,594, IndexMeta:None: 4, Person:base: 1,214, Person:incremental: 4, PullRequest:base: 6,026, Space:base: 1, Sprint:base: 92, StatusChange:base: 7,607, StatusChange:incremental: 59, Version:base: 86, WorkItem:base: 3,265, WorkItem:incremental: 10, incremental_nodes_by_label: Person: 4, StatusChange: 59, WorkItem: 10, nodes_without_slice_by_label: Chunk: 13,846, Community: 1,297, Entity: 9,038, File: 8,594, IndexMeta: 4, incremental_workitem_keys: KAFKA-20035, KAFKA-20040, KAFKA-20042, KAFKA-20043, KAFKA-20045, KAFKA-20046, KAFKA-20053, KAFKA-20056, KAFKA-20064, KAFKA-20065, chunks_by_slice: None: 13,846, census_after_part1: at: 2026-09-17T09:35:09+00:00, duration_s: 2.63, exit_code: 1, nodes_total: before: 58,622, after: 58,766, edges_total: before: 161,985, after: 162,224, nodes_by_label_delta: Chunk: 71, Person: 4, StatusChange: 59, WorkItem: 10, WorkItem/Bug: 5, WorkItem/Improvement: 2, WorkItem/JiraTest: 1, WorkItem/NewFeature: 1, WorkItem/Task: 1, edges_by_type_delta: AFFECTS_VERSION: 3, ASSIGNED_TO: 10, COMMENTED: 33, FIX_VERSION: 10, HAS_CHANGE: 59, HAS_CHUNK: 71, IN_COMPONENT: 19, LINKS_TO: 2, MENTIONS_PERSON: 9, REFERENCES: 13, REPORTED_BY: 10, chunks: before: chunks: 13,846, live: 12,915, embedded: 13,846, missing_embedding: 0, orphaned: 931, orphaned_by_kind: description: 173, section: 758, without_has_chunk: 0, by_kind: comment: 3,629, description: 3,423, message: 4,150, section: 1,713, by_parent_kind: Commit: 4,150, Document: 1,713, WorkItem: 7,052, by_lang: en: 12,915, has_chunk_edges: Document: 2,471, WorkItem: 7,225, Commit: 4,150, has_chunk_total: 13,846, present: כן, pct_embedded: 100.0, live_embedded: 12,915, pct_live_embedded: 100.0, after: chunks: 13,917, live: 12,986, embedded: 13,917, missing_embedding: 0, orphaned: 931, orphaned_by_kind: description: 173, section: 758, without_has_chunk: 0, by_kind: comment: 3,662, description: 3,433, message: 4,150, section: 1,741, by_parent_kind: Commit: 4,150, Document: 1,741, WorkItem: 7,095, by_lang: en: 12,986, has_chunk_edges: Document: 2,499, WorkItem: 7,268, Commit: 4,150, has_chunk_total: 13,917, present: כן, pct_embedded: 100.0, live_embedded: 12,986, pct_live_embedded: 100.0, gate_before: issues: PASS, kips_embedded: PASS, kips_referenced_all_embedded: PASS, commits: PASS, person_resolution_precision: PASS, person_resolution_recall: FAIL, entity_resolution_precision: PASS, entity_resolution_recall: PASS, llm_edges_without_provenance: PASS, all_indexes_online: PASS, make_smoke_green: STALE, lessons_01_09: PASS, progress_complete: PASS, gate_after: issues: PASS, kips_embedded: PASS, kips_referenced_all_embedded: PASS, commits: PASS, person_resolution_precision: PASS, person_resolution_recall: FAIL, entity_resolution_precision: PASS, entity_resolution_recall: PASS, llm_edges_without_provenance: PASS, all_indexes_online: PASS, make_smoke_green: STALE, lessons_01_09: PASS, progress_complete: PASS, note: measured after `brain extract build`; the pipeline's own `index` step (12) has not run — the extractor agent, merge, resolve and communities come first., nodes_by_label_before: WorkItem: 3,265, Document: 1,391, Person: 1,214, Commit: 6,107, PullRequest: 6,026, File: 8,594, StatusChange: 7,607, Component: 30, Version: 86, Sprint: 92, Area: 24, Space: 1, Chunk: 13,846, Entity: 9,038, Community: 1,297, IndexMeta: 4, WorkItem/Bug: 774, WorkItem/Epic: 143, WorkItem/Feature: 121, WorkItem/Improvement: 314, WorkItem/JiraTest: 123, WorkItem/NewFeature: 45, WorkItem/Story: 232, WorkItem/SubTask: 250, WorkItem/Task: 302, WorkItem/Test: 817, WorkItem/TestExecution: 78, WorkItem/TestPlan: 13, WorkItem/TestSet: 44, WorkItem/Wish: 9, nodes_by_label_after: WorkItem: 3,275, Document: 1,391, Person: 1,218, Commit: 6,107, PullRequest: 6,026, File: 8,594, StatusChange: 7,666, Component: 30, Version: 86, Sprint: 92, Area: 24, Space: 1, Chunk: 13,917, Entity: 9,038, Community: 1,297, IndexMeta: 4, WorkItem/Bug: 779, WorkItem/Epic: 143, WorkItem/Feature: 121, WorkItem/Improvement: 316, WorkItem/JiraTest: 124, WorkItem/NewFeature: 46, WorkItem/Story: 232, WorkItem/SubTask: 250, WorkItem/Task: 303, WorkItem/Test: 817, WorkItem/TestExecution: 78, WorkItem/TestPlan: 13, WorkItem/TestSet: 44, WorkItem/Wish: 9, gate_values_delta: issues: before: 1,416, after: 1,426, kips_embedded: before: 267, after: 271, kips_referenced_all_embedded: before: 267/267, after: 271/271, index_before_path: data/reports/index.before-incremental.json, validate_incremental_remaining: —, census_after: before: 58,622, after: 58,560, edges: before: 161,985, after: 162,330, rollback_dry_run: command: uv run brain reset --slice incremental   (no --yes = dry run), exit_code: 1, duration_s: 0.95, applied: לא, output: reset (slice:incremental) — DRY RUN,   the pipeline must be IDLE: a harvest, load, chunk, extract or resolve running against this graph or data dir will write into a half-deleted state.,   incremental would delete       4 records from persons.jsonl,   incremental would delete      10 records from workitems.jsonl,   incremental would delete      43 :Chunk nodes,   incremental would delete       3 :Person nodes,   incremental would delete      59 :StatusChange nodes,   incremental would delete      10 :WorkItem nodes,   incremental would delete      29 entities whose every evidence chunk is in this slice,   incremental would delete 1 resolution-ledger row(s) pointing at a deleted identity,   incremental kept data/raw/<source>/since-*/ is kept; `brain canon` would restore the records from it. Add --data to remove the raw pages too.,   incremental kept Community nodes are kept: the increment changed their membership and only `brain communities build` can put it back.,   census     still present: {'WorkItem': 3275, 'Document': 1391, 'Person': 1217, 'Commit': 6107, 'PullRequest': 6026, 'File': 8594, 'StatusChange': 7666, 'Component': 30, 'Version': 86, 'Sprint': 92, 'Area': 24, 'Space': 1, 'Chunk': 13917, 'IndexMeta': 4, 'Entity': 9064, 'Community': 1066}, reset: nothing was deleted — pass --yes to go ahead., rollback_dry_run_after_fix: command: uv run brain reset --slice incremental   (no --yes = dry run), exit_code: 1, duration_s: 0.47, applied: לא, output: reset (slice:incremental) — DRY RUN,   the pipeline must be IDLE: a harvest, load, chunk, extract or resolve running against this graph or data dir will write into a half-deleted state.,   incremental would delete       4 records from persons.jsonl,   incremental would delete      10 records from workitems.jsonl,   incremental would delete      43 :Chunk nodes,   incremental would delete       3 :Person nodes,   incremental would delete      59 :StatusChange nodes,   incremental would delete      10 :WorkItem nodes,   incremental would delete      26 entities whose every evidence chunk is in this slice,   incremental would delete 1 resolution-ledger row(s) pointing at a deleted identity,   incremental kept data/raw/<source>/since-*/ is kept; `brain canon` would restore the records from it. Add --data to remove the raw pages too.,   incremental kept Community nodes are kept: the increment changed their membership and only `brain communities build` can put it back.,   census     still present: {'WorkItem': 3275, 'Document': 1391, 'Person': 1217, 'Commit': 6107, 'PullRequest': 6026, 'File': 8594, 'StatusChange': 7666, 'Component': 30, 'Version': 86, 'Sprint': 92, 'Area': 24, 'Space': 1, 'Chunk': 13917, 'IndexMeta': 4, 'Entity': 9064, 'Community': 1066}, reset: nothing was deleted — pass --yes to go ahead. |
| chunks | — |
| ישויות | entities_total_before: 9,038, entities_total_after: 9,064, entities_minted_here: 26, entities_attached_to_an_existing_one: 3, attached_to: Technology\|AdminApiDriver, Technology\|Kafka-clients, Technology\|lz4-java, minted_by_kind: Decision: 1, Feature: 2, Problem: 12, Risk: 2, Technology: 9, edges_written_by_this_batch: DECIDES: 1, DEPENDS_ON: 1, INTRODUCES_RISK: 2, MENTIONS: 29, MOTIVATED_BY: 2, provenance_complete_entities: 26/26, provenance_complete_edges: 35/35, batch_id_collision: by_run: corpus_run: 61, incremental_run: 26, note: the incremental batch is ALSO called shard-01/001 — batch ids are unique inside a root, not across roots, so provenance cannot tell the corpus's first batch from the increment's without `extracted_at`. Reported, not fixed: the fix is to prefix the id with the slice in build.PlannedBatch.batch_id., names_in_batch: 29, provenance_overwrite_bug: what: `brain extract merge` OVERWRITES an existing entity's `evidence_chunk_ids`, `batch_id` and `batch_ids` with only the batches of the current run, instead of unioning them. `extracted_at` survives (ON CREATE only), which is how the damage is visible at all., why_it_never_showed: a full-corpus merge re-plans every batch, so the current run's aggregate IS the whole truth and overwrite == union. `--slice` is the first partial merge this pipeline has ever run., damage_in_the_live_graph: 3, damaged: entity: Technology\|AdminApiDriver, was_extracted: 2026-09-06T15:11:02+00:00 from corpus batch shard-04/001, evidence chunk c4719fbad58f027f3c61ee88de55bd94d4211e2e, now_claims: batch_id shard-01/001, evidence [035f6cfcd9a3b837bcdbde447a8aae3606780021] — the description chunk of KAFKA-20064, entity: Technology\|lz4-java, was_extracted: 2026-09-06T15:17:28+00:00 from corpus batch shard-05/003, evidence chunk 334e0a6688517e865ce18b5f64f707d47b7d1f25, now_claims: batch_id shard-01/001, evidence [cef5803e5fe4d03b78b5c4ddb549174f75eb6287] — the description chunk of KAFKA-20043, entity: Technology\|Kafka-clients, was_extracted: 2026-09-06T15:13:47+00:00 (corpus batch not located by name grep; the entity predates this run), now_claims: batch_id shard-01/001, evidence [cef5803e5fe4d03b78b5c4ddb549174f75eb6287], consequence_1: provenance: three base entities cite text that did not produce them. conventions rule 3 says every LLM-derived node carries its evidence; these now carry somebody else's., consequence_2: rollback: `brain reset --slice incremental --yes` deletes entities whose every evidence chunk is in the slice. All 29 now qualify, including these 3 base entities — so the rollback would NOT return the census to index.before-incremental.json, it would go 3 entities below it., made_worse_by: batch ids are not unique across roots: the incremental batch is also `shard-01/001`, so even `batch_ids` cannot show that two different batches contributed., fix: two changes, neither applied here: (1) union `evidence_chunk_ids`/`batch_ids` onto what the node already carries instead of replacing them; (2) prefix a sliced build's batch id with the slice (brain/extract/build.PlannedBatch.batch_id) so the two contributions are distinguishable. Both are design decisions about provenance, which is why they are reported rather than taken., repair_of_the_three: re-running `brain extract merge` (no --slice) over the corpus root restores their base evidence — and then overwrites the incremental contribution, which is the same bug from the other side. They need the union fix first., repair: why: run_merge has no per-batch selector, so corpus batches shard-04/001 and shard-05/003 could not be re-merged alone. Every value written here was read out of the committed .out.json of the batch that minted the entity., rows: key: Technology\|adminapidriver, batch_id: shard-04/001, batch_ids: shard-04/001, shard: shard-04, chunks: c4719fbad58f027f3c61ee88de55bd94d4211e2e, key: Technology\|kafka client, batch_id: shard-05/002, batch_ids: shard-05/002, shard-05/003, shard: shard-05, chunks: 334e0a6688517e865ce18b5f64f707d47b7d1f25, 4d1128bc60b3e98a8c44a49dd1a3332d391feb16, 7a76976b4488f76c59ae6bf5a87b1356a8894e88, key: Technology\|lz4 java, batch_id: shard-05/003, batch_ids: shard-05/003, shard: shard-05, chunks: 334e0a6688517e865ce18b5f64f707d47b7d1f25, before: id: Technology\|adminapidriver, batch_id: shard-04/001, batch_ids: shard-04/001, ev: c4719fbad58f027f3c61ee88de55bd94d4211e2e, id: Technology\|kafka client, batch_id: shard-01/001, batch_ids: shard-01/001, ev: cef5803e5fe4d03b78b5c4ddb549174f75eb6287, id: Technology\|lz4 java, batch_id: shard-01/001, batch_ids: shard-01/001, ev: cef5803e5fe4d03b78b5c4ddb549174f75eb6287, after: id: Technology\|adminapidriver, batch_id: shard-04/001, batch_ids: shard-04/001, ev: c4719fbad58f027f3c61ee88de55bd94d4211e2e, id: Technology\|kafka client, batch_id: shard-05/002, batch_ids: shard-05/002, shard-05/003, ev: 334e0a6688517e865ce18b5f64f707d47b7d1f25, 4d1128bc60b3e98a8c44a49dd1a3332d391feb16, 7a76976b4488f76c59ae6bf5a87b1356a8894e88, id: Technology\|lz4 java, batch_id: shard-05/003, batch_ids: shard-05/003, ev: 334e0a6688517e865ce18b5f64f707d47b7d1f25, counters: nodes_created: 0, nodes_deleted: 0, relationships_created: 0, relationships_deleted: 0, properties_set: 12, labels_added: 0, labels_removed: 0, indexes_added: 0, constraints_added: 0, next: re-merge the incremental root: the union must add the slice's chunk and batch back on top of these, without losing them., union_remerge: first: exit_code: 0, duration_s: 1.06, entities: id: Technology\|adminapidriver, batch_id: shard-04/001, batch_ids: incremental/shard-01/001, shard-04/001, ev: 035f6cfcd9a3b837bcdbde447a8aae3606780021, c4719fbad58f027f3c61ee88de55bd94d4211e2e, id: Technology\|kafka client, batch_id: shard-05/002, batch_ids: incremental/shard-01/001, shard-05/002, shard-05/003, ev: 334e0a6688517e865ce18b5f64f707d47b7d1f25, 4d1128bc60b3e98a8c44a49dd1a3332d391feb16, 7a76976b4488f76c59ae6bf5a87b1356a8894e88, cef5803e5fe4d03b78b5c4ddb549174f75eb6287, id: Technology\|lz4 java, batch_id: shard-05/003, batch_ids: incremental/shard-01/001, shard-05/003, ev: 334e0a6688517e865ce18b5f64f707d47b7d1f25, cef5803e5fe4d03b78b5c4ddb549174f75eb6287, rerun: exit_code: 0, duration_s: 0.9, counters: nodes_created: 0, nodes_deleted: 0, relationships_created: 0, relationships_deleted: 0, properties_set: 4,357, labels_added: 0, labels_removed: 0, indexes_added: 0, constraints_added: 0, written: entities: 29, mentions: 29, relations: 6, arrays_unchanged: כן, entities_total: 9,064, entities_deletable_by_rollback: 26, proof: each of the three carries its Plan 1 batch_id and Plan 1 evidence chunk AND the incremental chunk/batch — the union added, it did not replace., final: exit_code: 0, duration_s: 0.67, batches: found: 1, valid: 1, invalid: 0, expected: 1, envelope_valid_rate: 1.0, envelope_valid_rate_vs_expected: 1.0, reported_failed: 0, missing_count: 1, missing_outputs: shard-01/001, unexpected_outputs: incremental/shard-01/001, previously_merged_now_absent: —, counters: nodes_created: 0, nodes_deleted: 0, relationships_created: 0, relationships_deleted: 0, properties_set: 4,357, labels_added: 0, labels_removed: 0, indexes_added: 0, constraints_added: 0, provenance: required: evidence_chunk_ids, batch_id, model, extracted_at, edges_without_provenance: 0, by_type: — |
| קהילות | member_hash_changed: 645, member_hash_changed_ids: L0-0, L0-11, L0-113, L0-115, L0-130, L0-131, L0-16, L0-169, L0-188, L0-193, L0-196, L0-20, L0-203, L0-216, L0-239, L0-24, L0-245, L0-249, L0-250, L0-251, L0-254, L0-263, L0-266, L0-268, L0-269, L0-27, L0-275, L0-277, L0-280, L0-281, L0-282, L0-288, L0-289, L0-290, L0-297, L0-301, L0-302, L0-304, L0-305, L0-308, L0-309, L0-313, L0-316, L0-322, L0-330, L0-335, L0-337, L0-343, L0-344, L0-345, L0-355, L0-358, L0-361, L0-364, L0-366, L0-370, L0-373, L0-385, L0-387, L0-388, L0-389, L0-390, L0-399, L0-4, L0-40, L0-403, L0-404, L0-406, L0-410, L0-411, L0-415, L0-416, L0-419, L0-420, L0-422, L0-425, L0-428, L0-430, L0-431, L0-440, L0-444, L0-445, L0-446, L0-451, L0-456, L0-458, L0-460, L0-463, L0-465, L0-466, L0-467, L0-468, L0-469, L0-471, L0-476, L0-477, L0-484, L0-485, L0-493, L0-499, L0-50, L0-502, L0-507, L0-511, L0-512, L0-52, L0-529, L0-532, L0-537, L0-541, L0-542, L0-547, L0-56, L0-562, L0-567, L0-569, L0-571, L0-572, L0-59, L0-6, L0-7, L0-76, L0-77, L0-8, L0-82, L0-85, L0-90, L0-92, L0-94, L1-0, L1-1, L1-10, L1-101, L1-102, L1-103, L1-104, L1-105, L1-107, L1-109, L1-11, L1-110, L1-111, L1-112, L1-113, L1-115, L1-116, L1-117, L1-118, L1-119, L1-12, L1-120, L1-121, L1-122, L1-123, L1-124, L1-125, L1-126, L1-127, L1-128, L1-129, L1-13, L1-130, L1-131, L1-132, L1-133, L1-134, L1-136, L1-137, L1-138, L1-139, L1-14, L1-140, L1-141, L1-142, L1-143, L1-144, L1-145, L1-146, L1-147, L1-148, L1-149, L1-15, L1-150, L1-151, L1-152, L1-153, L1-154, L1-155, L1-156, L1-157, L1-158, L1-159, L1-16, L1-160, L1-161, L1-162, L1-163, L1-164, L1-165, L1-166, L1-167, L1-168, L1-169, L1-17, L1-170, L1-171, L1-172, L1-173, L1-174, L1-175, L1-176, L1-177, L1-178, L1-18, L1-180, L1-182, L1-184, L1-185, L1-186, L1-187, L1-188, L1-189, L1-19, L1-191, L1-193, L1-194, L1-195, L1-196, L1-197, L1-198, L1-199, L1-2, L1-20, L1-200, L1-201, L1-202, L1-203, L1-204, L1-205, L1-206, L1-208, L1-209, L1-21, L1-210, L1-211, L1-212, L1-213, L1-214, L1-215, L1-216, L1-217, L1-218, L1-219, L1-22, L1-220, L1-221, L1-222, L1-223, L1-224, L1-225, L1-226, L1-227, L1-228, L1-229, L1-23, L1-230, L1-231, L1-232, L1-233, L1-234, L1-235, L1-236, L1-237, L1-238, L1-239, L1-24, L1-240, L1-241, L1-242, L1-243, L1-244, L1-245, L1-246, L1-247, L1-248, L1-249, L1-25, L1-251, L1-253, L1-254, L1-255, L1-256, L1-257, L1-258, L1-259, L1-26, L1-260, L1-261, L1-262, L1-263, L1-265, L1-266, L1-267, L1-268, L1-269, L1-27, L1-271, L1-272, L1-273, L1-274, L1-275, L1-276, L1-278, L1-279, L1-28, L1-281, L1-282, L1-283, L1-284, L1-285, L1-286, L1-287, L1-288, L1-289, L1-29, L1-290, L1-291, L1-292, L1-293, L1-294, L1-295, L1-296, L1-297, L1-298, L1-299, L1-3, L1-30, L1-300, L1-301, L1-302, L1-303, L1-304, L1-305, L1-306, L1-307, L1-308, L1-309, L1-31, L1-310, L1-311, L1-312, L1-313, L1-314, L1-315, L1-316, L1-317, L1-318, L1-319, L1-32, L1-320, L1-321, L1-322, L1-323, L1-324, L1-325, L1-326, L1-327, L1-328, L1-329, L1-33, L1-330, L1-331, L1-332, L1-333, L1-334, L1-335, L1-336, L1-337, L1-338, L1-339, L1-34, L1-340, L1-342, L1-343, L1-344, L1-345, L1-347, L1-348, L1-349, L1-35, L1-350, L1-351, L1-352, L1-353, L1-354, L1-355, L1-356, L1-357, L1-358, L1-359, L1-36, L1-360, L1-361, L1-362, L1-363, L1-364, L1-366, L1-367, L1-369, L1-37, L1-370, L1-371, L1-372, L1-374, L1-375, L1-376, L1-378, L1-379, L1-38, L1-380, L1-381, L1-383, L1-384, L1-385, L1-386, L1-387, L1-388, L1-389, L1-39, L1-392, L1-393, L1-394, L1-395, L1-396, L1-397, L1-398, L1-4, L1-40, L1-400, L1-403, L1-404, L1-405, L1-406, L1-407, L1-408, L1-409, L1-41, L1-410, L1-412, L1-413, L1-414, L1-415, L1-416, L1-417, L1-418, L1-419, L1-42, L1-420, L1-421, L1-422, L1-423, L1-424, L1-425, L1-426, L1-427, L1-428, L1-429, L1-43, L1-430, L1-431, L1-432, L1-433, L1-434, L1-435, L1-436, L1-437, L1-438, L1-439, L1-44, L1-440, L1-441, L1-442, L1-443, L1-444, L1-445, L1-446, L1-447, L1-448, L1-449, L1-45, L1-450, L1-451, L1-452, L1-453, L1-454, L1-455, L1-456, L1-457, L1-458, L1-459, L1-46, L1-460, L1-461, L1-462, L1-463, L1-464, L1-465, L1-466, L1-467, L1-468, L1-469, L1-47, L1-470, L1-471, L1-472, L1-473, L1-474, L1-475, L1-476, L1-477, L1-478, L1-479, L1-48, L1-480, L1-481, L1-482, L1-483, L1-484, L1-485, L1-486, L1-487, L1-488, L1-489, L1-49, L1-490, L1-491, L1-492, L1-493, L1-494, L1-495, L1-496, L1-497, L1-498, L1-499, L1-5, L1-50, L1-500, L1-501, L1-502, L1-503, L1-504, L1-505, L1-506, L1-507, L1-508, L1-509, L1-510, L1-511, L1-512, L1-513, L1-514, L1-515, L1-516, L1-517, L1-518, L1-519, L1-52, L1-520, L1-521, L1-522, L1-523, L1-524, L1-525, L1-526, L1-527, L1-528, L1-529, L1-53, L1-530, L1-531, L1-532, L1-533, L1-534, L1-535, L1-536, L1-537, L1-538, L1-539, L1-54, L1-540, L1-541, L1-542, L1-543, L1-544, L1-545, L1-546, L1-547, L1-548, L1-549, L1-55, L1-58, L1-59, L1-6, L1-60, L1-61, L1-62, L1-63, L1-64, L1-65, L1-66, L1-67, L1-68, L1-69, L1-7, L1-70, L1-71, L1-72, L1-73, L1-74, L1-75, L1-76, L1-77, L1-78, L1-79, L1-8, L1-80, L1-81, L1-82, L1-83, L1-84, L1-85, L1-86, L1-87, L1-88, L1-89, L1-9, L1-90, L1-91, L1-92, L1-93, L1-95, L1-96, L1-97, L1-98, L1-99, total_before: 1,297, total_after: 1,066, reports_carried_over: 124, reports_copied_from_another_level: 42, reports_dropped: 62, would_summarize: 33, communities_with_a_report: 124, batches_refused_without_force: כן |

### חמש השאלות על הפריטים החדשים

| שאלה | ציטוט תקף | פירוט |
|---|---|---|
| inc1 | כן | — |
| inc2 | כן | — |
| inc3 | כן | — |
| inc4 | כן | — |
| inc5 | כן | — |

> sha b5a0dba is the commit that carries the code every step below was measured on (HEAD when part 1 started). HEAD moved five times during the 30 seconds of pipeline time — three eval agents share this tree — but `git diff --name-only b5a0dba..691e708` touches only brain/eval, brain/retrieve, docs and their tests: nothing under brain/harvest, brain/canon, brain/graph, brain/chunk or brain/extract.

> FIXED in 4cf28f9 — the merge-overwrites-provenance bug this step found, and the colliding batch ids underneath it. `evidence_chunk_ids`/`batch_ids` are unioned, `batch_id`/`shard` are first-seen, and a sliced build's id is `<slice>/shard-NN/NNN`. The three damaged entities were repaired from the committed corpus batch outputs and the slice re-merged: rollback now lists 26 entities. Detail in `entities.provenance_overwrite_bug`, `entities.repair` and `entities.union_remerge`.

> File nodes (8,594) carry neither `synthetic` nor `slice`; the index gate already reports the `synthetic` half as a warning.

> `brain extract merge` reads `data/batches/extract/` and globs `shard-[0-9][0-9]/`, so it will not see the incremental root. Part 2 needs the same `--slice` pass-through in brain/extract/merge.py + runner + CLI before step 7 can run.

> Part 1 stopped after `extract build`, as briefed. Steps 6-12 (kg-extractor, extract merge, resolve tier 1, communities build/batches/merge, index), `communities.member_hash_changed` and the five questions are part 2 — validate_incremental() therefore still reports them.

> `brain chunk --stamp-slice` closed the Chunk.slice gap part 1 reported: 13,846 null -> 0, 43 incremental, rerun stamps 0. The digest check is green again too — brain/modularity.py now digests the base rows.

> COMMUNITIES: the headline risk of this step. `brain communities build` repartitioned the graph: 645 member_hash changed, 62 community reports were dropped, and report coverage fell from 89.4% of members to 47.5%. That is Plan 1 agent work lost to a 0.3% change in node count, and re-summarising the 33 eligible communities needs `brain communities batches --force`, which moves Plan 1's finished .out.json aside. A planner decision, not taken here.

> THE S4 EXAMPLE BANK IS BLIND TO THIS SLICE: every example query it matched carries `WHERE NOT w.status IN ['Resolved','Closed','Done','Completed','Removed']`, and nine of the ten new issues are Resolved. Any mode-A question routed to S4 without a key therefore excludes the increment by construction — which is why inc2 and inc5 scored 0 on every strategy. This is a finding about the bank, not about the questions.

> MODE: the runbook's '>=4 of 5 with a resolved citation' is a mode-B criterion (`brain-analyst` through MCP). What is recorded in `questions[]` is the mode-A fixed sweep, which is retrieval only — 3 of 5. The mode-B run is what the criterion asks for.

> MODE A vs MODE B on the five questions: 3/5 in mode A (fixed retrieval, no agent), 5/5 in mode B (`brain-analyst` over MCP, 70/70 citations valid). The runbook's >=4/5 is the mode-B criterion and it passes. The mode-A failures are a finding about the S4 example bank, not about the questions: every example it matched filters `WHERE NOT w.status IN ['Resolved',...]` and nine of the ten new issues are Resolved, so a keyless question routed to S4 excludes the whole slice by construction. No question was rewritten. Dispatching an LLM-role agent is normally the planner's job (conventions, Roles); this one was dispatched by the ingest engineer with the planner's agreement, and it is noted here rather than left implicit.

> COMMUNITIES left as they are by planner decision: no forced re-summarization. The measured cost stands as a finding for the report's 'what I would change' — Leiden is a global re-partition, so +0.3% nodes moved 645 of 1,297 member_hashes, dropped 62 community reports, and left 33 communities >=25 members without one.

## מתי מה

הטבלה היחידה בדוח שנגזרת ולא מועתקת: לכל סוג שאלה, מי הובילה ב-recall (שכבה 2), מי הובילה בנכונוּת לפי השופט (שכבה 3), ומה המהלך הזה עלה מול ה-baseline (`s1r`). הפרש חיובי = יקר יותר מה-baseline.

| סוג שאלה | מובילה ב-recall | recall | recall של baseline | מובילה בנכונוּת | נכונוּת | נכונוּת baseline | Δ latency p50 (ms) | Δ tokens p50 | Δ Cypher | הערה |
|---|---|---|---|---|---|---|---|---|---|---|
| גלובלי (`global`) | s5 | 0.1888 | 0.0998 | agentic | 2.0 | 1.0 | -1966.0 | 165.0 | -11.0 | — |
| השפעה (`impact`) | s4 | 0.3636 | 0.0476 | s4 | 1.167 | 0.429 | -1403.0 | 41.0 | -14.0 | — |
| נימוק (`rationale`) | s3 | 0.3337 | 0.0727 | agentic | 1.25 | 0.5 | -1503.0 | -1014.0 | 18.0 | — |
| זמני (`temporal`) | s6 | 0.8133 | 0.0833 | agentic | 1.875 | 0.0 | -697.0 | -2103.0 | -8.0 | — |
| עקיבוּת (`traceability`) | s3 | 0.3707 | 0.0839 | agentic | 1.4 | 0.5 | -1869.0 | -2697.0 | 19.0 | — |

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
