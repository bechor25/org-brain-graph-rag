# שיעור 14 — סט השאלות והמטריצה: 32 שאלות, 224 ריצות, ו-recall שאי אפשר לזייף

**תאריך:** 2026-09-17 · **תכנית:** Plan 3 (Task 1 + Task 2) · **מודול בקורס:** 9 ("הערכה ומדידה — לדעת אם זה באמת עובד")

## מה עשינו

**Task 1 בנה את סט ההערכה.** `brain eval questions build` דגם **22 מסלולים אמיתיים** מהגרף ומ-`data/canonical/synthetic_truth.json` ב-**12 צורות** (`by_shape`), ארז אותם ל-**6 batches** בשני shards (הגדול 37,851B מתוך תקרת 40,000B), וביקש מ-`question-forger` **17** שאלות — 30% יותר מהגירעון של 13, כדי ששאלה שתידחה לא תרוקן תא. `brain eval questions merge` קיבל **13**, סימן **4** כעודף, דחה **0**, ועבר שמונה בדיקות: `"0 questions cited a key the graph does not hold"` ו-`"0 questions leaked their answer"` בכללן. ה-gold של 19 שאלות הכשירות מ-Plan 2 נגזר **בקוד** ב-`brain eval questions gold-competency` — 19 שורות, 0 pending, **154 מזהי ראיות**, ולכל שורה `gold_query` שאפשר להריץ שוב. התוצאה: **32 שאלות** ב-`data/eval/questions.jsonl` (sha256 `b0c23fd4…`), **7/7/6/6/6** לפי סוג ו-**11 בעברית**.

**Task 2 הריץ את המטריצה.** `brain eval run --mode fixed` הריץ **32 שאלות × 7 אסטרטגיות = 224 ריצות** ל-`data/eval/runs/fixed/<qid>.<strategy>.json`, כולן באותו תקציב הקשר (`budget_tokens: 4000`, `k: 10`, `include_synthetic: true`, baseline `s1r`), וחישב את שכבה 2 של §5.2 בקוד — **בלי שופט**. ריצה חוזרת החזירה **224 `unchanged`**. התוצאה הראשונה: **S3 recall 0.23** (precision 0.56, hit@10 0.81) מול **S1+rerank 0.08** ו-**S2 0.05**; ה-reranker שילם ×13 latency עבור hit@1 0.125→0.156. S6 קיבל תיקון באמצע (עוגן `Document`) ועבר מ-8 שאלות שנקשרות ל-**18**, ו-14 n/a כנות עם סיבה כתובה.

**וגם: gold שהתברר כמדידה של סדר אחסון.** ה-gold של `cq13`/`cq19` נגזר דרך `changes_between` לא-ממוין; אחרי שהשאילתה קיבלה סדר טוטאלי (`2e3c42c`) ה-gold נגזר מחדש (`c15b838`) — וה-recall של S6 על שתי השאלות האלה ירד מ-0.83 (מקרי) ל-**0.0**.

## למה ככה (הקישור לקורס)

**המודול פותח בדיוק בעיקרון שהתכנית בנויה סביבו.** *"מערכת Graph RAG נכשלת בארבעה מקומות שונים, וציון אחד כולל מסתיר את כולם"* — ולכן §5.2 של ה-spec מפרק לשכבות 0–3 + עלות, ו-Task 2 הוא **שכבה 2 בלבד**: מה נשלף, כמה זה עלה, ואיפה אסטרטגיה בכלל לא חלה. ה-docstring של `brain/eval/metrics.py` מנסח את אותו כלל בגרסה המבצעית: *"never report an answer score without its retrieval score"*.

