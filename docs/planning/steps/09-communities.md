# שלב 09 — `brain communities` (Plan 1, Task 8)

**תכנית:** Plan 1 · **סוכנים:** `brain-graph-engineer` (GDS + batches + merge) + `community-summarizer` ×2 · **מודול בקורס:** 5 ("להבין את פייפליין האינדוקס" — community reports), נספח LightRAG

## מטרה
תשתית ל-Global search: קהילות היררכיות (Leiden) עם דוחות מסוכמים ומצוטטים — בלי MS GraphRAG.

## קלטים
- גרף אחרי extract merge + resolve (Entity, MENTIONS, יחסי LLM, Person ממוזגים)
- GDS 2026.06 (`gds.graph.project`, `gds.leiden`), `OllamaEmbedder`
- spec §3.7, §4.1 (S5), Task 8 בתכנית, `.claude/agents/community-summarizer.md`

## פלטים
- `brain/community/`, פקודות `brain communities build [--levels 2] [--min-size 5]`, `brain communities batches`, `brain communities merge`, `data/reports/communities.json`
- צמתי `Community{id, level, size, member_hash, title, summary, findings[], rank, embedding}` + `IN_COMMUNITY{level}`; vector index `community_embedding`

## הכרעות מתכנן
1. **Projection (GDS, undirected, in-memory):** צמתים `Entity, WorkItem, Document, Component`; קשתות `MENTIONS` (דרך Chunk→parent: להקריס ל-Entity–Parent), `REFERENCES`, `LINKS_TO`, `IMPLEMENTS`, `DEPENDS_ON`, `IN_COMPONENT`, `DECIDES`, `MOTIVATED_BY`, `REJECTS`, `RESOLVES`→ (Commit לא בפרויקציה; להקריס Commit→WorkItem↔Document דרך `IMPLEMENTS_KIP` כקשת WorkItem–Document). **לא** Chunk, **לא** Person, **לא** סינתטי (`synthetic=true` מחוץ לפרויקציה ב-v1 — כדי שהקהילות ישקפו את הארגון האמיתי; דגל `--include-synthetic`).
2. **Leiden:** `includeIntermediateCommunities: true`, 2 רמות (fine = הרמה האחרונה; coarse = רמה אחת מעליה), seed קבוע, `gamma` ברירת מחדל; קהילות בגודל <5 (fine) מתמזגות ל-"misc" ברמה coarse בלבד ולא מסוכמות. יעד: 100–200 קהילות מסוכמות סה"כ.
3. **Community.id** = `L<level>-<leidenId>`; `member_hash` = sha1 של מפתחות החברים ממוינים; `IN_COMMUNITY{level}` מכל חבר.
4. **Batches לסוכן** (≤40KB, מודפס-יפה): לכל קהילה — top-25 חברים לפי degree עם `key, kind/label, name/title, description`, + עד 8 chunks ראייתיים (הכי מקושרים ל-MENTIONS של החברים) עם `chunk_id, parent_key, text` (≤600 תווים כל אחד). 2 shards.
5. **Merge:** ולידציה (schema; כל `evidence_chunk_ids` קיימים ושייכים לחברי הקהילה); `title, summary, findings[]{statement, evidence_chunk_ids[]}, rank, rank_reason`; provenance (`batch_id, model:"opus:community-summarizer", extracted_at`); embedding של `title + summary` → `Community.embedding`; vector index `community_embedding`; **re-summarize רק אם `member_hash` השתנה** (ledger).
6. **דוח:** קהילות לפי רמה, התפלגות גודל, modularity, זמן GDS, קהילות ללא סיכום (misc), findings ללא ראיה (חייב 0), דוגמה: 3 הכותרות עם rank הגבוה ביותר.

## קריטריוני קבלה
- [ ] 100–200 קהילות מסוכמות; כל report עם ≥1 finding ו-≥1 `evidence_chunk_id` תקף; 0 findings בלי ראיה.
- [ ] `community_embedding` ONLINE; `IN_COMMUNITY` לכל חבר ברמה fine; ריצת build חוזרת = אותם `member_hash` (דטרמיניזם עם seed) ו-0 סיכומים מחדש.
- [ ] `make check` + `make smoke` (Leiden על הקורפוס-מיני → ≥1 קהילה; merge עם פלט סוכן מדומה).

## מה תלמד בשלב הזה
מה MS GraphRAG עושה מאחורי הקלעים (Leiden + community reports) ב-200 שורות משלך; למה קהילות על גרף ידע ≠ clustering של embeddings; ומה עולה סיכום היררכי.

## הערות למבצע
- הסוכן-מהנדס לא מריץ את המסכמים; המתכנן מפזר.
- GDS דורש projection בזיכרון — `gds.graph.drop` בסוף, תמיד.
