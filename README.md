# מוח ארגוני — Graph RAG POC

POC לימודי: מוח ארגוני על דאטה אמיתי (Apache Kafka: Jira, Confluence/KIPs, git) + שכבת Xray/ADO סינתטית,
בנוי על Neo4j, embeddings מקומיים (bge-m3, עברית+אנגלית) וסוכני Opus כ-"LLM" של הפייפליין. אפס API חיצוני.

- עיצוב: `docs/superpowers/specs/2026-09-03-org-brain-graph-rag-design.md`
- תכניות: `docs/superpowers/plans/` (roadmap + Plan 0; Plans 1–3 נכתבות לפני הביצוע שלהן)
- החלטות (ADR): `docs/decisions/`
- שיעורים לכל שלב: `docs/lessons/`
- התקדמות: `docs/planning/progress.md`
- סוכנים ומוסכמות: `.claude/agents/`, `docs/agents/conventions.md`

## הקמה (Mac)

```bash
# 1. Python + תלויות (Python 3.11 דרך uv)
uv venv --python 3.11 && uv sync --extra dev

# 2. Neo4j (APOC + GDS מובנים ב-image; פורטים על 127.0.0.1 בלבד)
cp -n .env.example .env
make up

# 3. embeddings מקומיים — Ollama נייטיב (לא Docker: אין GPU ל-Docker על Mac)
#    אם Ollama לא מותקן: brew install ollama   (או האפליקציה מ-ollama.com)
ollama serve &          # או להפעיל את אפליקציית Ollama
ollama pull bge-m3

# 4. בדיקת סביבה — חייב להיות 7/7 OK
uv run brain doctor

# 5. בדיקות
make check    # ruff + בדיקות יחידה (offline)
make smoke    # doctor + בדיקות live מול Neo4j ו-Ollama
```

הערה: `ollama serve` הוא תהליך ידני — אחרי reboot צריך להפעיל שוב (או להשתמש באפליקציית Ollama שרצה ברקע). `brain doctor` יגיד `[FAIL] ollama` אם הוא לא רץ.

## פתרון תקלות

- **GDS/APOC לא נטענים או ה-image לא קיים:** ב-`.env` להגדיר `NEO4J_IMAGE=neo4j:5.26`, ואז `make down && docker volume rm graph-rag_neo4j_data graph-rag_neo4j_logs && make up`. לאמת: `docker exec brain-neo4j cypher-shell -u neo4j -p brainpass "RETURN apoc.version(), gds.version()"`.
- **`[FAIL] ollama`:** להריץ `ollama serve` (או לפתוח את אפליקציית Ollama) ולוודא `ollama list` מציג `bge-m3`.
- **`embed_dim` לא תואם:** האינדקס והשאילתות חייבים אותו מודל; לא לשנות `EMBED_MODEL`/`EMBED_DIM` בלי לבנות מחדש את האינדקסים.

## הפייפליין

`brain harvest → canon → load → chunk → extract → resolve → communities → index → serve → eval`

`brain --help` מסביר כל שלב. כל השלבים ממומשים (Plans 0–3); `docs/planning/progress.md` הוא מקור האמת ל"איפה אנחנו".

## איך שואלים את המוח

**מהטרמינל (אסטרטגיה קבועה):**
```
uv run brain ask "למה נבחר פרוטוקול ה-rebalance ב-KIP-848?"          # router בוחר
uv run brain ask "Which tests cover KAFKA-14649?" --strategy s3        # s1|s2|s3|s4|s5|s6
uv run brain ask "Who owns streams?" --strategy s4 --type traceability  # Text2Cypher מבנק הדוגמאות
```
כל פריט חוזר עם ציטוט (`chunk_id` + quote) או שורה (`cypher_used`).

**דרך Claude Code (אגנטי, מצב B):** `.mcp.json` בשורש מפעיל `brain serve --stdio` בעליית סשן — פתח סשן חדש בתיקייה, אשר את שרת ה-MCP, `/mcp` יראה `brain` עם 15 כלים. הסוכן `brain-analyst` עונה עם ציטוטים ש-`brain eval cite-check` מאמת מול הגרף. HTTP: `docker compose --profile mcp up -d` → `http://127.0.0.1:8765/mcp`.