**הטריק הדטרמיניסטי של הקורס הוא הליבה של Task 2.** הקורס מציג את `IDBasedContextPrecision` / `IDBasedContextRecall` כ*"הטריק הדטרמיניסטי: הערכת אחזור לפי מזהים — בלי LLM שופט בכלל … במדויק, בחינם, ובלי שונות של מודל שופט"*. אצלנו זה `gold_evidence[]` מול המזהים שכל אסטרטגיה החזירה, עם `MATCH_KINDS` מפורש (`key`, `chunk`, `provenance`, `chunk_prefix`, `parent`) ו-`HIT_KS = (1, 3, 5, 10)` — *"10 הוא ראש ה-k; 1 ו-3 הם מה שסוכן קורא לפני שהוא מפסיק לקרוא"*. אין שופט בשלב הזה בכלל.

**"baseline חלש מוכיח כלום".** הקורס מראה בטבלת HippoRAG 2 ש-*"retriever וקטורי חזק לבדו מנצח את Microsoft GraphRAG בכל שלושת הבנצ'מרקים"*. הכרעה 3 בתכנית קובעת לכן ש-baseline = **S1 hybrid + rerank**, ו-`brain/eval/runs.py` מחזיק אותו כעמודה נפרדת: *"`s1r` … kept as its own column so 'did the reranker earn its latency' is a number rather than an opinion"*.

**עלות היא עמודה, לא הערת שוליים.** הביקורת "עלות נעלמת" בקורס (אינדוקס גרפי פי ~57 מווקטורי; שאילתת global ב-~610 אלף טוקנים) מתורגמת ל-`cost` בדוח: latency p50/p95, context tokens, `tool_calls`, `cypher_total`. זה מה שהופך את שורת ה-reranker למספר.

**והנקודה שבגללה ה-gold לא נכתב ע"י LLM.** הקורס מזהיר: *"שאלות סינתטיות יורשות את ההטיות של המודל המחולל"*, ומוסיף את **זיהום הידע הפרמטרי** כביקורת נפרדת. ה-gold אצלנו לא נשען על דעה של מודל אלא על **מסלול שהגרף מחזיק** או על **truth שנכתב בכוונה** — ו-`brain/eval/gold_competency.py` אומר למה: *"a gold answer someone wrote from memory is a fact about that person, and an evaluation graded against it measures agreement with them rather than with the corpus"*. לכן כל אחת מ-19 שאלות הכשירות נושאת את השאילתה שייצרה את התשובה:

```
cq14 · temporal · en
question:   "What was the status of KAFKA-15538 on 2024-01-16?"
gold_answer:"On 2024-01-16, KAFKA-15538 was Reopened (status change at 2024-01-16T16:35:06.947Z)."
gold_query: brain.retrieve.temporal.status_at('KAFKA-15538', '2024-01-16')
gold_derived_by: "code" · gold_evidence: ["KAFKA-15538", "4d304a84…"]
```

ומולה שאלה מזויפת (forged) בעברית, שנכתבה ע"י `question-forger` ממסלול יחיד ומצטטת רק מפתחות שהמסלול הציע:

```
q003 · rationale · he · source_path_id: "decision_rejects:KIP-853"
question: "אילו חלופות תכן נשקלו ונדחו ב-KIP-853, ומה כל אחת מהן הציעה לעשות במקום?"
gold_evidence: ["KIP-853", "Alternative|kip 642 dynamic quorum reassignment",
                "Alternative|leverage metadata controller mechanism", "000d61bd…"]
notes: "The template's \"on what grounds\" variant was not used: the path gives each
        alternative a description but no rejection reason …"
```

**הגנת הדליפה היא מכנית, לא טעם.** הכרעה 2 בתכנית דורשת ששאלה שנגזרה מהגרף תהיה ניתנת למענה גם מ-S1 — ולכן `brain/eval/paths.py` מצהיר לכל צורה `anchor_roles` (מה שמותר לשאלה לנקוב) ו-`answer_roles` (מה שאסור), *"which is what lets `brain eval questions merge` check leakage mechanically instead of by taste"*. `questions_report.py` אוכף שלוש בדיקות: אין מפתח `answers[]` בשאלה, אין מפתח ראיה שאינו עוגן, ואין **רצף של 8 מילים משותפות** בין השאלה לתשובת הזהב (`MAX_SHARED_RUN = 8` — *"eight rather than five: a real question and a real answer share 'the status of the work item' without either giving anything away"*, וזה עובד באותה צורה בעברית). קיום הראיה נבדק פעמיים: מול מה שה-batch **הציע**, ומול הגרף החי.

