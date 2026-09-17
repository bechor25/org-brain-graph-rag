# הערכה — דוח Plan 3

נוצר אוטומטית ע"י `brain eval report` ב-2026-09-17T11:04:16+00:00 (HEAD `c2ce8094`) מתוך דוחות השלבים ב-`data/reports`. **המספרים נלקחים מדוחות השלבים** ואין כאן מספר שהוקלד לתוך הדף; **הערות השלבים הן פרוזה של המהנדס/ת של השלב** (עמודות `הערות`/`note` שנגררות מה-JSON), ושני הסעיפים האחרונים נכתבים ביד ע"י המתכנן ונשמרים בכל יצירה מחדש.

כל שורה בטבלה הבאה היא קלט של הדוח: מתי נמדד, באיזה commit, והאם ה-commit הזה הוא ה-HEAD. `STALE` = המספרים קיימים אבל נמדדו על קוד אחר; `חסר` = הסעיף שמתבסס עליו יופיע כ-**טרם נמדד** עם הפקודה שמייצרת אותו.

| קובץ | מה מביא | מצב | נמדד ב | sha | הפקודה שכותבת | הערה |
|---|---|---|---|---|---|---|
| `data/reports/harvest.json` | שכבה 0 — מה נמשך מהמקורות | עדכני | 2026-09-17T11:02:23+00:00 | c2ce8094 | `brain harvest` | — |
| `data/reports/index.json` | שכבה 1 — מפקד, provenance, קהילות | עדכני | 2026-09-17T11:04:16+00:00 | c2ce8094 | `brain index` | — |
| `data/reports/resolve.json` | שכבה 1 — P/R/F1 של איחוד ישויות | STALE | 2026-09-17T10:16:44+00:00 | 1ef67612 | `brain resolve eval` | נמדד ב-1ef67612, HEAD הוא c2ce8094 |
| `data/reports/retrieve.json` | תשתית האחזור — reranker ו-guard | STALE | 2026-09-17T10:17:35+00:00 | 1ef67612 | `brain cypher-examples check` | נמדד ב-1ef67612, HEAD הוא c2ce8094 |
| `data/reports/eval_questions.json` | סט השאלות | STALE | 2026-09-17T10:17:36+00:00 | 1ef67612 | `brain eval questions merge` | נמדד ב-1ef67612, HEAD הוא c2ce8094 |
| `data/reports/eval_retrieval.json` | שכבה 2 | STALE | 2026-09-17T09:35:39+00:00 | 9dba7252 | `brain eval run --mode fixed` | נמדד ב-9dba7252, HEAD הוא c2ce8094 |
| `data/reports/eval_answers.json` | שכבה 3 — תשובות ושיפוט עיוור | STALE | 2026-09-17T10:58:21+00:00 | 55598fe9 | `brain eval answers merge · brain eval judge merge` | נמדד ב-55598fe9, HEAD הוא c2ce8094 |
| `data/reports/plan2_gate.json` | שער מצב B (ציטוטים) | STALE | 2026-09-17T09:18:15+00:00 | c15b8385 | `brain eval cite-check` | נמדד ב-c15b8385, HEAD הוא c2ce8094 |
| `data/reports/incremental.json` | §5.5 — ריצת העדכון האינקרמנטלי (כל הפייפליין) | STALE | 2026-09-17T10:12:44+00:00 | 4cf28f9 | `brain harvest --since <date> --source jira` | נמדד ב-4cf28f9, HEAD הוא c2ce8094 |

## סט השאלות

מקור: `data/reports/eval_questions.json` · נמדד ב-2026-09-17T10:17:36+00:00 · sha `1ef67612`. **STALE** — נמדד ב-1ef67612, HEAD הוא c2ce8094.

הסט: `data/eval/questions.jsonl` (sha256 `68df8e48c68b9476`), 32 שאלות מתוך יעד 32. רצפת העברית: 11.

`gold source: graph 32 / truth 0`.

