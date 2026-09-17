# Plan 3 — הערכה + דוח (brief-style)

Spec: §5 (סט הערכה, מדידה בשכבות, מצבי ריצה, שופט עיוור, אינקרמנטלי, דוח). Roadmap gate: **`docs/report/eval-report.md` מלא; מטריצה אסטרטגיה×סוג; סעיף "מתי לא הייתי משתמש בגרף" נכתב.**

עיקרון: כל מספר בדוח נוצר מקוד מ-JSONL. המתכנן כותב רק את שני הסעיפים הפרשניים בסוף. הסוכן-שופט עיוור לשם האסטרטגיה.

## הכרעות מתכנן (חלות על כל המשימות)
1. **32 שאלות = 19 הכשירות (מ-Plan 2) + 13 חדשות** מ-`question-forger` על מסלולים אמיתיים מהגרף ומ-`synthetic_truth.json` (Xray/ADO — אמת ידועה). יעד: 8 לכל סוג, ≥11 בעברית. `gold_evidence` = מפתחות/chunk ids שקיימים בגרף; gold מהסינתטי = מה-truth, לא מהגרף.
2. **הגנה מדליפה:** שאלה שנגזרה ממסלול בגרף חייבת להיות ניתנת למענה גם מ-S1 (הטקסט הגולמי) — ה-gold הוא עובדה מהמקור, לא מהחילוץ. השאלות מסומנות `gold_source: graph|truth|text`.
3. **מצב A** (קבוע): כל אסטרטגיה רלוונטית על כל שאלה (S1 ±rerank, S2, S3, S4 מבנק הדוגמאות בלבד — בלי סוכן, S5, S6 כשיש תבנית), אותו context budget (~4k tokens). **Baseline** = S1 hybrid + rerank. **מצב B** (אגנטי): `brain-analyst` ×3 דרך MCP, כמו Task 4 של Plan 2 — על כל 32.
4. **שכבה 2 דטרמיניסטית:** hit@k / recall של `gold_evidence`, context precision, latency p50/p95, context tokens, #Cypher — מה-JSONL של האחזור. אין שופט.
5. **שכבה 3 בשופט עיוור:** `eval-judge` ×2, batches ≤40KB, תוויות אקראיות במקום שמות אסטרטגיות (`data/eval/blind_map.json` נשמר מחוץ ל-batches), pairwise baseline-מול-גרף בסדר אקראי, rubric 0–2 × 4 מדדים, JSON מאומת בקוד. המשתמש דוגם 10% מהשיפוטים (המתכנן מכין רשימה).
6. **תשובות למצב A** — הסוכן העונה במצב A הוא `brain-analyst` שמקבל **רק** את ההקשר שהוחזר (לא כלים) ועונה — כך המדידה משווה אחזור, לא כישרון סוכן.
7. **אינקרמנטלי:** 10 issues אמיתיים של Kafka שנוצרו אחרי 2025-12-31 (מחוץ לפרוסה) → `brain harvest --since 2026-01-01 --source jira` → canon → load → chunk → extract (batch אחד) → resolve tier 1 → communities (copy rule) → index. מודדים זמן לכל שלב, מה נוסף, כמה קהילות השתנו (`member_hash`), ו-5 שאלות על הפריטים החדשים במצב B.
8. **דוח** `docs/report/eval-report.md` נוצר מקוד: מטריצה אסטרטגיה × סוג (שכבה 2 + 3), עלות (latency/tokens/tool calls/agent time), שכבה 1 (מ-index.json/resolve.json), אינקרמנטלי, cross-lingual (HE מול EN מקבילות). שני הסעיפים האחרונים — "מתי לא הייתי משתמש בגרף כאן" ו-"מה הייתי משנה" — כותב המתכנן אחרי קריאת המספרים, ומסומנים ככאלה.

---