**המתכון של הקורס לבניית סט הערכה — המיפוי לקוד:**

| במתכון של המודול | איפה אצלנו |
|---|---|
| ריבוד לפי סוג שאלה, "דווחו תוצאות לפי רובד, לא מספר אחד ממוצע" | `QUESTION_TYPES` — 5 סוגים, `by_type` + `matrix` בדוח |
| "‏multi-hop ספציפיות — F1 + recall של ראיות הזהב" | `gold_evidence[]` + `metrics.py` (recall/precision/hit@k) |
| "גלובליות/תמטיות" | סוג `global` (6 שאלות), צורה `community_theme` (8 מסלולים), אסטרטגיה S5 |
| "עברו ידנית על מדגם" | המתכנן והמשתמש דוגמים 10 מתוך ה-32 (Task 1, Acceptance) |
| שכבה 1 לפני שמאשימים אחזור (65.8% כיסוי ב-HotpotQA) | `gold_evidence exists` ב-merge: **100%** מה-154+ המפתחות קיימים בגרף לפני שהורצה שאלה אחת |
| "עלות נעלמת" | `cost{latency, context_tokens, tool_calls, cypher_total}` |

**ולמה `n/a` ולא 0.** `brain/eval/runs.py`: *"A strategy that cannot apply to a question produces an `n/a` record with a reason, on disk, next to the runs that worked. An empty cell in the matrix that nobody can explain is worse than a low number."* ואותו כלל חל על fallback: אם ביקשנו `sN` ו-`Result.strategy` חזר אחרת — זה n/a, *"a strategy is never credited with another strategy's answer"*.

## מספרים