> אף שאלה לא נגזרה מ-`synthetic_truth.json` — הזרוע הסינתטית של §5.1 לא נבדקה בפועל, וכל ה-gold מגיע מהגרף עצמו.

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

מקור: `data/reports/harvest.json` · נמדד ב-2026-09-17T11:02:23+00:00 · sha `c2ce8094`.

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

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T11:04:16+00:00 · sha `c2ce8094`.

הגרף שעליו נמדד הכול: **58,560** צמתים ו-**162,330** קשתות (147,534 דטרמיניסטיות, 14,796 מ-LLM). שער Plan 1: לא עבר — 12/13, נכשל: person_resolution_recall. המפקד המלא: `docs/report/plan1-graph-census.md`.

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

מקור: `data/reports/resolve.json` · נמדד ב-2026-09-17T10:16:44+00:00 · sha `1ef67612`. **STALE** — נמדד ב-1ef67612, HEAD הוא c2ce8094.

P/R/F1 מול זוגות הזהב בלבד (יעד 0.85); מיזוגים שהזהב לא אומר עליהם דבר נספרים כ-`ungraded` ולא נכנסים לאף צד.

| סוג | זוגות זהב | P | R | F1 | TP | FP | FN | לפני | אחרי | ungraded (index.json) | ungraded (resolve.json) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| entity | 100 | 1.0 | 0.98 | 0.9899 | 49 | 0 | 1 | 9,237 | 9,064 | 176 | 176 |
| person | 633 | 0.9801 | 0.7789 | 0.868 | 444 | 9 | 126 | 2,187 | 1,217 | 1,657 | 1,657 |

שתי עמודות ה-ungraded אינן שגיאה: המפקד סופר את המיזוגים דרך tier אחד פחות מ-`brain resolve eval`, ולכן הן נבדלות ב-1 עבור `person`. שתיהן מודפסות עם הקובץ שלהן כדי שאיש לא יצטט את אחת מהן כ״המספר״.

> Precision and recall are computed over the labelled gold pairs only. Merges between identities the gold says nothing about (the real Jira / git / Confluence duplicates) are counted as `ungraded_merges` and left out of both, because calling them right or wrong would be a guess.

### שכבה 1 — כיסוי provenance

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T11:04:16+00:00 · sha `c2ce8094`.

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

מקור: `data/reports/index.json` · נמדד ב-2026-09-17T11:04:16+00:00 · sha `c2ce8094`.

**1,066** קהילות, מהן 124 מסוכמות (11.63%). 47.5% מהחברים נמצאים בקהילה מסוכמת — זה הכיסוי ש-S5 (חיפוש גלובלי) יכול לראות בכלל.

| רמה | קהילות | גודל p50 | גודל p95 | חברים | מסוכמות |
|---|---|---|---|---|---|
| 0 | 533 | 1 | 100 | 11,911 | 62 |
| 1 | 533 | 1 | 100 | 11,911 | 62 |

## שכבה 2 — אחזור (דטרמיניסטי)

מקור: `data/reports/eval_retrieval.json` · נמדד ב-2026-09-17T09:35:39+00:00 · sha `9dba7252`. **STALE** — נמדד ב-9dba7252, HEAD הוא c2ce8094.

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

> **gold shares the strategy's code path** — 4 שאלות (`cq13`, `cq14`, `cq15`, `cq19`, סוג: temporal): ה-gold שלהן הוא הפלט של אותה קריאת `brain.retrieve.temporal.*` שהאסטרטגיה מריצה. השורות שלהן בטבלה שלמעלה מודדות דטרמיניזם של הקריאה, לא אחזור.

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
| p50 בלי rerank (ms) | 164 |
| p50 עם rerank (ms) | 1,823 |
| תוספת p50 (ms) | 1,659 |
| guard: נחסמו | 56/56 |
| guard: דלפו | 0 |
| guard: קריאות שעברו | 21/21 |
| guard: LIMIT הוזרק | 9 |
| guard: timeout נאכף (ms) | 1,073 |

