# Plan 2 — אחזור + MCP (brief-style)

Spec: §4 (אסטרטגיות, router, כלי MCP, אריזת הקשר, אבטחה), §2.3 (15 שאלות הכשירות). Roadmap gate: **כל 15 שאלות הכשירות נענות במצב B (הסוכן השואל דרך MCP) עם ציטוטים תקפים; ה-guard חוסם 100% ממקרי הכתיבה בבדיקות.**

עיקרון ביצוע (כמו Plan 1): המתכנן כותב חוזים וקריטריונים; הסוכנים מתכננים וכותבים את הקוד; `brain-reviewer` סוקר; המתכנן מכריע ומשלים את שכבת ההוראה בשיעור.

**מה המשתמש רואה בסוף:** שאלה בעברית/אנגלית → תשובה מצוטטת מהגרף, דרך Claude Code עם `.mcp.json`. זו "התוצאה הראשונה" של הפרויקט.

## הכרעות מתכנן (חלות על כל המשימות)
1. **ספרייה אחת, שני משטחים.** `brain/retrieve/` = Python טהור (להערכה head-to-head ב-Plan 3); `brain/mcp/server.py` = מעטפת FastMCP דקה שקוראת לאותן פונקציות. אין לוגיקת אחזור בשרת.
2. **חוזה תוצאה אחיד** (`brain/retrieve/types.py`, pydantic):
   ```python
   class Provenance(BaseModel):
       chunk_id: str | None = None; quote: str | None = None
       batch_id: str | None = None; model: str | None = None; source: str | None = None
   class Item(BaseModel):
       kind: str            # Chunk | WorkItem | Document | Person | Change | Container | Entity | Community | Row
       key: str             # KAFKA-…, KIP-…, sha, person id, entity id, community id, chunk id
       title: str = ""; snippet: str = ""; score: float = 0.0
       props: dict[str, Any] = {}          # small, whitelisted per kind
       provenance: list[Provenance] = []
   class Result(BaseModel):
       strategy: str; items: list[Item]; cypher_used: list[str] = []
       latency_ms: int; truncated: bool = False; route: dict | None = None
   ```
   תקרת אריזה ~4k tokens (`3.49 chars/token` נמדד): קיטום לפי score, שומרים ≥1 פריט מכל kind, `truncated=true`.
3. **לוג קריאות** `data/logs/retrieval.jsonl`: `ts, question, strategy, cypher, latency_ms, hit_ids, tokens_out, mode` — כל קריאה, גם מה-MCP וגם מ-Python.
4. **וקטורים:** `SEARCH` אם זמין ב-Neo4j 2026.06, אחרת `db.index.vector.queryNodes` (deprecated, עדיין עובד) — לבדוק ולתעד. `neo4j-graphrag` מותר ל-S1/S2 רק אם השאילתות שלו רצות בלי שגיאה על 2026.06; אחרת מימוש ישיר. ההחלטה ותוצאת הבדיקה בדוח.
5. **Reranker מקומי** `BAAI/bge-reranker-v2-m3` (רב-לשוני) דרך `sentence-transformers` CrossEncoder, extra `local-embed`; דגל `rerank=False` ברירת מחדל; אם המודל לא נטען — אזהרה ו-fallback ללא rerank (לא כשל).
6. **סינתטי:** ברירת מחדל האחזור כולל סינתטי (שאלות Xray/ADO תלויות בו); דגל `include_synthetic=False` לכיבוי. כל Item מסמן `props.synthetic`.
7. **שאלות הכשירות** (`data/eval/competency.jsonl`, 15 EN + 4 HE): מפתחות אמיתיים נבחרים **בקוד** (למשל ה-issue עם הכי הרבה `TESTS`; ה-KIP עם הכי הרבה `REJECTS`; component עם הכי הרבה `RESOLVES`) — לא ידנית. `question-forger` (Plan 3) יחליף/ירחיב.
8. **Decision חלשות** (81% `weak=true`) — מסלול "למה הוחלט" מחזיר גם `MENTIONS` של Feature/Problem עם quotes; לא רק `DECIDES/MOTIVATED_BY`.
9. **מצב B דורש סשן חדש של Claude Code** (`.mcp.json` נטען בעלייה). המתכנן מודיע למשתמש בשלב 6.

---

## Task 1 — ספריית אחזור: S1, S2, S3, S6, lookup, explain_edge, impact, route (agent: `brain-retrieval-engineer`) · lesson 11

**Goal:** כל אסטרטגיה ניתנת לקריאה מ-Python ומ-`brain ask`, מחזירה `Result` אחיד, נרשמת ללוג.