| מדד | ערך |
|---|---|
| commits | Task 1: `2fb8686`, `546adff`, `7f283b2` · Task 2: `38cbbb9` (המטריצה הראשונה), תיקוני S6 `8a2474f`, `d5ebc44`, סדר טוטאלי `2e3c42c`, gold מחדש `c15b838` |
| **סט השאלות** | **32** = **19** כשירות (Plan 2) + **13** מ-`question-forger` · `data/eval/questions.jsonl` sha256 `b0c23fd4…` · `complete: true` |
| חלוקה לפי סוג | `traceability` **7** · `impact` **7** · `rationale` **6** · `global` **6** · `temporal` **6** — *"the most even split of 32 over 5 types is [7, 7, 6, 6, 6]"* |
| היעד שלא הושג | `per_type_goal` **8** · `goal_reachable: false` · `questions_for_goal` **40** (5 סוגים × 8) · `per_type_goal_8: false` |
| שפה | עברית **11** / אנגלית **21** · `hebrew_floor` 11 = `ceil(32/3)` |
| תאים (סוג/שפה) | traceability 3he+4en · impact 2he+5en · rationale 2he+4en · global 2he+4en · temporal 2he+4en |
| חשבון הגירעון | `need_by_type`: global **5**, traceability 2, impact 2, rationale 2, temporal 2 = **13** · `requested_total` **17** (`slack` 0.30) |
| מקור ה-gold בפועל | `by_gold_source` של ה-32: **`graph` 32**, `truth` **0** · `by_origin`: `competency` 19, `forged` 13 |
| מסלולים שנדגמו | **22** · `by_gold_source` graph **18** / truth **4** · `spares` **5** · `snippets` **95** · `snippet_chars` **400** · seed **2026** · `duration_ms` **190** |
| **12 הצורות** | `community_theme` 8 · `blast_radius` 2 · `decision_rejects` 2 · `test_fix` 2 · `duplicate_test` 1 · `motivation` 1 · `ownership_timeline` 1 · `release_window` 1 · `renamed_test` 1 · `stale_state` 1 · `story_kip` 1 · `text_only_link` 1 |
| batches | **6** ב-2 shards · תקרה 40,000B · max **37,851B** · min 18,038 · mean 30,406 · total 182,435 · `max_line_bytes` 428 · `over_budget: []` |
| merge | `accepted_forged` **13** · `surplus_forged` **4** (`q007`, `q102`, `q104`, `q108`) · `rejected_forged` **0** · `failed_batches` **0** · 6/6 batches נענו |
| שערי ה-merge | 8 בדיקות `ok: true`; 4 מהן `gate: true` (איזון סוגים, עברית ≥11, gold לכל שורה, אין batch שנכשל) · `"0 questions cited a key the graph does not hold"` · `"0 questions leaked their answer"` |
| **gold הכשירות (בקוד)** | `data/eval/competency_gold.jsonl` sha256 `d6dab24d…` — **19 שורות, 19 derived, 0 pending**, **154** מזהי ראיות, `clipped_answers` 0, `gold_derived_by: "code"` |
| עוגנים שנבחרו מחדש מהגרף | `issue_most_tests` KAFKA-14649 · `issue_most_assignees` KAFKA-14648 · `issue_most_status_changes` KAFKA-15538 · `kip_most_decides` KIP-932 · `kip_most_rejects` KIP-796 · `kip_most_motivated` KIP-1071 · `kip_most_commits` / `ado_story_kip` KIP-848 · `component_most_open_bugs` clients · `component_most_resolves` streams · `version_low/high` **3.7/3.8** · `status_date` **2024-01-16** |
| תקרות ב-gold | `IMPACT_CAP` 10 · `RATIONALE_CAP` **4** · `THEME_CAP` 3 · `LIST_CAP` 6 · `QUOTE_CHARS` 160 |
| **הסחיפה** | **32 × 7 = 224** ריצות ב-`data/eval/runs/fixed/<qid>.<strategy>.json` · `k` 10 · `budget_tokens` **4000** · `include_synthetic: true` · baseline `s1r` |
| זמני סחיפה | ריצה מלאה ~**89 שניות** (89,097 / 89,036 / 89,741 / 89,294 ms ל-224 ריצות) · ריצת `s6` בלבד 3,074 / 3,113 ms |
| **אידמפוטנטיות** | ריצה ראשונה מלאה: `new` **222** + `unchanged` 2 · ריצה שנייה: **`changed` 17 — כולן `.s4`** · ריצות שלישית ורביעית: **`unchanged` 224** |
| recall לפי אסטרטגיה (7 עמודות; מטבלת Plan 3 ב-`progress.md`) | **S3 0.23** (precision 0.56, hit@10 0.81) · **S5 0.19** (גלובלי) · **S4 0.15** · **S1 0.08** · **S1+rerank 0.08** · **S2 0.05** · **S6 0.2911** |
| שני ה-recall | `recall` (chunk שה-parent שלו הוא צומת gold נספר) מול `recall_strict`: **S1 0.077 מול 0.014**; בשאר האסטרטגיות זהים · `strict_match_kinds` = `key, chunk, provenance, chunk_prefix` (בלי `parent`) |
| **S6 אחרי תיקון עוגן ה-Document** | `ok` **18** / `na` **14** (לפני: 8 / 24) · recall **0.2911** · recall_strict **0.2676** · precision **0.2555** · hit@1/@3/@5/@10 **0.8889** · `items_p50` 15 |
| עלות S6 (מ-`cost`) | `runs` 18 · latency p50 **5ms** / p95 **70ms** / total 250ms · context tokens p50 **3,718**, total **47,612** · `tool_calls` 18 · `cypher_total` **56** (p50 4, mean 3.1111) |
| **שורת ה-reranker** | hit@1 **0.125 → 0.156** · latency p50 **137 → 1,774 ms** (×13) |
| שאר שורות העלות | S1/S2: **~3.8k tokens** ב-precision **0.09–0.11** · S4: **15 ms**, **1.3k tokens** |
| n/a של S6 | 14, כולן בסיבה אחת: `"temporal wording but no key/date/version pair to bind"` — global 6, impact 6, temporal 1, traceability 1 · בכולן `executed: "s2"`, כלומר fallback שזוהה |
| כללי n/a (מ-`runs.py`) | **S4** — הדוגמה הקרובה מתחת ל-`MIN_SIMILARITY = 0.4`, מוכרע **לפני** הריצה · **S5** — אין אות תמטי (`THEMATIC_RULES`) והשאלה אינה `global`; מורחב ב-`--s5-scope all` · **S6** — אין תבנית זמן שנקשרת · והכלל הכללי: `Result.strategy != strategy` = n/a |
| cross-lingual (S6) | 4 זוגות: `tests` (cq01/cq16), `why` (cq09/cq18), `changed` (cq13/cq19) — **`key_jaccard` 1.0** בשלושתם · `change` (cq05/cq17) n/a בשני הצדדים → `key_jaccard: null` |
| אינווריאנטיות לשפה | S3/S4/S6 אינווריאנטים (Jaccard 1.0 ב-3/4 הזוגות) · S1/S2 סוטים |
| cq13/cq19 אחרי גזירה מחדש | S6: recall **0.0**, precision **0.0**, `hit: false`, 15 פריטים, 3,965 tokens (קודם 0.83 — מקרי) |
| קצוות במטריצה | `cq02` (KIP-848, traceability): S6 recall **1.0**, precision 0.3889, 18 פריטים · `cq14`: recall **1.0**, precision **1.0**, 2 פריטים, 519 tokens · `q109`: precision **1.0** ב-2 פריטים |
| כיסוי | `coverage`: expected 32, present 32, `missing: []`, `complete: true` · `gold_pending` 0, `gold_unresolved: []` |
| קוד | `brain/eval/paths.py` (מסלולים: `select`+`detail`, `POOL_FACTOR` 6, seed) · `questions.py` (water-fill + batches) · `questions_report.py` (merge + דליפה + קיום) · `gold_competency.py` (gold ל-19) · `runs.py` (הסחיפה) · `metrics.py` (שכבה 2) · `brain/eval/templates.md` · `.claude/agents/question-forger.md` |