> measured, not improved: relevance is judged in Plan 3 (plan decision 5). A 100% top-1 change rate is what an RRF baseline invites: the fusion scores of ten chunks sit within ~0.002 of each other (1/(60+rank)), so any second opinion reorders them. `top1_source_changed` is the stricter number — how often the reranker moved the answer to a different document.

> The matrix is assembled from every run file on disk, not from the strategies of the last invocation, so a partial `--strategies` rerun refreshes one column and leaves the rest standing. Each column in `columns` carries the commit its own runs were measured at — not at HEAD: s1, s1r, s2, s3, s4, s5, s6.

> `recall` counts a chunk of the gold node as a hit (match kind `parent`); `recall_strict` does not. Both are reported so the vector baseline is not scored against an id scheme only the graph strategies emit.

## שכבה 3 — תשובות ושיפוט עיוור

מקור: `data/reports/eval_answers.json` · נמדד ב-2026-09-17T10:58:21+00:00 · sha `55598fe9`. **STALE** — נמדד ב-55598fe9, HEAD הוא c2ce8094.

שיפוט עיוור לפי רובריקה 0–2 על ארבעה מדדים (§5.4). 229 שיפוטים על 190 מקרים, 143 הכרעות pairwise על 120 זוגות, 0 נדחו. **המסלול של מצב A:** 184 מקרים נבנו, 184 תשובות התקבלו במיזוג, 13 נשמטו לפני השיפוט; 190 מקרים נשפטו בפועל (מצב A + מצב B יחד, שכן תשובות מצב B מוזגו בנפרד).

### מטריצה: אסטרטגיה × סוג שאלה (ממוצעי השופטים)

| אסטרטגיה | סוג שאלה | n | נאמנות להקשר | נכונוּת מול gold | תקינות ציטוט | רלוונטיות |
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

### ממוצעים לפי אסטרטגיה

`n` = כמה מקרים נשפטו לאסטרטגיה הזו. המכנים אינם שווים, ולכן הפרש בין שני ממוצעים אינו בהכרח הפרש בין שתי אסטרטגיות.

| אסטרטגיה | n | נאמנות להקשר | נכונוּת מול gold | תקינות ציטוט | רלוונטיות |
|---|---|---|---|---|---|
| s1 | 26 | 2.0 | 0.5 | 2.0 | 1.038 |
| s1r | 29 | 1.966 | 0.517 | 1.966 | 1.172 |
| s2 | 30 | 2.0 | 0.5 | 2.0 | 1.033 |
| s3 | 32 | 2.0 | 0.469 | 2.0 | 1.031 |
| s4 | 30 | 2.0 | 0.95 | 2.0 | 1.433 |
| s5 | 6 | 2.0 | 1.0 | 2.0 | 2.0 |
| s6 | 18 | 2.0 | 0.667 | 2.0 | 1.139 |
| agentic | 19 | — | 1.395 | 1.342 | 1.947 |

**13 מקרים שנענו לא הגיעו לשופט** (s1: 6, s1r: 3, s2: 2, s4: 2). זה ההפרש בין המכנים למעלה, והוא אינו מקרי: זרוע ה-baseline ספגה אותו הכי חזק.

| מקרה | אסטרטגיה | סיבה | פירוט |
|---|---|---|---|
| `cq09.s1` | s1 | `context_drift` | the run file now packs a different context |
| `cq13.s1` | s1 | `context_drift` | the run file now packs a different context |
| `cq16.s1` | s1 | `context_drift` | the run file now packs a different context |
| `cq17.s1` | s1 | `context_drift` | the run file now packs a different context |
| `cq19.s1` | s1 | `context_drift` | the run file now packs a different context |
| `q106.s1` | s1 | `context_drift` | the run file now packs a different context |
| `cq13.s1r` | s1r | `context_drift` | the run file now packs a different context |
| `cq16.s1r` | s1r | `context_drift` | the run file now packs a different context |
| `cq19.s1r` | s1r | `context_drift` | the run file now packs a different context |
| `cq01.s2` | s2 | `context_drift` | the run file now packs a different context |
| `cq17.s2` | s2 | `context_drift` | the run file now packs a different context |
| `cq03.s4` | s4 | `context_drift` | the run file now packs a different context |
| `q103.s4` | s4 | `context_drift` | the run file now packs a different context |