**Contract**
- `brain/retrieve/{types,log,pack,vector,hybrid (S1),graph_vector (S2),local (S3),temporal (S6),lookup,explain,impact,route}.py`.
- S1 `search_chunks(query, k=10, mode="hybrid|vector|fulltext", rerank=False)`: vector על `chunk_embedding` + fulltext על Chunk.text (index `chunk_text` — ליצור אם `brain index` לא יצר), RRF.
- S2 `search_with_context(query, k=8, hops=1)`: chunk → parent → הרחבה 1–2 hops (`REFERENCES, LINKS_TO, TESTS, HAS_RUN, RESOLVES, IMPLEMENTS_KIP, ASSIGNED_TO(current), IN_COMPONENT`), הקשר מובנה ב-`props.neighbors`.
- S3 `local_search(query, kinds=None, depth=2, k=10)`: regex keys → lookup ישיר; אחרת embedding שאלה → top-k `Entity` (`entity_embedding`) → שכונה עומק ≤2 על `MENTIONS/DECIDES/MOTIVATED_BY/REJECTS/IMPLEMENTS/DEPENDS_ON/INTRODUCES_RISK/TESTS/RESOLVES`, דירוג degree×משקל, chunks ראייתיים עם quotes (הכרעה 8).
- S6 דטרמיניסטי: `status_at(key, date)`, `timeline(key)`, `changes_between(component, v1, v2)`, `assignees_over_time(key)` — מ-`StatusChange`, `FIX_VERSION`, `RESOLVES`, `ASSIGNED_TO{valid_from,valid_to}`.
- `lookup(key)`: KAFKA-/KIP-/sha/person id/entity id → צומת + שכונה (עד 50 שכנים, לפי type).
- `explain_edge(src, dst)`: provenance מלא של כל קשת ביניהם (quotes, chunk ids, batch, model, extracted_at, tier/score ל-SAME_AS).
- `impact(key_or_name, depth=2)`: issues פתוחים, tests + סטטוס ריצה אחרון, docs, commits על אותם קבצים.
- `route(question) -> {strategy, reason, confidence}` דטרמיניסטי לפי §4.2 (regex keys → S3/lookup; תאריך/גרסה → S6; אגרגציה EN/HE → S4; תמה EN/HE → S5; ברירת מחדל S2).
- CLI: `brain ask "<q>" [--strategy s1|s2|s3|s6|auto] [--k] [--rerank] [--json]` — מדפיס `Result` קריא + כותב ללוג.