**מטריצת S6 × סוג שאלה** (מ-`matrix` בדוח; זהה ל-`by_type` כי הדוח בנוכחותו מחזיק עמודה אחת):

| סוג | n | ok | n/a | recall | recall_strict | precision | hit@1 | items p50 | latency p50/p95 | tokens p50 | Cypher |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `traceability` | 7 | 6 | 1 | **0.3022** | 0.2837 | 0.1592 | 1.0 | 10 | 5 / 70 ms | 1,430 | 18 |
| `impact` | 7 | 1 | 6 | 0.0909 | 0.0909 | 0.0556 | 1.0 | 18 | 9 / 9 ms | 3,927 | 4 |
| `rationale` | 6 | 6 | 0 | 0.167 | 0.167 | 0.0979 | 1.0 | 15 | 5 / 7 ms | 3,718 | 24 |
| `global` | 6 | 0 | 6 | `null` | `null` | `null` | `null` | — | — | — | 0 |
| `temporal` | 6 | 5 | 1 | **0.4667** | 0.4044 | **0.6** | 0.6 | 5 | 4 / 40 ms | 1,284 | 10 |
| **סה"כ** | **32** | **18** | **14** | **0.2911** | **0.2676** | **0.2555** | **0.8889** | 15 | 5 / 70 ms | 3,718 | **56** |

## מה הפתיע