### לפי שפת השאלה

| שפה | n | נאמנות להקשר | נכונוּת מול gold | תקינות ציטוט | רלוונטיות |
|---|---|---|---|---|---|
| אנגלית | 128 | 1.991 | 0.664 | 1.922 | 1.223 |
| עברית | 62 | 2.0 | 0.742 | 1.944 | 1.306 |

### ציטוטים לפי אסטרטגיה (רחב מול strict)

`valid` מקבל ציטוט שמצביע על ההורה של פריט ההקשר; `valid_strict` דורש את המזהה שההקשר באמת הכיל. ההפרש הוא הרגל של S4: לצטט את פריט העבודה של שורה במקום את השורה.

| אסטרטגיה | תשובות | סירובים | ציטוטים | valid | valid strict | % בהקשר | % בהקשר strict |
|---|---|---|---|---|---|---|---|
| s1 | 32 | 11 | 129 | 129 | 129 | 100.0 | 100.0 |
| s1r | 32 | 12 | 134 | 134 | 134 | 100.0 | 100.0 |
| s2 | 32 | 11 | 98 | 98 | 98 | 100.0 | 100.0 |
| s3 | 32 | 16 | 90 | 90 | 90 | 100.0 | 100.0 |
| s4 | 32 | 7 | 192 | 192 | 140 | 100.0 | 72.92 |
| s5 | 6 | 0 | 15 | 15 | 15 | 100.0 | 100.0 |
| s6 | 18 | 9 | 75 | 75 | 75 | 100.0 | 100.0 |

### pairwise מול ה-baseline (`s1r`)

אותה שאלה, שתי תשובות, בסדר אקראי, בלי שם אסטרטגיה. **היחידה היא זוג ולא הכרעה:** 143 הכרעות על 120 זוגות, כי 20% מהזוגות נשפטו פעמיים. זוג ששני השופטים בחרו בו מנצחים שונים נספר כתיקו ומופיע ב"הכרעות מפוצלות" למטה. `win_rate` סופר תיקו כאי-ניצחון; `both_wrong` דורש שכל שופט שראה את הזוג יאמר זאת, ומדווח בנפרד כי שתי תשובות שגויות שמסכימות אינן תיקו.

| מתמודדת | n (זוגות) | הכרעות | ניצחונות | הפסדים | תיקו | מפוצלים | שתיהן שגויות | win rate | בלי תיקו |
|---|---|---|---|---|---|---|---|---|---|
| s2 | 32 | 40 | 3 | 12 | 17 | 5 | 19 | 0.094 | 0.2 |
| s3 | 32 | 36 | 8 | 14 | 10 | 2 | 17 | 0.25 | 0.364 |
| s4 | 32 | 40 | 21 | 10 | 1 | 1 | 3 | 0.656 | 0.677 |
| s5 | 6 | 7 | 5 | 1 | 0 | 0 | 0 | 0.833 | 0.833 |
| s6 | 18 | 20 | 6 | 5 | 7 | 0 | 9 | 0.333 | 0.545 |

הכרעות מפוצלות — שני שופטים, שני מנצחים שונים, לכן תיקו:

| זוג | שאלה | מתמודדת | מי ניצח לפי כל שופט | shards |
|---|---|---|---|---|
| `36fffedb` | q004 | s2 | s1r, tie | shard-01, shard-02 |
| `51edc8e2` | cq11 | s2 | s1r, tie | shard-01, shard-02 |
| `59095c12` | cq05 | s3 | s1r, tie | shard-01, shard-02 |
| `75113b5c` | q008 | s4 | s4, tie | shard-01, shard-02 |
| `84d9ab12` | cq06 | s2 | s2, tie | shard-01, shard-02 |
| `9b4ab5aa` | q109 | s2 | s1r, tie | shard-01, shard-02 |
| `a152cfd1` | cq16 | s2 | s1r, tie | shard-01, shard-02 |
| `ce6ec4ab` | cq09 | s3 | s3, tie | shard-01, shard-02 |

