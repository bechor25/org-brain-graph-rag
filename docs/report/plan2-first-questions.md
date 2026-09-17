# שאלות ראשונות — שער Plan 2

נוצר אוטומטית ע"י `brain eval gate-report` מתוך `data/reports/plan2_gate.json` (נמדד ב-2026-09-17T09:18:15+00:00, sha `c15b8385`). **אין כאן מספר שהוקלד ביד** — למעט פסקת ההכרעה של המתכנן, שנשמרת בין יצירות.

הסוכנים ענו על 19 מתוך 19 שאלות מ-`data/eval/competency.jsonl`; התשובות נמצאות ב-`data/eval/plan2_answers`. כל תשובה נכתבה במצב B (הסוכן בוחר כלים דרך MCP), ו-`cite-check` בדק כל ציטוט מול הגרף בקריאה בלבד.

## שער Plan 2

**מצב:** עבר — 5/6 קריטריונים. רק הקריטריונים המסומנים `כן` בעמודת **מחייב** קובעים את קוד היציאה של `brain eval cite-check`; השאר נמדדים ומדווחים.

| קריטריון | דרישה | ערך | תוצאה | מחייב | פירוט |
|---|---|---|---|---|---|
| every_question_answered | 19/19 answer files | 19/19 | עבר | כן | every question has a file |
| every_answer_has_a_valid_citation | ≥1 citation that resolves in the graph, per question | 19/19 | עבר | כן | every answer cites something that exists |
| citation_validity_rate_at_least_90 | ≥90% of distinct citations resolve | 99.0% | עבר | לא | 198/200 |
| every_answer_has_a_strategy_line | a `strategy:` line naming the tools used | 19/19 | עבר | לא | all present |
| every_answer_is_in_the_questions_language | answer in the language of the question | 19/19 | עבר | לא | all match |
| every_sentence_carries_a_citation | plan decision 2: cite in every factual sentence | 92/95 | נכשל | לא | 3 of 95 sentences carry no citation |

## שאלה אחרי שאלה

שורה לכל שאלה מתוך `competency.jsonl`. **ציטוטים** = כמה מהציטוטים השונים בתשובה נמצאו בגרף מתוך כמה נבדקו (אותו ציטוט פעמיים נספר פעם אחת). **latency** במילישניות, משורת `latency:` בקובץ התשובה או מהפרש `started_at`/`finished_at`. **הכרעה** נכתבת ביד ע"י המתכנן ב-`planner_verdicts` שב-JSON; `—` = טרם הוכרע.

| שאלה | שפה | כלים | ציטוטים | latency (ms) | הכרעה | הערות |
|---|---|---|---|---|---|---|
| cq01 | en | route, lookup, impact | 10/10 | 155,000 | נכון | — |
| cq02 | en | route, lookup, search_with_context, get_schema, cypher_examples, run_cypher, explain_edge | 15/17 | 420,000 | חלקי | 2 ציטוטים פסולים |
| cq03 | en | route, get_schema, cypher_examples, run_cypher | 5/5 | 115 | נכון | — |
| cq04 | en | route, lookup, get_schema, cypher_examples, run_cypher, explain_edge | 8/8 | 145,000 | חלקי | — |
| cq05 | en | route, impact, get_schema, cypher_examples, run_cypher | 21/21 | 300,000 | חלקי | — |
| cq06 | en | route, get_schema, cypher_examples, run_cypher | 17/17 | 232 | נכון | — |
| cq07 | en | route, local_search, impact, run_cypher, explain_edge | 10/10 | 170,000 | נכון | — |
| cq08 | en | route, lookup, get_schema, cypher_examples, run_cypher | 10/10 | 180,000 | חלקי | — |
| cq09 | en | route, lookup, local_search, run_cypher, search_chunks, explain_edge | 8/8 | 752 | נכון | — |
| cq10 | en | route, lookup, run_cypher | 7/7 | 145,000 | נכון | — |
| cq11 | en | route, lookup, local_search, search_chunks, run_cypher | 3/3 | 240,000 | נכון | — |
| cq12 | en | route, global_search, run_cypher | 12/12 | 290 | נכון | — |
| cq13 | en | route, changes_between, run_cypher | 13/13 | 140,000 | נכון | — |
| cq14 | en | route, lookup, status_at | 2/2 | 120,000 | נכון | — |
| cq15 | en | route, assignees_over_time | 5/5 | 27 | נכון | — |
| cq16 | he | route, lookup, impact, run_cypher | 10/10 | 85,000 | נכון | — |
| cq17 | he | route, impact, get_schema, cypher_examples, run_cypher | 21/21 | 240,000 | חלקי | — |
| cq18 | he | route, lookup, run_cypher, local_search, explain_edge | 8/8 | 460 | נכון | — |
| cq19 | he | route, changes_between, run_cypher | 13/13 | 95,000 | נכון | — |

