# שלב 03 — `brain canon` (Plan 1, Task 2)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-ingest-engineer` · **מודול בקורס:** 3 ("ומה עם דאטה מובנה?", "שלב 0: איכות הקלט")

## מטרה
למפות כל raw למודל הקנוני (5 טיפוסים) עם refs דטרמיניסטיים מסוננים, ולמדוד מה הנרמול מאבד.

## קלטים
- `data/raw/jira/`, `data/raw/confluence/`, `data/raw/git/commits.jsonl` + `data/reports/harvest.json` (כולל `raw_layout` — כלל dedupe בין base ל-`since-*/`, ו-`kip_key` — התנגשויות מפתחות KIP)
- `brain/canon/models.py`, `brain/canon/mentions.py`, `brain/canon/io.py`
- Task 2 ב-`docs/superpowers/plans/2026-09-03-plan1-corpus-to-graph.md`

## פלטים
- `brain/canon/mappers/{jira,confluence,git}.py`, פקודת `brain canon`, `data/canonical/*.jsonl`, `data/reports/canon.json`
- golden-file tests תחת `tests/fixtures/canon/`

## הכרעות מתכנן (לא לפתוח מחדש)
1. **Document.key** = `KIP-N` מהכותרת. עמודים בלי מספר → `kind=Page`, `key=confluence:<id>`. התנגשויות (אותו KIP-N ב-2+ עמודים): העמוד הקנוני = הכותרת שמתחילה ב-`KIP-N:` בלי `[DRAFT]`/`Copy of`/`OLD`/release-notes; האחרים → `kind=Page` עם `labels += ["kip-variant"]` ו-`ancestors` שמכיל את ה-key הקנוני. הדוח מפרט כל התנגשות והכרעה.
2. **Blacklist** `KAFKA-1`. **Allowlist** למפתחות issue: `{KAFKA}` בלבד (הסינתטי יוסיף `XT/XE/XP/XS/ADO` בשלב 04). refs שנפלו בסינון → נספרים בדוח עם top-20 "מפתחות" שהוסרו.
3. **Person.id** = `"<source>:<key>"`: Jira `name`; Confluence `userKey` (fallback `username`, fallback `displayName` slug); git `email` (lowercase). `resolved=false` (שדה חדש במודל מותר — ראה carryover). כל identity שומרת `display`.
4. **Change.id**: commit = sha; PR = `pr:<N>` (ריפו יחיד ב-POC — מגבלה ידועה, לתעד בדוח).
5. **HTML→Markdown**: ספרייה מתוחזקת אחת, מוצמדת. לשמור כותרות (`#`), טבלאות כטקסט סביר, קישורים כ-`[text](url)` (ה-URL הוא ref!). `KIP-929` (body ריק) → `body_md=""` + אזהרה בדוח, לא כשל.
6. **Dedupe** בין base ל-`since-*/`: לפי `raw_layout` — האחרון לפי `updated`/`version` מנצח.
7. `write_jsonl` → temp+rename (carryover מ-Plan 0).

## קריטריוני קבלה
- [ ] ספירות: WorkItems = issues ייחודיים; Documents = pages ייחודיים; Changes = commits (+ PR records נגזרים מ-`(#N)`); Persons = זהויות ייחודיות; Containers = components+versions+space.
- [ ] כל WorkItem עם ≥1 component; כל Document (למעט KIP-929) עם `body_md` לא ריק; כל Change עם `at`.
- [ ] ≥20% מה-WorkItems עם ref issue מטקסט בלבד (לא ב-`links[]`). הדוח מפריד `via_link` / `via_text` לכל מקור.
- [ ] דוח `unmapped_fields` לכל מקור (שמות שדות raw שנזרקו), `kip_key` הכרעות, refs שסוננו.
- [ ] golden files: דגימה אחת לכל מקור → רשומה קנונית צפויה; בדיקות mentions על allowlist/blacklist.
- [ ] ריצה חוזרת = פלט זהה בייטים; `make check` ירוק.

## מה תלמד בשלב הזה
כמה מהעקיבות מגיעה מטקסט לעומת קישורים פורמליים (במספרים), מה נזרק בנרמול, ואיך מפתח "פשוט" כמו KIP-N מתפצל למציאות של drafts/copies — הבעיה שהקורס מכנה "איכות הקלט".

## הערות למבצע
- הסוכן מתכנן וכותב את הקוד. אין כתיבה ל-Neo4j. אין LLM.
- להוסיף `resolved: bool = False` ל-`Person` ב-`models.py` (שינוי מודל מאושר ע"י המתכנן).
- לא לגעת ב-`brain/harvest/` מעבר לקריאה.