### הסכמה בין השופטים

נמדדת רק על המקרים ששני shards שונים ניקדו: 39 מקרים חופפים.

| מדד | הושוו | זהה | בהפרש ≤1 | % זהה | % ≤1 | הפרש ממוצע |
|---|---|---|---|---|---|---|
| נאמנות להקשר | 31 | 31 | 31 | 100.0 | 100.0 | 0.0 |
| נכונוּת מול gold | 39 | 35 | 39 | 89.74 | 100.0 | 0.103 |
| תקינות ציטוט | 39 | 34 | 39 | 87.18 | 100.0 | 0.128 |
| רלוונטיות | 39 | 32 | 39 | 82.05 | 100.0 | 0.179 |
| מנצח pairwise | 23 | 15 | — | 65.22 | — | — |

השורה האחרונה היא הסולם השני, והיא זו שהטבלה שמעליה נשענת עליה: `win_rate` בנוי על "מי ניצח", שאין לו "בהפרש ≤1". ההסכמה עליו נמוכה מההסכמה על הרובריקה — כלומר דירוג של שתי תשובות פחות יציב מניקוד של אחת.

### שופט מול בדיקת הקוד (ציטוטים)

בדיקת הקוד בינארית: כל סוגר מרובע מצביע על משהו שהאחזור החזיר ושהגרף מחזיק. הסתירה נרשמת רק בקצוות — 1 של השופט מתיישב עם שתי ההכרעות. **הגרף זז בין האחזור לבדיקה** (הפרוסה האינקרמנטלית עשתה re-partition לקהילות), ולכן סתירה שנובעת ממזהה שהאחזור באמת החזיר ושנמחק אחר כך מופרדת מטעות שופט אמיתית.

| פירוק | מקרים |
|---|---|
| הושוו | 190 |
| בהסכמה | 186 |
| בסתירה מול הגרף עכשיו | 4 |
| מתוכן: היו ב-snapshot של האחזור | 3 |
| מתוכן: טעות שופט אמיתית | 1 |
| אילו | cq06.s4 |
| % סתירה | 2.11 |

| מדד | ערך |
|---|---|
| מקרים שנבדקו בקוד | 203 |
| ציטוטים תקפים (קוד) | 198 |
| ציטוטים פסולים (קוד) | 5 |
| מהם תקפים מול ה-snapshot | 201 |
| תשובות בלי ציטוט | 24 |
| ציטוט שלא היה בהקשר | 0 |
| ציטוט שאין לו צומת בגרף עכשיו | 4 |
| ציטוט שגם ב-snapshot לא היה | 1 |

| מקרה | ציון השופט | הכרעת הקוד | סיווג | למה |
|---|---|---|---|---|
| cq12.s5 | 2.0 | invalid | היה ב-snapshot | not in the graph now: community:L0-1436; but in the retrieval snapshot: community:L0-1436 — the graph moved between the retrieval and this check |
| q107.s5 | 2.0 | invalid | היה ב-snapshot | not in the graph now: community:L1-377; but in the retrieval snapshot: community:L1-377 — the graph moved between the retrieval and this check |
| q004.s5 | 2.0 | invalid | היה ב-snapshot | not in the graph now: community:L0-1352; but in the retrieval snapshot: community:L0-1352 — the graph moved between the retrieval and this check |
| cq06.s4 | 2.0 | invalid | טעות שופט | the answer carries no citation |

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

> Layer 3 of spec §5.2. A case judged twice contributes the mean of its two judgments, once. `n` is the denominator of the row and they are not equal: 13 case(s) never reached a judge (`dropped`). `*_n` is how many cases carried that metric — faithfulness is null on agentic cases, which recorded no context.

## עדכון אינקרמנטלי (§5.5)

מקור: `data/reports/incremental.json` · נמדד ב-2026-09-17T10:12:44+00:00 · sha `4cf28f9`. **STALE** — נמדד ב-4cf28f9, HEAD הוא c2ce8094.

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

