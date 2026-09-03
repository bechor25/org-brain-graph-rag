# שלב 00 — תשתית וסוכנים (Plan 0)

**תכנית:** Plan 0 · **סוכנים מבצעים:** general-purpose (Opus) לפני שהסוכנים הוגדרו · **מודולים בקורס:** 1 (סביבת עבודה), 2 (Neo4j/Cypher/אינדקסים), 10 (אבטחה — Text2Cypher כמשטח תקיפה), 8 (Agentic GraphRAG)

## מטרה
פלטפורמה מקומית מלאה + חוזה סביבה מבוצע (`brain doctor`) + מודל קנוני + 15 סוכנים, לפני שנוגעים בדאטה.

## קלטים
- spec סעיפים 1, 2.2, 6, 7; `docs/superpowers/plans/2026-09-03-plan0-foundations.md`

## פלטים
- `docker-compose.yml`, `brain/` (cli, config, doctor, graph/client, embed/client, canon/*), `tests/`, `data/fixtures/mini/`, `.claude/agents/` (15), `docs/agents/conventions.md`, README
- אין `data/reports/` בשלב זה — המספרים בדוחות הסוכנים ובסקירות (ראו `docs/planning/progress.md` ו-git log)

## קריטריוני קבלה
- [x] `make check` ירוק (22 בדיקות יחידה)
- [x] `uv run brain doctor` 7/7 OK
- [x] `make smoke` ירוק (5 בדיקות live)
- [x] 15 סוכנים, `model: opus`, הענקות כלים לפי הספק
- [x] progress.md מלא עם hashes

## מה תלמד בשלב הזה
איך נראית תשתית Graph RAG מקומית שלמה בלי שורת דאטה אחת; למה embeddings מקומיים ומימד מוצמד; מה השרת עצמו אוכף (READ mode) לעומת מה ש-regex יכול; ולמה הגבלת כלים פיזית לסוכן חזקה מכל משפט בפרומפט.

## הערות למבצע (lesson-writer)
מקורות למספרים: `docs/planning/progress.md`, `git log --stat main..plan0-foundations`, וקובץ ההערות הגולמי `/private/tmp/claude-501/-Users-bechorsimhaevness-Desktop-code-graph-rag/f18df721-caa2-4800-a86b-674b88f22f10/scratchpad/plan0-lesson-notes.md`. NN = 00. להשאיר את "מה זה מלמד (המתכנן)" ריק.