**Acceptance**
- [ ] כל שאלה מ-`data/eval/competency.jsonl` שסוגה עקיבות/השפעה/רציונל/טמפורלי מחזירה ≥1 Item עם provenance תקף (chunk_id קיים) באסטרטגיה שה-route מציע — טבלה בדוח `data/reports/retrieve.json` (שאלה, אסטרטגיה, latency, #items, מפתחות).
- [ ] cross-lingual: 4 השאלות בעברית מחזירות את אותם מפתחות עוגן כמו המקבילות באנגלית (S1/S3).
- [ ] latency p50 < 1.5s ל-S1/S2/S3 ללא rerank (Ollama embedding כלול).
- [ ] unit tests על הקורפוס-מיני (`data/fixtures/mini`) לכל אסטרטגיה + `route` (≥20 שאלות EN/HE); `make check` + `make smoke`.

**Course link:** מודול 4 (דפוסי אחזור), 6 (temporal), 7 (provenance).

---

## Task 2 — Text2Cypher מוגן (S4) + reranker (agents: `brain-retrieval-engineer`; `cypher-author` ×1 לבנק הדוגמאות) · lesson 12

**Contract**
- `brain/retrieve/cypher_guard.py`: `run_cypher(cypher, params, timeout_s=10, limit=100)` — (1) `RoutingControl.READ`; (2) deny-list על tokens (`CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD CSV|CALL {` + כל `CALL` שאינו ב-allowlist: `db.index.*`, `apoc.meta.*`, `apoc.text.*`, `gds.*.stream` אסור); (3) `EXPLAIN` לפני ריצה — דחייה אם התוכנית מכילה אופרטור כתיבה; (4) timeout; (5) הזרקת `LIMIT` אם חסר. כל דחייה = `GuardError{reason}` + לוג.
- `get_schema()`: `apoc.meta.schema` מצומצם (labels, props, rel types עם כיוון וספירות) + `IndexMeta`; cache 10 דק'.
- `cypher_examples(question_type)`: בנק `data/eval/cypher_examples.jsonl` — 3–5 דוגמאות לכל אחד מ-4 סוגי השאלות, נכתבות ע"י `cypher-author` דרך batch (`data/batches/cypher/001.in.json` עם סכמה + השאלות → `.out.json`), **מאומתות בקוד** (guard + ריצה + ≥1 שורה) לפני קבלה.
- `brain/retrieve/rerank.py`: CrossEncoder מקומי (הכרעה 5); `brain doctor` מקבל check אופציונלי `reranker`.
- `brain ask --strategy s4` מקבל Cypher מוכן (`--cypher`) או שאלה + סוג (מצב A: המתכנן מפזר `cypher-author`).

**Acceptance**
- [ ] בדיקות guard: ≥25 מקרי כתיבה/הזרקה (כולל `CALL { CREATE … }`, `apoc.cypher.run*`, `LOAD CSV`, unicode/comment tricks) → 100% נחסמים; ≥10 שאילתות קריאה עוברות; timeout מוכח.
- [ ] בנק דוגמאות: 100% הדוגמאות רצות ומחזירות ≥1 שורה על הגרף החי.
- [ ] rerank: על 20 שאלות, שינוי ב-top-1 נמדד ומדווח (לא נדרש שיפור — נמדד ב-Plan 3).

**Course link:** מודול 4 (Text2Cypher), 10 (אבטחה — משטח תקיפה).

---

## Task 3 — Global search (S5) + שרת MCP + אריזת הקשר (agent: `brain-retrieval-engineer`) · lesson 13

**Contract**
- S5 `global_search(query, level="coarse|fine", k=5)`: embedding שאלה → `community_embedding` → reports (`title, summary, findings[] עם evidence_chunk_ids, rank`); ה-reduce אצל הסוכן השואל.
- `brain/mcp/server.py` (FastMCP): כל הכלים מטבלת §4.3 בשמות המדויקים; resources `brain://schema`, `brain://stats`; prompt `answer_with_citations`. פלט = `Result.model_dump()`. transport stdio (`brain serve --stdio`) + streamable HTTP (`brain serve --http 8765`; שירות `brain-mcp` ב-compose, port 127.0.0.1:8765).
- `.mcp.json` בשורש: server `brain` דרך `uv run brain serve --stdio`.
- `brain/retrieve/pack.py`: תקרת ~4k tokens לכל תשובה (הכרעה 2).
- `brain-analyst` ב-`.claude/agents/`: `tools:` מעודכן לרשימת כלי ה-MCP (`mcp__brain__*`).

**Acceptance**
- [ ] `brain serve --stdio` עונה ל-`tools/list` עם כל 15 הכלים + 2 resources + prompt; בדיקה אוטומטית דרך MCP client (Python SDK) על הקורפוס-מיני.
- [ ] HTTP: `curl` ל-`/mcp` מחזיר רשימת כלים; compose `up` → healthy.
- [ ] כל כלי מחזיר `Result` תקף (pydantic) ונרשם ללוג; `truncated` נכון על תשובה >4k tokens.
- [ ] `make check` + `make smoke` (smoke: stdio round-trip על 3 כלים).

**Course link:** מודול 8 (Agentic GraphRAG, MCP), 5 (community reports).

---

## Task 4 — שער Plan 2: 15 שאלות במצב B (planner + `brain-analyst` ×3, `brain-reviewer`) · lesson 13

**Contract**
- המשתמש מפעיל סשן חדש (`.mcp.json`); המתכנן מפזר `brain-analyst` ×3 (5 שאלות כל אחד, כולל 4 בעברית) — כל תשובה נשמרת ב-`data/eval/plan2_answers/<qid>.md` עם `strategy:` trace.
- קוד (`brain eval cite-check`) מאמת ציטוטים: כל `[KAFKA-…]/[KIP-…]/[chunk:…]` קיים בגרף; שיעור ציטוטים תקפים לכל תשובה.
- `docs/report/plan2-first-questions.md` (עברית, נוצר מקוד + פסקת מתכנן): שאלה, אסטרטגיות בשימוש, ציטוטים תקפים/סה"כ, latency, הערכת מתכנן (נכון/חלקי/שגוי).

**Acceptance (gate)**
- [ ] 15/15 נענות עם ≥1 ציטוט תקף; ≥12/15 נכונות לפי המתכנן (ההערכה המלאה ב-Plan 3).
- [ ] guard: 100% ממקרי הכתיבה בבדיקות נחסמים (מ-Task 2).
- [ ] `docs/planning/progress.md` מעודכן; שיעורים 11–13; merge ל-`main`.

## Self-review (planner)
- כל כלי מ-§4.3 מכוסה ב-Task 1–3 (`search_chunks, search_with_context, lookup, local_search, get_schema, cypher_examples, run_cypher, global_search, status_at, timeline, changes_between, assignees_over_time, impact, explain_edge, route`) — 15 כלים.
- S5 תלוי ב-communities (Plan 1 שלב 09) — Task 3 רץ אחרי merge הסיכומים; Task 1–2 לא תלויים.
- מקביליות: Task 1 ו-Task 2 במקביל (קבצים נפרדים; `types.py` בבעלות Task 1, Task 2 מייבא); Task 3 אחרי שניהם.