שורה לכל מדד, בשמו. הרשימה של 645 מזהי הקהילות שהשתנו נספרת כאן ולא מודפסת — היא ב-`data/reports/incremental.json`, וזה המקום לקרוא אותה.

| מדד | ערך |
|---|---|
| צמתים שנוספו | 144 |
| קשתות שנוספו | 239 |
| chunks שנוספו | 71 |
| ישויות חדשות | 26 |
| קהילות לפני | 1,297 |
| קהילות אחרי | 1,066 |
| קהילות שהשתנו (member_hash) | 645 |
| דוחות קהילה שנשמרו | 124 |
| דוחות קהילה שאבדו | 62 |
| כיסוי חברים בקהילה מסוכמת — לפני (%) | 89.41 |
| כיסוי חברים בקהילה מסוכמת — אחרי (%) | 47.5 |

### rollback — הרצה יבשה

`uv run brain reset --slice incremental   (no --yes = dry run)` — לא הופעל (`applied=לא`). Dry run only. Plan 3 decision 7: the rollback is proven on a slice fixture in `_ResetTest`, not by deleting from the measured graph.

| מה היה נמחק | כמה |
|---|---|
| :Chunk nodes | 43 |
| :Person nodes | 3 |
| :StatusChange nodes | 59 |
| :WorkItem nodes | 10 |
| entities whose every evidence chunk is in this slice | 26 |
| records from persons.jsonl | 4 |
| records from workitems.jsonl | 10 |
| resolution-ledger row(s) pointing at a deleted identity | 1 |

### חמש השאלות על הפריטים החדשים

מצב A (אחזור קבוע, בלי סוכן) מול מצב B (הסוכן בוחר כלים). `ציטוט תקף` הוא המדד הדטרמיניסטי — כל סוגר מרובע מצביע על צומת שקיים.

| שאלה | סוג | route | אסטרטגיה מובילה | recall | A: ציטוט תקף | B: ציטוטים תקפים | B: ציטוט תקף |
|---|---|---|---|---|---|---|---|
| inc1 | temporal | s6 | s6 | 1.0 | כן | 8/8 | כן |
| inc2 | traceability | s2 | s4 | 0.0 | לא | 31/31 | כן |
| inc3 | rationale | s3 | s6 | 1.0 | כן | 5/5 | כן |
| inc4 | rationale | s3 | s6 | 1.0 | כן | 6/6 | כן |
| inc5 | global | s4 | s4 | 0.0 | לא | 20/20 | כן |

סיכום נגזר: מצב A 3/5 · מצב B 5/5.

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

**4 מתוך 5 סוגי שאלה: מובילת ה-recall אינה מובילת הנכונוּת (global, rationale, temporal, traceability)**

שלוש עמודות ה-Δ שייכות ל**מובילת ה-recall** ולא למובילת הנכונוּת — העמודה "עלות מיוחסת ל" אומרת למי בדיוק. `n` ליד כל מוביל הוא מספר השאלות שהתא נשען עליהן; מתחת ל-3 השורה מסמנת `n<3` ולא קוראת לאסטרטגיה "מובילה".