## סיכום

| מדד | ערך |
|---|---|
| שאלות בסט | 19 |
| נענו | 19/19 |
| חסרות | — |
| תשובות עם ≥1 ציטוט תקף | 19/19 |
| ציטוטים שונים | 200 |
| מהם תקפים | 198 |
| מהם פסולים | 2 |
| שיעור תקינות | 99.0% |
| משפטים עם ציטוט | 92/95 |
| latency p50 (ms) | 140,000 |
| latency min–max (ms) | 27–420,000 |
| תשובות לפי שפה | en: 15, he: 4 |

### ציטוטים לפי סוג

| סוג | ציטוטים שונים |
|---|---|
| chunk | 43 |
| commit | 19 |
| קהילה | 4 |
| מסמך/KIP | 26 |
| אדם | 11 |
| פריט עבודה | 97 |

### כלים שהסוכנים השתמשו בהם

כמה תשובות הזכירו כל כלי בשורת ה-`strategy:` (תשובה נספרת פעם אחת לכלי).

| כלי | תשובות |
|---|---|
| route | 19 |
| run_cypher | 16 |
| lookup | 10 |
| cypher_examples | 7 |
| get_schema | 7 |
| explain_edge | 5 |
| impact | 5 |
| local_search | 4 |
| changes_between | 2 |
| search_chunks | 2 |
| assignees_over_time | 1 |
| global_search | 1 |
| search_with_context | 1 |
| status_at | 1 |

## ציטוטים פסולים

ציטוט פסול = מפתח או מזהה שהסוכן כתב ושאין לו צומת בגרף. זו בדיקה דטרמיניסטית (`STARTS WITH` ל-chunk ול-sha, השוואה מדויקת למפתחות), לא שיפוט של התשובה.

| שאלה | הציטוט | סוג | למה נפסל |
|---|---|---|---|
| cq02 | chunk:426899d3d5277600 | chunk | no Chunk id starts with '426899d3d5277600' |
| cq02 | 9ea0503e2343685 | commit | no Commit sha starts with '9ea0503e2343685' |

## הכרעת המתכנן

<!-- planner:start -->
**הכרעת המתכנן (2026-09-17).** 19/19 נענו דרך כלי ה-MCP בלבד; 198/200 ציטוטים קיימים בגרף (99%). 14 נכונות, 5 חלקיות, 0 שגויות — **השער עובר** (≥15 נכון/חלקי, guard 100%).

מה "חלקי" אומר בפועל: בכל חמשת המקרים העוגן נכון והסט צר יותר מה-gold — cq05/cq17 ספרו 321 פריטים פתוחים *בלי* טסטים מול 716 כולל; cq08 ספר 9 פתוחים לא-טסטים מול 17; cq04 נתן את ה-Epic ושלושה ילדים מתוך 15; cq02 קרא את השאלה כ"הבאג מאחורי הרולאאוט" (KAFKA-16637 + commit אחד) בעוד ה-gold מונה 64 commits מיישמים. הפער הוא בפרשנות של "פתוח" ו"מתקן", לא בעובדות.

שני הציטוטים הפסולים, שניהם ב-cq02: `chunk:426899d3d5277600` ו-`9ea0503e2343685` — מזהים שלא קיימים. הסוכן כנראה שחזר מזהה מזיכרון במקום להעתיק מהכלי. זה בדיוק מה ש-cite-check קיים בשבילו, וזה נספר לרעתו.

תובנות מהמעבר: (1) האנליסטים עקפו את ה-router ב-6/19 (s3→s4 לסטים מדויקים, s2→impact לשאלות השפעה) — תמיד עם סיבה, וההכרעות נכונות; (2) `impact("clients")` דיווח 0 טסטים כי הוא סופר רק טסטים של הפריטים הפתוחים שהחזיר, בעוד 284 טסטים מכסים את הרכיב — פער כלי, נרשם; (3) cq06 = "אף טסט לא נכשל" הוא האמת בקורפוס (191 ריצות FAIL קיימות, אף אחת על ששת הטסטים של באגי 3.8 הפתוחים) — הסוכן אימת בשלוש דרכים לפני שענה "None"; (4) cq03 חשף שני "בעלים" לפי שני מדדים (lbrutschy לפי הקצאות+commits פותרים, mjsax לפי קבצים) — תשובה שרק גרף יכול לתת.
<!-- planner:end -->

---

מה הדף הזה *לא* אומר: האם התשובה נכונה. `cite-check` יודע רק שהציטוט קיים בגרף — השאלה אם הסוכן השתמש בראיה נכון היא הכרעת המתכנן כאן, ושיפוט עיוור לפי רובריקה הוא Plan 3.