- **"8 לכל סוג" הוא לא יעד — הוא חשבון שלא סוגר.** התכנית ביקשה 32 שאלות **וגם** 8 לכל סוג; חמישה סוגים × 8 = **40**. `brain/eval/questions.py` לא התעלם ולא עיגל: הוא מדווח `per_type_goal: 8`, `goal_reachable: false`, `questions_for_goal: 40`, וממלא את 13 החדשות **water-fill** — מרים את הסוג הנמוך ביותר קודם, *"which is the same instinct 8-per-type had, applied to the budget that exists"*. `global` התחיל עם 1 מתוך 19 וקיבל 5 מתוך 13 בדיוק בגלל זה. התוצאה `[7, 7, 6, 6, 6]` היא החלוקה השווה ביותר האפשרית, והיא נרשמה כ**שער שעבר**, לא כפשרה שהוסתרה.
- **0 מתוך 32 השאלות הסופיות נשענות על `synthetic_truth.json`.** הכרעה 1 אמרה במפורש *"gold מהסינתטי = מה-truth, לא מהגרף"*, ו-4 מתוך 22 המסלולים באמת היו `gold_source: truth`. אבל שלושה מהם (`renamed_test`, `duplicate_test`, `stale_state`) נארזו כ-`spare: true` — כלומר לא נשאלה עליהם שאלה — והרביעי (`text_only_link:XT-20156-KAFKA-16355`) כן ייצר שאלה, `q104`, שנחתכה ל**עודף** כי תא `impact/he` היה צריך אחת וקיבל שתיים. `by_gold_source` של הסט: **`graph: 32`**. השכבה שנבנתה במיוחד כדי לספק אמת ידועה (rename, stale state, קישור שקיים רק בטקסט) **לא נמדדת בסבב הזה בכלל**.
- **ריצה חוזרת זיהתה 17 שינויים שהם שעון עצר, לא תוצאה.** ה-rerun השני דיווח `changed: 17` — כל 17 הקבצים `.s4`, ואף אחד מהם לא החזיר שורה אחרת. הסיבה כתובה ב-`runs.py`: `result.route.query_ms`, ש-`cypher_guard.run_cypher` כותב לתוך המעטפת. הכלל שנולד מזה הוא `VOLATILE_SUFFIX = "_ms"` — כל שדה שמסתיים ב-`_ms`, בכל עומק, יוצא מההשוואה: *"A comparison that calls a stopwatch a difference cannot prove determinism."* הריצה הבאה: **224 `unchanged`**.
- **ה-gold של `cq13`/`cq19` מדד את סדר האחסון של Neo4j.** הוא נגזר דרך `brain.retrieve.temporal.changes_between('clients', '3.7', '3.8')` בלי סדר טוטאלי, ולכן 12 מפתחות ה-`XT-…` שבו הם פשוט מה שהמסד החזיר באותה ריצה. ה-recall 0.83 שנמדד לפני התיקון היה **מקרי**. אחרי `2e3c42c` (סדר טוטאלי בכל שאילתת זמן) ו-`c15b838` (gold נגזר מחדש), S6 על שתי השאלות האלה מקבל **0.0** — כלומר האסטרטגיה שהשאלה נכתבה עבורה (`expected_strategy: s6`) מחזירה 15 פריטים שאף אחד מהם אינו ראיית זהב. מספר גרוע ואמיתי עדיף על מספר טוב שנשען על סדר שורות.
- **קיימות שלוש שאלות שה-gold שלהן חלש — ומתועדות ככאלה.** `cq06` נגזר ל"שום דבר לא נכשל" (תשובה באורך 159 תווים, `gold_evidence` = `clients`, `producer`, `streams`): זו **אמת בקורפוס** — לשכבה הסינתטית אין ריצות FAIL על הטסטים האלה — אבל שאלה שהתשובה הנכונה שלה היא רשימה ריקה נותנת לכל אסטרטגיה "לנצח" בריק. `cq09`/`cq18` שואלות על KIP-932 שיש בו **178 החלטות**, וה-gold נוקב ב-**4** בלבד (`RATIONALE_CAP = 4`) — כל אסטרטגיה שתחזיר 20 החלטות נכונות אחרות תיענש ב-recall. את שתיהן `gold_competency.py` כתב לפי הכלל שלו (*"an empty answer stays empty"*), והן נרשמו בטבלת Plan 3 כ"חולשות ידועות ב-gold" לפני שנמדד עליהן משהו.
- **תיקון עוגן ה-Document הכפיל את מה ש-S6 בכלל מסוגל לענות עליו: n/a 24 → 14.** לפני התיקון S6 נקשר ל-8 שאלות מתוך 32 והראה recall 0.56 — מספר גבוה על בסיס זעיר. אחרי שהתבנית למדה לבנות היסטוריה של KIP מ**קומיטים שמממשים ו-issues שמפנים** (`props.via`), 18 שאלות נקשרות וה-recall ירד ל-0.2911. **ה-n/a שנשארו הם כנים**: כולם נושאים סיבה אחת, `"temporal wording but no key/date/version pair to bind"`, ו-`executed: "s2"` — כלומר הקוד זיהה את ה-fallback ולא זקף אותו ל-S6.
- **גם שאלות שנכתבו במפורש ל-S6 יכולות ליפול על ה-n/a.** `q008` היא שאלת `release_window` עם `expected_strategy: s6` וכל הרכיבים בגוף השאלה (`system tests`, `3.8.0`, `4.0.0`) — ובכל זאת התבנית לא נקשרה והריצה סומנה n/a. מנגד, 6 שאלות `global` קיבלו את אותה סיבה **בדיוק** ("ניסוח זמני"), למרות שאין בהן שום ניסוח זמני: זו סיבת ברירת המחדל של ה-binder, ולא אבחנה אמיתית לגביהן.
- **ה-reranker לא החזיר את ה-latency שלו.** 0.125 → 0.156 ב-hit@1 זה **שאלה אחת מתוך 32** (4 → 5), במחיר p50 של 137 ms → 1,774 ms. במטריצה של הקורס זו בדיוק השורה שבדרך כלל לא מדווחת.
- **hit@10 גבוה עם recall נמוך זה לא סתירה — זה תיאור מדויק.** S3 מגיע ל-hit@10 0.81 עם recall 0.23, ו-S6 ל-hit@1 0.8889 עם recall 0.2911: כמעט תמיד חוזר **העוגן** (KIP-848, KAFKA-14649), וכמעט אף פעם לא כל שאר שרשרת הראיות. ב-`cq04` למשל S6 החזיר 18 פריטים ופגע ב-`KIP-848` בלבד מתוך 11 מפתחות זהב — recall 0.0909 עם `hit: true`.
- **שני ה-recall הם אמירה על הגינות, לא על מתמטיקה.** `recall` סופר chunk שה-`parent_key` שלו הוא צומת זהב; `recall_strict` לא. ההפרש נוגע כמעט רק ב-S1: **0.077 מול 0.014**. הערת הדוח אומרת למה שניהם מתפרסמים: *"so the vector baseline is not scored against an id scheme only the graph strategies emit"* — בלי הכלל הזה, ה-baseline הווקטורי היה מקבל אפס כמעט על הכול רק מפני שהוא מחזיר chunks ולא מפתחות.