| סוג שאלה | מובילה ב-recall | recall | recall של baseline | מובילה בנכונוּת | נכונוּת | נכונוּת baseline | עלות מיוחסת ל | Δ latency p50 (ms) | Δ tokens p50 | Δ Cypher | עלות מובילת הנכונוּת | הערה |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| גלובלי (`global`) | `s5` (n=6) | 0.1888 | 0.0998 | —, n<3 (`agentic`, n=1) | 2.0 | 1.0 | `s5` | -1966.0 | 165.0 | -11.0 | — (ל-`agentic` אין תא עלות בשכבה 2 — מצב B לא ארז הקשר ולא נמדד בשעון) | — |
| השפעה (`impact`) | `s4` (n=7) | 0.3636 | 0.0476 | `s4` (n=6) | 1.167 | 0.429 | `s4` | -1403.0 | 41.0 | -14.0 | Δ latency -1403.0 · Δ tokens 41.0 · Δ Cypher -14.0 | — |
| נימוק (`rationale`) | `s3` (n=6) | 0.3337 | 0.0727 | `agentic` (n=4) | 1.25 | 0.5 | `s3` | -1503.0 | -1014.0 | 18.0 | — (ל-`agentic` אין תא עלות בשכבה 2 — מצב B לא ארז הקשר ולא נמדד בשעון) | — |
| זמני (`temporal`) | `s6` (n=5) | 0.8133 | 0.0833 | `agentic` (n=4) | 1.875 | 0.0 | `s6` | -697.0 | -2103.0 | -8.0 | — (ל-`agentic` אין תא עלות בשכבה 2 — מצב B לא ארז הקשר ולא נמדד בשעון) | — |
| עקיבוּת (`traceability`) | `s3` (n=7) | 0.3707 | 0.0839 | `agentic` (n=5) | 1.4 | 0.5 | `s3` | -1869.0 | -2697.0 | 19.0 | — (ל-`agentic` אין תא עלות בשכבה 2 — מצב B לא ארז הקשר ולא נמדד בשעון) | — |

- מובילת recall ≠ מובילת נכונוּת: `s5` (r=0.1888, n=6) מול `agentic` (c=2.0, n=1)
- מובילת recall ≠ מובילת נכונוּת: `s3` (r=0.3337, n=6) מול `agentic` (c=1.25, n=4)
- מובילת recall ≠ מובילת נכונוּת: `s6` (r=0.8133, n=5) מול `agentic` (c=1.875, n=4)
- מובילת recall ≠ מובילת נכונוּת: `s3` (r=0.3707, n=7) מול `agentic` (c=1.4, n=5)

---

מה הדוח הזה *לא* אומר: איזו ארכיטקטורה נכונה. הוא אומר מה נמדד, על איזה קורפוס, באיזו עלות. שני הסעיפים הבאים הם הפרשנות, והם היחידים בדף שנכתבו ביד.

## מתי לא הייתי משתמש בגרף כאן

*איפה הגרף לא החזיר את ההשקעה — לפי המספרים שלמעלה, לא לפי תחושה.*

<!-- planner:start -->
**נכתב ביד ע"י המתכנן (Claude Fable 5.1), 2026-09-17, אחרי קריאת המספרים שלמעלה.**

**1. שאלות "מה כתוב על X".** baseline וקטורי (S1) מגיע ל-correctness 1.0 על שאלות גלובליות ול-0.5–0.67 על עקיבות והשפעה ב-144 ms ו-3.8k tokens — ובלי גרף בכלל. אם השאלה היא "מה כתוב על X" ולא "מה קשור ל-X", עלות הגרף (חילוץ, resolve, קהילות, אחזקה) לא מחזירה את עצמה. 5/19 שאלות הכשירות הן כאלה בפועל.

**2. כשאין מפתחות.** כל היתרון של S3/S4/S6 (cross-lingual 4/4, correctness 1.0–1.8) נשען על מזהים בשאלה (`KIP-848`, `clients`, תאריך). שאלה חופשית בלי מפתח → router נופל ל-S2 (correctness 0.5) או ל-S4 מבנק דוגמאות שעשוי להיות עיוור לה (inc2/inc5: 0/5 במצב A). ארגון שבו אנשים לא מדברים במפתחות לא יקבל את המספרים האלה.

**3. קורפוס שמשתנה מהר.** התוספת של 10 issues (0.3%) שינתה 645/1,297 קהילות והפילה כיסוי Global search מ-89% ל-47.5%. Leiden הוא re-partition גלובלי; עם קצב שינוי של ארגון חי, S5 דורש סיכום מחדש שוטף — ~1.5M tokens לסבב. אם אין תקציב לזה, S5 יהיה יפה בדמו ומיושן בייצור.