## Task 1 — סט השאלות (agents: `brain-eval-engineer` לכלים; `question-forger` ×2) · lesson 14
**Contract:** `brain/eval/{question_schema.json,templates.md,paths.py,build_questions.py}`; `brain eval questions build [--n 13] [--shards 2]` דוגם מסלולים (Test–TESTS→WorkItem←RESOLVES–Commit; Document–DECIDES→Entity←REJECTS; ASSIGNED_TO intervals; IN_COMMUNITY) + קטעי truth → batches ≤40KB; `brain eval questions merge` מאמת (gold_evidence קיימים; סוג/שפה מאוזנים; אין דליפה: השאלה לא מכילה את התשובה) → `data/eval/questions.jsonl` (32). המתכנן והמשתמש דוגמים 10.
**Acceptance:** 32 שאלות, 8/סוג, ≥11 HE, 100% gold_evidence קיים, `data/reports/eval_questions.json`.

## Task 2 — הרצה במצב A + שכבה 2 (agent: `brain-eval-engineer`) · lesson 14
**Contract:** `brain eval run --mode fixed [--strategies …]` → `data/eval/runs/fixed/<qid>.<strategy>.json` (Result + הקשר ארוז); שכבה 2 מחושבת בקוד → `data/reports/eval_retrieval.json` (מטריצה, latency, tokens, #cypher). S4 רק מבנק הדוגמאות (התאמת שאלה לדוגמה לפי similarity + הצבת anchors) — ללא סוכן.
**Acceptance:** כל שילוב אסטרטגיה×שאלה רץ או מסומן N/A עם סיבה; recall@k של gold_evidence לכל תא; rerun זהה (דטרמיניסטי מלבד latency).

## Task 3 — תשובות (מצב A ו-B) + שופט עיוור (agents: `brain-eval-engineer`; `brain-analyst` ×3; `eval-judge` ×2) · lesson 15
**Contract:** `brain eval answers build` → batches לסוכן העונה (מצב A: הקשר בלבד); מצב B = Task 4 של Plan 2 על 32; `brain eval judge build` → batches עיוורים pairwise; `brain eval judge merge` → `data/reports/eval_answers.json` (faithfulness/correctness/citation/relevancy לפי אסטרטגיה×סוג; הסכמה בין שני השופטים על 20% חופפים).
**Acceptance:** 100% batches תקינים; `blind_map.json` לא נחשף לשופטים; מדגם 10% למשתמש מוכן; cite-check דטרמיניסטי על כל תשובה.

## Task 4 — אינקרמנטלי (agent: `brain-ingest-engineer`) · lesson 16
**Contract:** לפי הכרעה 7; `data/reports/incremental.json` (זמן/שלב, נוסף/השתנה, קהילות שהשתנו, 5 שאלות); ידני: `brain reset --since` לא קיים → הפריטים מסומנים `slice: incremental` כדי שאפשר יהיה להסירם.
**Acceptance:** 10 פריטים בגרף עם provenance; 0 כפילויות (ledger); ≥4/5 שאלות עם ציטוט תקף.

## Task 5 — דוח + שער (planner + `brain-eval-engineer` + `brain-reviewer`) · lesson 16
**Contract:** `brain eval report` → `docs/report/eval-report.md` מקוד; המתכנן מוסיף שני סעיפים; סקירה; merge ל-main.
**Acceptance:** roadmap gate; `docs/planning/progress.md`; שיעורים 14–16; README מעודכן עם "איך לשאול".

## Self-review (planner)
- כל שכבה מ-§5.2 מכוסה: 0 (דוחות קיימים), 1 (index/resolve + 100 MENTIONS לשופט — נכנס ל-Task 3 כ-batch נפרד), 2 (Task 2), 3 (Task 3), עלות (Task 2+3).
- מקביליות: Task 1 → Task 2 → Task 3; Task 4 במקביל ל-2–3 (מוסיף לגרף — לכן **רץ אחרי** שה-runs של מצב A נשמרו, או על snapshot; הכרעה: אחרי Task 2).