## מה היינו משנים

- **הריצה של אסטרטגיה בודדת דרסה את המטריצה בדוח.** `data/reports/eval_retrieval.json` במצבו הנוכחי מחזיק `strategies: ["s6"]`, וגם `by_strategy`, `by_type`, `matrix` ו-`cost` בו מכילים **רק את S6** — כי שתי הריצות האחרונות היו `--strategies s6`. שבע העמודות עדיין קיימות ב-224 קבצי הריצה על הדיסק, והמספרים המסכמים שלהן שרדו רק בטבלת Plan 3 ב-`progress.md`. מה שמחמיר: כל ה-`sections` מסומנים `stale: false`, כי כולם נכתבו ע"י אותה ריצה אחרונה — הדוח לא יודע לספר שהוא מציג תמונה חלקית. המיזוג (`write_report`) צריך למזג **לפי אסטרטגיה** ולסמן עמודה שלא רצה בסבב הזה כ-`stale` עם ה-sha שבו כן רצה.
- **סיבת n/a צריכה להיות פר-אסטרטגיה ופר-שאלה, לא מחרוזת אחת.** 14 ה-n/a של S6 נושאים מחרוזת זהה, כולל שש שאלות `global` שלא ביקשו שום דבר זמני. סיבה שנכונה ל-8 מקרים ולא מדויקת ל-6 היא בדיוק סוג ההודעה שגורמת לקרוא את המטריצה לא נכון בעוד חודש. `runs.py` כבר יודע לזהות fallback גנרי (`Result.strategy != strategy`) — חסרה לו סיבה שנגזרת מהבדיקה שנכשלה בפועל.
- **`gold_query` צריך היה לכלול `ORDER BY` כתנאי, לא כמוסכמה.** הכלל של `gold_competency.py` ("כל תשובה נושאת את השאילתה שייצרה אותה") הוא מה שאִפשר לגלות את הבאג של `cq13`/`cq19` ולגזור מחדש בעלות אפס. אבל שום דבר בקוד לא **אכף** שהשאילתה תחזיר סדר דטרמיניסטי; הגילוי הגיע אחרי שהמטריצה כבר פורסמה עם recall 0.83. בדיקה שמריצה כל `gold_query` פעמיים ומשווה את סדר התוצאות הייתה תופסת את זה לפני המדידה.
- **שאלה שהתשובה שלה ריקה צריכה סיווג משלה.** `cq06` ("שום דבר לא נכשל") נמדדת היום כמו כל שאלה אחרת, ולכן היא מזכה כל אסטרטגיה ב-recall על שלושה שמות רכיבים. הקורס מונה רובד רביעי — *"בלתי-ניתנות-למענה (בדפוס MuSiQue-Full) — מודדות הזיות כשאין ראיות"* — ושם שאלה כזאת שווה זהב, אבל **בשכבה 3** (האם הסוכן ממציא?), לא בשכבה 2. שדה כמו `gold_empty: true` שמוציא את השאלה מממוצעי ה-recall ומכניס אותה למדד הזיות היה נותן לשאלה הזאת את התפקיד הנכון.
- **4 מסלולי ה-truth היו צריכים תא שמור, לא מקום בתור.** השכבה הסינתטית קיימת כדי לתת אמת שאינה תלויה בחילוץ — וכל 4 המסלולים שלה נפלו מהסט: שלושה כ-`spare` ואחד כעודף. ה-water-fill מאזן סוג ושפה בלבד; הוא לא יודע ש-`gold_source` הוא ממד שלישי שצריך מכסה. מכסה מינימלית (למשל 2 שאלות `truth`) הייתה עולה שתי שאלות `graph` ומחזירה את היכולת לבדוק rename ו-stale state.
- **`impact` שילם את המחיר של סחיפה חד-אסטרטגית.** מתוך 7 שאלות ה-`impact`, **6 הן n/a** ב-S6, כך שהתא כולו נשען על שאלה אחת (`cq08`, recall 0.0909). כל עוד הדוח מחזיק עמודה אחת, `by_type` נראה כאילו הוא מתאר את כל המערכת — בזמן שהוא מתאר את S6 בלבד. הסדר הנכון הוא: להריץ תמיד את כל העמודות, ולסמן במפורש כשמדובר בעמודה אחת.

## מה זה מלמד (המתכנן)

<נכתב ע"י המתכנן>