**4. כשהאמת היא בטקסט חופשי ולא בקשתות.** 81% מה-Decisions שחולצו הן `weak` (בלי סיבה מקושרת); S3 מוביל ב-recall (0.23) ואחרון ב-correctness (0.47) — הוא מחזיר ישויות שהעונה לא יכול להפוך לתשובה. במקום שהידע הוא פרוזה ארוכה (KIP motivation), chunk מצוטט (S1/S2) עונה טוב יותר מגרף ישויות.

**5. צוות שלא ישמור מזהים גולמיים.** resolve הדטרמיניסטי (268+15 מיזוגים) קיים רק כי load סירב לנרמל `JIRAUSER…`; recall אנשים נתקע ב-0.78 בגלל band של שופט. בארגון שבו המערכות לא חולקות מזהים, entity resolution הוא הפרויקט, לא שלב.
<!-- planner:end -->

## מה הייתי משנה

*מה היה נעשה אחרת בסיבוב הבא — סכימה, אחזור, סט השאלות או המדידה עצמה.*

<!-- planner:start -->
**נכתב ביד ע"י המתכנן, 2026-09-17.**

**1. לענות מ-S4/S6 כשיש מפתח, לא מ-S3.** המספרים אומרים שהשורה (Cypher/זמן) עדיפה על הישות: S4 correctness 0.95–1.4 ב-15 ms מול S3 0.47. S3 צריך להחזיר את הפריט עצמו (title+description) ליד הישויות — הראיה שהוא מוצא לא מגיעה לעונה.

**2. Global search אינקרמנטלי.** במקום Leiden מחדש: הקצאת צמתים חדשים לקהילה הקרובה (לפי שכנים), סיכום מחדש רק לקהילות ש-`member_hash` שלהן זז מעל סף, re-partition גלובלי פעם ברבעון. ה-copy rule כבר קיים — חסר הצעד הראשון.

**3. בנק Cypher לפי סטטוס, לא רק לפי סוג.** דוגמאות עם `NOT status IN (Resolved…)` הן נכונות ל"פתוח" ושגויות ל"מה נוצר ב-2026". `sample_values` בסכמה + דוגמה אחת לכל ערך שכיח של `status`/`source` — ו-cq05/cq08 היו מפסיקים להיות "חלקי".

**4. faithfulness בלי הקשר ארוז למצב B.** agentic הוא הכי נכון (1.40) אבל היחיד שאין לו faithfulness, וה-citation_validity שלו הנמוך (1.34) נובע ממזהים מקוצרים שהסוכן העתיק חלקית. לרשום את פלט הכלים של הסוכן כהקשר, ולאכוף מזהים מלאים בציטוט. ובנפרד: 3 מ-8 אי-ההסכמות קוד-מול-שופט הן מזהי קהילה שנמחקו ב-re-partition האינקרמנטלי בין האחזור לבדיקה — בדיקת ציטוטים חייבת לרוץ על אותו snapshot כמו האחזור.

**5. gold מה-truth הסינתטי.** 0/32 — זרוע שלמה של ההערכה (Xray/ADO עם אמת ידועה) לא מומשה כי 3 מסלולים נארזו כ-spare. 4 שאלות, שעה עבודה; זה ה-supplement הראשון.

**6. שני recall בכל דוח, snapshot קפוא, ו-sha על כל מספר.** שלוש התקלות של Plan 3 (gold שמדד סדר אחסון, `--force` בזמן שהגרף זז, דוח שנדרס לעמודה אחת) נתפסו כי היו ledger, sha ו-`context_sha256`. הייתי מקדים את שלושתם ל-Plan 0 במקום ללמוד אותם ב-Plan 3.

**7. איפה זה מגיע ל-NessBot.** הפרויקט הזה נבנה עם Kafka כדי שהאתגרים יהיו אמיתיים; ב-NessBot המערכות הן ADO+Xray אמיתיים. הצעד הראשון: קונקטור ADO לפי `docs/guides/adding-a-connector.md`, 15 שאלות כשירות שלכם, ואותו `brain eval` — לפני שמחליטים אם צריך גרף בכלל.
<!-- planner:end -->