**מה עובד מתי (נמדד, `docs/report/eval-report.md`):** שאלות עם מפתח → `lookup` + `local_search`; אגרגציות ומצב-בנקודת-זמן → `run_cypher`/כלי הזמן; נושאים → `global_search`; "מה כתוב על X" → `search_chunks` (baseline) מספיק. אמינות (faithfulness) 2.0 בכל אסטרטגיה — הכשלים הם באחזור, לא בהזיה.

**הערכה מחדש:** `brain eval questions build|merge` → `brain eval run --mode fixed` → `brain eval answers build|merge` → `brain eval judge build|merge` → `brain eval report`. סוכני-LLM (`answer-writer`, `eval-judge`, `question-forger`) רצים על ה-batches ב-`data/batches/`.

## חיבור מערכת חדשה

כל המקורות יושבים ב-`sources.yaml` בשורש — כתובת, שאילתה (JQL/CQL/WIQL/חלון commits), `project_keys` ושם משתנה הסביבה של הטוקן. **אין כתובת, שאילתה או מפתח פרויקט בקוד.**

```bash
uv run brain harvest --source <id>   # רק המקור החדש
uv run brain canon && uv run brain load
```

מערכת חדשה = שלושה קבצים (connector, mapper, בדיקת golden) ורשומה אחת ב-`sources.yaml`. הגבול הוא המודל הקנוני: שום שלב אחרי `brain canon` לא יודע מאיפה הדאטה הגיע.

טוקנים רק במשתני סביבה (`.env`, ראו `.env.example`); לא מוגדר = אנונימי, וזה מצב העבודה הרגיל מול ה-endpoints הציבוריים של ASF. טוקן אף פעם לא נכתב ללוג, לדוח או ל-checkpoint.

**המדריך המלא — כולל שלדים עובדים ל-Azure DevOps ול-Xray:** [`docs/guides/adding-a-connector.md`](docs/guides/adding-a-connector.md).

## איפוס

```bash
uv run brain reset --synthetic --yes   # רק השכבה הסינתטית; הקורפוס האמיתי נשאר טעון
uv run brain reset --graph --yes       # כל הצמתים; constraints ו-indexes נשארים
uv run brain reset --data --yes        # data/raw|canonical|batches|reports|eval
uv run brain reset --all --yes         # graph + data
```

בלי `--yes` שום דבר לא נמחק — הפקודה מדפיסה מניפסט של מה שהייתה מוחקת ויוצאת עם קוד 1. `data/fixtures/` אף פעם לא נמחק. אחרי `--all --yes`: `brain doctor` נשאר 7/7 וכל ספירה בגרף היא 0. פירוט ב-[מדריך](docs/guides/adding-a-connector.md#9-brain-reset--למחוק-את-דאטה-הבדיקות).

## עקרונות שלא מתפשרים עליהם

1. **סוכן = ה-LLM.** כל שלב שדורש מודל שפה רץ כ-batch לסוכן Opus שכותב JSON. רק קוד דטרמיניסטי כותב ל-Neo4j. סוכני-LLM מקבלים רק `Read, Write, Glob` — פיזית אין להם shell.
2. **embeddings מקומיים ודו-לשוניים.** `bge-m3` דרך Ollama. מודל+מימד נאכפים; אי-התאמה = שגיאה קשה, לא fallback שקט.
3. **מודל קנוני.** כל מערכת (Jira/ADO/Xray/Confluence/git) ממופה ל-5 טיפוסים ב-`brain/canon/models.py`. קונקטור חדש = mapper חדש, לא סכמה חדשה.
4. **Read-only נאכף בשרת.** `GraphClient.read()` רץ ב-`RoutingControl.READ` — Neo4j עצמו דוחה כתיבה (`Neo.ClientError.Statement.AccessMode`).
5. **provenance על כל דבר שיצא מ-LLM.** `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at`.

## סוכנים

`.claude/agents/` — 16 סוכנים: הנדסה (`brain-infra`, `brain-ingest-engineer`, `brain-graph-engineer`, `brain-retrieval-engineer`, `brain-eval-engineer`), סוכני-LLM (`synthetic-org-generator`, `kg-extractor`, `entity-adjudicator`, `community-summarizer`, `cypher-author`, `question-forger`, `eval-judge`, `answer-writer`, `brain-analyst`), איכות (`brain-reviewer`, `lesson-writer`). מוסכמות: `docs/agents/conventions.md`.
