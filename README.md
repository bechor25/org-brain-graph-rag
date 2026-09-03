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

## הפייפליין

`brain harvest → canon → load → chunk → extract → resolve → communities → index → serve → eval`

`brain --help` מסביר כל שלב; שלבים שטרם מומשו יוצאים עם קוד 2 ואומרים באיזו תכנית הם מגיעים.

## עקרונות שלא מתפשרים עליהם

1. **סוכן = ה-LLM.** כל שלב שדורש מודל שפה רץ כ-batch לסוכן Opus שכותב JSON. רק קוד דטרמיניסטי כותב ל-Neo4j. סוכני-LLM מקבלים רק `Read, Write, Glob` — פיזית אין להם shell.
2. **embeddings מקומיים ודו-לשוניים.** `bge-m3` דרך Ollama. מודל+מימד נאכפים; אי-התאמה = שגיאה קשה, לא fallback שקט.
3. **מודל קנוני.** כל מערכת (Jira/ADO/Xray/Confluence/git) ממופה ל-5 טיפוסים ב-`brain/canon/models.py`. קונקטור חדש = mapper חדש, לא סכמה חדשה.
4. **Read-only נאכף בשרת.** `GraphClient.read()` רץ ב-`RoutingControl.READ` — Neo4j עצמו דוחה כתיבה (`Neo.ClientError.Statement.AccessMode`).
5. **provenance על כל דבר שיצא מ-LLM.** `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at`.

## סוכנים

`.claude/agents/` — 15 סוכנים: הנדסה (`brain-infra`, `brain-ingest-engineer`, `brain-graph-engineer`, `brain-retrieval-engineer`, `brain-eval-engineer`), סוכני-LLM (`synthetic-org-generator`, `kg-extractor`, `entity-adjudicator`, `community-summarizer`, `cypher-author`, `question-forger`, `eval-judge`, `brain-analyst`), איכות (`brain-reviewer`, `lesson-writer`). מוסכמות: `docs/agents/conventions.md`.
