# שלב 10 — `brain index` + שער Plan 1 (Plan 1, Task 9)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-graph-engineer` · **מודול בקורס:** 2 ("אינדקסים וקטוריים ו-full-text"), 9 (מדידה בשכבות — שכבה 1: איכות גרף)

## מטרה
כל האינדקסים סופיים + מפקד גרף מלא = דוח השער של Plan 1. זה המסמך שממנו Plan 2 יוצא לדרך.

## קלטים
- גרף אחרי resolve (אנשים + ישויות) ו-communities; `IndexMeta`; דוחות כל השלבים
- roadmap gate: ≥1,400 issues, ≥190 KIPs מוטמעים, ≥4,000 commits, resolution P/R, 0 קשתות LLM בלי provenance, כל האינדקסים ONLINE, `make smoke` ירוק, שיעורים 01–09

## פלטים
- `brain/index/` (או `brain/graph/index.py`), פקודת `brain index [--check-gate]`, `data/reports/index.json`, `docs/report/plan1-graph-census.md` (נוצר מהדוח — עברית, טבלאות)

## הכרעות מתכנן
1. **אינדקסים:** vector `chunk_embedding` (קיים), `entity_embedding` (קיים), `community_embedding` (09); **fulltext** `workitem_text` על `WorkItem(title, description)`, `document_text` על `Document(title, body_md)`, `entity_text` על `Entity(name, description)`, `person_text` על `Person(display, aliases)`; range indexes על `StatusChange.at`, `WorkItem.created`, `Commit.at`. כולם idempotent (`IF NOT EXISTS`), ממתינים ל-ONLINE.
2. **מפקד (census):** צמתים לפי label (+ תת-label), קשתות לפי type, `synthetic` לעומת אמיתי, provenance coverage (% צמתים/קשתות LLM עם `batch_id`), orphans לפי label, dangling refs (מ-load), resolution (זהויות/אדם, P/R/F1 אנשים + ישויות), קהילות (לפי רמה, גודל p50/p95, % חברים בקהילה מסוכמת), chunks (חיים/orphaned, מוטמעים), אינדקסים (שם, סוג, מצב, populationPercent), `IndexMeta` לכל vector index (מודל, מימד, ספירה **חיה**), fingerprint של canonical (sha256), גרסאות (Neo4j, GDS, APOC, Ollama, bge-m3).
3. **`--check-gate`:** מעריך את קריטריוני השער מה-roadmap ומדפיס PASS/FAIL לכל אחד; exit 1 אם FAIL. (recall אנשים 0.744 — השער מדווח FAIL על ≥0.85 recall **בכנות**, עם ההערה של המתכנן שהתקבל.)
4. **דוח בעברית** `docs/report/plan1-graph-census.md` — נוצר ע"י קוד מה-JSON (טבלאות; אין מספרים ידניים), ונכנס ל-git.
5. **`IndexMeta.chunk_count`** = ספירה חיה (לא orphans) — תיקון מסקירת chunk.

## קריטריוני קבלה
- [ ] כל האינדקסים ONLINE, 100%; ריצה חוזרת = 0 אינדקסים חדשים.
- [ ] `data/reports/index.json` מלא לפי §2; `docs/report/plan1-graph-census.md` נוצר ותואם ל-JSON.
- [ ] `brain index --check-gate` מדפיס את כל הקריטריונים עם ערכים; FAIL רק על recall אנשים (ידוע).
- [ ] `make check` + `make smoke` ירוקים; `brain load` עדיין 0/0 אחרי index.

## מה תלמד בשלב הזה
"שכבה 1" של ההערכה בקורס — איכות הגרף במספרים לפני שנוגעים באחזור; ומה זה אומר לקרוא מפקד גרף ולדעת מה הוא יכול ולא יכול לענות.
