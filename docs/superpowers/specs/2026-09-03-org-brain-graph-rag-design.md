# מוח ארגוני Graph RAG — מסמך עיצוב (POC)

תאריך: 2026-09-03 · מתכנן: Claude Fable 5.1 · מבצע: סוכני Opus 5 · סטטוס: כל 7 הסעיפים מאושרים (2026-09-03); ממתין לסקירת המשתמש לפני תכנית מימוש

מטרת ה-POC: ללמוד, במספרים, "מתי איזו אסטרטגיית Graph RAG" על דאטה ארגוני אמיתי (Jira/Confluence/git + Xray/ADO), ולהגיע ליכולת לאנדקס, לפרסר ולהתממשק לכל מערכת ארגונית לתוך מוח אחד עם קשרים איכותיים. ההחלטות המכוננות ב-`docs/decisions/`.

**Scope:** פרויקט עצמאי אחד ב-`graph-rag/`, ארבעה שלבי-על (קורפוס וגרף → אחזור ו-MCP → הערכה → דוח). מחוץ ל-scope: אינטגרציה ל-NessBot (ADR-0004), API חיצוני כלשהו (ADR-0002/0003), מיילינג-ליסטים/Slack/AST/RDF/Graphiti (2.5), הרשאות ברמת צומת (4.5).

---

## סעיף 1 — ארכיטקטורה ופריסה ✅ מאושר

### רכיבים

| רכיב | מימוש | נימוק |
|---|---|---|
| Graph DB | Neo4j 2026.x community + APOC + GDS Community (Leiden) | LPG, Cypher, vector + fulltext index מובנים. GDS מספק קהילות בלי MS GraphRAG. `brain doctor` מאמת שה-image tag תומך ב-GDS; אם לא — מצמידים tag שתומך |
| Embedding | Ollama נייטיב (Metal) עם `bge-m3`, `/v1/embeddings` תואם-OpenAI. fallback: sentence-transformers בתהליך | עברית+אנגלית, 1024-dim, 8k tokens. Docker על Mac ללא GPU |
| Brain CLI | חבילת Python 3.11 (`uv`): `brain harvest / canon / load / chunk / extract / resolve / communities / index / serve / eval` | כל שלב = פקודה idempotent עם תיקיית פלט — ניתן להרצה חוזרת ול-diff |
| MCP server | FastMCP; stdio ל-Claude Code, HTTP ב-compose | הממשק היחיד החוצה; מה שיפורסס ל-NessBot |
| Tracing | JSONL לכל שאילתה: route, cypher, latency, tokens, hits | מספיק לטבלאות הערכה; בלי Langfuse (YAGNI) |

### זרימת דאטה

```
sources ─harvest→ data/raw/*.json ─canon→ data/canonical/*.jsonl
   ─load→ Neo4j (structured nodes/edges, deterministic, no LLM)
   ─chunk+embed→ :Chunk + vector index
   ─extract(agents)→ data/batches/extract/**/*.out.json ─validate+merge→ :Entity + relations
   ─resolve→ SAME_AS merges (deterministic keys → embedding similarity → agent queue)
   ─communities→ GDS Leiden → :Community ─summarize(agents)→ community reports
   ─index→ fulltext/vector on entities + communities
MCP tools ←retrieve← router  ←→  eval (baseline vs graph, per question type)
```

### מבנה ריפו

```
graph-rag/
├── docker-compose.yml       neo4j(+apoc,gds), brain-mcp
├── pyproject.toml           uv, גרסאות מוצמדות (מהקורס)
├── brain/                   harvest/ canon/ graph/ chunk/ extract/ resolve/ community/ retrieve/ mcp/ eval/ embed/
├── data/                    raw/ canonical/ batches/ reports/ eval/ fixtures/ (gitignored למעט fixtures)
├── docs/
│   ├── superpowers/specs/   מסמך זה + תכנית מימוש
│   ├── planning/            יומן תכנון
│   ├── decisions/           ADR לכל הכרעה
│   └── lessons/             שיעור בעברית לכל שלב: מה, למה, מה למדנו, מספרים
└── .claude/agents/          סוכני Opus 5
```

### עקרון "סוכן כ-LLM"

כל שלב שדורש LLM מפוצל ל-batches: קובץ קלט JSON + סכמה + דוגמאות → סוכן → קובץ פלט JSON → ולידציה pydantic; batch שבור נדחה ומורץ שוב. הסוכן לעולם לא כותב ל-Neo4j; רק קוד דטרמיניסטי כותב. תוצאה: replay, diff, מדידה.

---

## סעיף 2 — קורפוס, מודל קנוני, סכמת גרף ✅ מאושר

### 2.1 פרוסת הקורפוס (Kafka)

| מקור | מה נלקח | איך | גודל יעד |
|---|---|---|---|
| Jira (`issues.apache.org/jira`) | project=KAFKA, רכיבים `streams`, `connect`, `clients`, חלון זמן ~2023-01 → 2025-12; שדות מלאים + comments + `expand=changelog` | REST v2 אנונימי, JQL, paging | 1,500–3,000 issues |
| Confluence (`cwiki.apache.org`) | space KAFKA: דפי `KIP-NNN: …` שמוזכרים ב-issues של הפרוסה + דפי ילדים/דיונים | REST אנונימי, `body.storage` → Markdown | 80–150 דפים |
| GitHub (`apache/kafka`) | commits עם `KAFKA-NNNN` בהודעה בחלון הזמן; PR numbers מההודעות `(#NNNN)`; קבצים שנגעו | `git clone --filter=blob:none` + parsing מקומי (בלי API); PR metadata דרך API רק אם יש token | 3,000–6,000 commits |
| Xray (סינתטי) | Test / TestSet / TestPlan / TestExecution / TestRun / Precondition, מקושרים ל-stories/bugs אמיתיים | סוכן-מייצר מקבל stories אמיתיים → tests עם steps נגזרים; executions לפי fix versions; כשלונות מתואמים לבאגים אמיתיים | 1–4 tests per story |
| ADO (סינתטי + probe) | Epic/Feature/Story/Task/Bug, Iterations, Area paths, PR links; Epics = KIPs, Stories מקושרים ל-Jira keys | probe לפרויקט ADO ציבורי (דגימת קונקטור אמיתי); שכבה מקושרת נוצרת ע"י סוכן עם רעש מכוון | ~500 work items |

**רעש מכוון בשכבה הסינתטית (כי זה מה שיש בארגון אמיתי):** אותם אנשים בשמות שונים ("Rao, Jun" / "jrao" / "Jun Rao"); קישורים שמופיעים רק בטקסט ("see KAFKA-15123") ולא כ-link פורמלי; פריטים מיושנים (state לא עודכן); שמות פיצ'רים שונים מעט בין Xray ל-Jira. כל צומת סינתטי מסומן `synthetic=true`.

**הכרעה על רכיבים:** רשימה קבועה `streams`, `connect`, `clients` (רפרודוציבילי). ה-harvester מפיק דוח צפיפות קישורים (issues↔KIPs↔commits לכל רכיב); מחליפים רכיב רק אם הדוח מראה צפיפות נמוכה.

**Fallback:** אם Kafka Jira לא נגיש — Apache Flink (Jira + FLIPs ב-Confluence, אותה תבנית). ה-harvester בודק נגישות לפני הורדה.

### 2.2 מודל קנוני (pydantic) — הליבה של "איך מאנדקסים כל מערכת"

כל מקור ממופה ל-5 טיפוסים בלבד. זה הלקח המרכזי: קונקטור חדש = mapper חדש למודל הקנוני, לא סכמת גרף חדשה.

| טיפוס | מקורות | שדות עיקריים |
|---|---|---|
| `WorkItem` | Jira issue, ADO work item, Xray Test/Execution (תת-סוגים) | id, source, type, title, description, status, priority, created/updated, reporter, assignee, components[], labels[], fix_versions[], affects_versions[], parent, links[]{type,target}, comments[]{author,at,body}, changelog[]{field,from,to,at,by} |
| `Document` | Confluence page, KIP, README | id, source, space, title, body_md, version, created/updated, author, ancestors[], labels[], links[] |
| `Person` | Jira user, Confluence author, git name+email, ADO identity | identities[]{source, key, display, email} — צומת אחד אחרי resolution |
| `Change` | Commit, PullRequest | id, message, author, at, files[], refs[]{kind: issue/kip/pr, key} |
| `Container` | Component, Version, Sprint/Iteration, AreaPath, Space | id, source, kind, name, parent |

**Mentions דטרמיניסטיים לפני כל LLM:** regex על כל טקסט — `KAFKA-\d+`, `KIP-\d+`, `#\d+`, URLs ל-Jira/Confluence/GitHub, `@user`. יוצרים `REFERENCES{via:"text"}` לצד קישורים פורמליים `{via:"link"}`. חלק גדול מהעקיבות הארגונית מגיע מכאן, בחינם.

### 2.3 שאלות כשירות (15) — הסכמה נגזרת מהן

המפתחות בדוגמאות (KAFKA-15123, KIP-848, KIP-932, KIP-1000) הם להמחשה; `question-forger` מחליף אותם במפתחות אמיתיים מהפרוסה אחרי ה-harvest.

| # | סוג | דוגמה (EN) | דוגמה (HE — בדיקת cross-lingual) |
|---|---|---|---|
| 1–4 | עקיבות חוצת-מערכות | Which tests cover KAFKA-15123 and what was their last execution status? · Which commits fixed the bug behind KIP-848 rollout issues? · Who owns component `connect` (most assignments/commits last year)? · Which ADO story delivers KIP-932? | אילו טסטים מכסים את KAFKA-15123 ומה הסטטוס האחרון שלהם? |
| 5–8 | ניתוח השפעה | If we change `GroupCoordinator`, which open issues, tests, and KIPs are affected? · Which components have failing tests tied to open bugs in 3.7? · What depends on the consumer rebalance protocol? | אם נשנה את GroupCoordinator — אילו issues פתוחים, טסטים ודוקים מושפעים? |
| 9–11 | רציונל החלטות | Why was the incremental rebalance protocol chosen in KIP-848? · What alternatives were rejected in KIP-932 and why? · What problem motivated KIP-1000? | למה נבחר פרוטוקול ה-rebalance ב-KIP-848? |
| 12–15 | גלובלי / טמפורלי | What are the main themes of open bugs in streams? · What changed in `connect` between 3.6 and 3.7? · What was the status of KAFKA-15123 on 2024-03-01? · Who was assigned to KIP-848 work over time? | מה השתנה ב-connect בין 3.6 ל-3.7? |

### 2.4 סכמת גרף (LPG, Neo4j)

**צמתים מובנים (מ-load, ללא LLM):** `WorkItem{type: Issue|Story|Bug|Epic|Task}`, `Test`, `TestExecution`, `TestRun`, `Document{kind: KIP|Page}`, `Person`, `Component`, `Version`, `Sprint`, `Commit`, `PullRequest`, `File`, `Chunk`, `StatusChange` (צומת-אירוע מה-changelog: field, from, to, at).

**קשתות מובנות:** `ASSIGNED_TO{valid_from,valid_to}`, `REPORTED_BY`, `AUTHORED`, `IN_COMPONENT`, `FIX_VERSION`, `AFFECTS_VERSION`, `PARENT_OF`, `LINKS_TO{type}`, `REFERENCES{via}`, `TESTS`, `IN_PLAN`, `EXECUTED_IN`, `HAS_RUN{status}`, `IN_SPRINT`, `TOUCHES` (Commit→File), `RESOLVES` (Commit→WorkItem), `HAS_CHUNK`, `HAS_CHANGE` (WorkItem→StatusChange), `SAME_AS` (לפני merge סופי).

**צמתים/קשתות מחילוץ LLM (מ-extract, מונחה-סכמה):** `Entity{kind: Feature|Decision|Problem|Alternative|Risk|Technology}`; `MENTIONS` (Chunk→Entity, עם quote), `DECIDES` (Document→Decision), `MOTIVATED_BY` (Decision→Problem), `REJECTS` (Decision→Alternative, reason), `IMPLEMENTS` (WorkItem→Feature), `DEPENDS_ON` (Component/Feature→Component/Feature), `INTRODUCES_RISK`.

**קהילות:** `Community{level, summary, title}` + `IN_COMMUNITY` — מ-GDS Leiden על תת-גרף Entity+WorkItem+Document.

**זמן:** שתי שכבות. (א) `StatusChange` כצמתי-אירוע — עונה "מה היה הסטטוס בתאריך D". (ב) `valid_from/valid_to` על `ASSIGNED_TO` — עונה "מי היה אחראי לאורך זמן". זה תת-קבוצה של Graphiti בלי התלות; מספיק ל-POC ומלמד את העיקרון.

**מפתחות ואילוצים:** `WorkItem.key` (KAFKA-1234 / ADO-77 / XT-12), `Document.id`, `Commit.sha`, `Person.id` (אחרי resolution), `Entity.norm_name+kind`. unique constraints על כולם; vector index על `Chunk.embedding` ו-`Entity.embedding`; fulltext על `WorkItem.title+description`, `Document.title+body`, `Entity.name`.

### 2.5 מה לא נכנס (YAGNI)

מיילינג-ליסטים, Slack, קוד מקור כ-AST, RDF/ontologies, Graphiti כתלות. כולם מועמדים להרחבה אחרי שיש מספרים.

---

## סעיף 3 — פייפליין בנייה ✅ מאושר

עיקרון: כל שלב = פקודת CLI אחת, idempotent, עם תיקיית פלט ודוח JSON (`data/reports/<step>.json`). אחרי כל שלב הסוכן המבצע כותב `docs/lessons/NN-<step>.md` (תבנית: מה עשינו · למה (מודול בקורס) · מה הפתיע · מספרים · מה היינו משנים).

### 3.1 `brain harvest` — קונקטורים

- קונקטור לכל מקור תחת `brain/harvest/<source>.py` עם ממשק אחיד: `probe() → bool`, `fetch(since, checkpoint) → iter[raw]`, `checkpoint` לקובץ — ניתן להמשך אחרי נפילה.
- Jira: JQL לפי רכיב+חלון, `fields=*all&expand=changelog`, paging 100, backoff על 429. Confluence: חיפוש `title ~ "KIP-"` ב-space KAFKA + `body.storage` → Markdown (markdownify). GitHub: clone רדוד + `git log --name-only` → parsing מקומי. ADO probe: WIQL על פרויקט ציבורי; אם נגיש — דגימה של 50 work items אמיתיים לתיעוד מבנה בלבד.
- פלט: `data/raw/<source>/*.json` + `data/reports/harvest.json` (ספירות, דוח צפיפות קישורים לכל רכיב, שגיאות).

### 3.2 `brain canon` — נרמול למודל הקנוני

- mapper לכל מקור → 5 קבצי `data/canonical/*.jsonl`. ולידציה pydantic; שדות שלא מופו נרשמים בדוח (זה מלמד מה אבד בנרמול).
- חילוץ mentions דטרמיניסטי (regex) על כל טקסט → `refs[]` בכל רשומה.
- שכבה סינתטית (Xray/ADO): סוכן-מייצר מקבל batches של stories/bugs/KIPs אמיתיים + מפרט רעש → כותב JSONL קנוני ישירות (אותה סכמה, `synthetic=true`). הרעש מוגדר ב-`brain/canon/synthetic_spec.md`, והסוכן כותב גם `data/canonical/synthetic_truth.json` (מיפוי זהויות/קישורים אמיתי) — ground truth מכונה-קריא ל-resolution ולהערכה.

### 3.3 `brain load` — טעינה מובנית (ללא LLM)

- constraints + indexes קודם. `UNWIND $rows MERGE` ב-batches של 1,000 דרך neo4j driver.
- `StatusChange` מ-changelog; `ASSIGNED_TO{valid_from,valid_to}` נגזר ממעברי assignee. `Person` — צומת לכל זהות בשלב זה (`identity_key`), האיחוד ב-3.6.
- `REFERENCES{via:text|link}` מ-refs; `RESOLVES` מ-commit refs.

### 3.4 `brain chunk` — יחידות טקסט + embedding

- Document: לפי כותרות, 500–800 tokens, overlap 50. WorkItem: description כ-chunk אחד (או פיצול), כל comment = chunk עם author+at. Commit: message כ-chunk אחד (ללא פיצול).
- כל Chunk: `id (hash), parent_key, kind, position, text, lang`. embedding דרך Ollama `bge-m3`, batches של 64; vector index `chunk_embedding` (cosine, 1024).
- שינוי טקסט = hash חדש → re-embed רק מה שהשתנה (בסיס לעדכון אינקרמנטלי).

### 3.5 `brain extract` — חילוץ מונחה-סכמה ע"י סוכנים

- **סדר עדיפות (תקציב זמן-סוכן):** Phase A = KIPs + descriptions של issues שמפנים ל-KIP או מסוג Bug/Improvement עם >300 תווים (~2.5k chunks). Phase B (אופציונלי, אחרי מספרים) = comments.
- batch = 20–25 chunks עם הקשר (parent title/key/type) → `data/batches/extract/<shard>/<n>.in.json`. סוכן-מחלץ מקבל shard שלם ומעבד batch אחר batch (לא קריאה לכל batch). 3–4 סוכנים במקביל על shards.
- פלט לכל batch: `entities[]{kind, name, description, quote, chunk_id}`, `relations[]{type, source, target, evidence_chunk_id, note}`. kinds ו-types סגורים (2.4). ולידציה: pydantic, chunk_id קיים, type מותר, שם לא ריק; batch שנכשל → `retry/`.
- merge: `Entity` על `(kind, norm_name)`; `MENTIONS{quote}`; קשתות עם `evidence_chunk_ids[]`, `batch_id`, `extracted_at`, `model` — provenance מלא. סוכן לא נוגע ב-DB.

### 3.6 `brain resolve` — איחוד ישויות (3 שכבות)

1. **דטרמיניסטי:** Person — email זהה, `jira_username == git_name` מנורמל, טבלת מיפוי ADO. Entity — `norm_name` (lowercase, ללא פיסוק, singular) בתוך אותו kind + טבלת aliases (`KIP-848` ↔ שם הפיצ'ר).
2. **embedding:** bge-m3 על `name + description`; cosine > 0.92 באותו kind → merge אוטומטי; 0.80–0.92 → תור לסוכן.
3. **סוכן-שופט:** batches של זוגות עם quotes → `{same|different|unsure, reason}`. `unsure` נשאר נפרד.
- ביצוע: `SAME_AS` → `apoc.refactor.mergeNodes` עם `aliases[]`, `merged_from[]`. **מדידה:** שיעור כפילויות לפני/אחרי; סט זהב של 100 זוגות (סוכן מסמן, המשתמש דוגם) → precision/recall. הרעש הסינתטי הידוע נותן ground truth אמיתי לאנשים.

### 3.7 `brain communities` — קהילות וסיכומים

- GDS projection: `Entity, WorkItem, Document, Component` עם `MENTIONS, REFERENCES, LINKS_TO, IMPLEMENTS, DEPENDS_ON, IN_COMPONENT`. Leiden היררכי, 2 רמות (fine/coarse), יעד 100–200 קהילות סה"כ.
- לכל קהילה: top-N חברים לפי degree + chunks ראייתיים → batch → סוכן-מסכם כותב `{title, summary, findings[], rank}` (מבנה community report של MS GraphRAG). סיכום מוטמע → `Community.embedding`.
- עדכון: hash של קבוצת החברים; מסכמים מחדש רק קהילות שהשתנו.

### 3.8 `brain index` — אינדקסים סופיים

vector על `Entity.embedding`, `Community.embedding`; fulltext על `WorkItem(title,description)`, `Document(title,body)`, `Entity(name,description)`. דוח: ספירת צמתים/קשתות לפי label, אחוז ישויות עם provenance, ישויות יתומות.

### 3.9 עדכון אינקרמנטלי (בונוס הקורס, חובה בארגון אמיתי)

harvest לפי `updated >= checkpoint`; canon/load idempotent; chunk לפי hash; extract רק chunks חדשים; resolve רק ישויות חדשות מול קיימות; communities מחדש (זול) + סיכום רק לשינויים. נבדק בסעיף 5 ("הוסיפו 10 מסמכים ומדדו").


## סעיף 4 — אחזור, router, MCP ✅ מאושר

עיקרון: ספריית אחזור אחת (`brain/retrieve/`) עם 6 אסטרטגיות, נגישה גם כ-Python (להערכה head-to-head) וגם ככלי MCP (לסוכן השואל). כל קריאה נרשמת ל-JSONL: `question, strategy, cypher, latency_ms, hit_ids, tokens_out`.

### 4.1 אסטרטגיות ↔ סוגי שאלות

| # | אסטרטגיה | מימוש | עונה בעיקר על |
|---|---|---|---|
| S1 | Hybrid chunks (baseline) | `HybridRetriever` של neo4j-graphrag עם `Embedder` מותאם שעוטף את Ollama: vector + fulltext, RRF. בלי גרף | baseline לכל דבר; שאלות "מה כתוב על X" |
| S2 | Graph-enhanced vector | `VectorCypherRetriever`: chunk → parent → הרחבה 1–2 hops (issues מקושרים, tests, commits, KIP) → הקשר מובנה | עקיבות כשהעוגן מעורפל |
| S3 | Entity-anchored local | קישור ישויות: regex keys → lookup ישיר; אחרת embedding שאלה → top-k `Entity` → הרחבת שכונה עומק 2 (`MENTIONS/IMPLEMENTS/DEPENDS_ON/TESTS/…`), דירוג לפי degree + משקל קשת, chunks ראייתיים עם quotes | רציונל החלטות, השפעה |
| S4 | Text2Cypher מוגן | הסוכן מקבל סכמה (`apoc.meta.schema`) + few-shot לכל סוג שאלה → Cypher. שומר: ריצה במצב `READ` של הדרייבר (`RoutingControl.READ` — השרת דוחה כתיבה; Community Edition ללא RBAC), deny-list (`CREATE/MERGE/DELETE/SET/REMOVE/CALL` פרט ל-allowlist), `EXPLAIN` לפני ריצה, timeout 10s, הזרקת `LIMIT` | אגרגציות ("מי הכי הרבה", "אילו רכיבים… עם…"), נקודת-זמן |
| S5 | Global (community reports) | embedding שאלה → top-k `Community` (vector) → מחזיר reports; ה-reduce נעשה ע"י הסוכן השואל (map-reduce של MS GraphRAG, בגרסה אגנטית) | תמטי/גלובלי |
| S6 | Temporal tools | דטרמיניסטי: `status_at(key, date)` מ-`StatusChange`; `timeline(key)`; `changes_between(component, v1, v2)` מ-`FIX_VERSION + RESOLVES + StatusChange`; `assignees_over_time(key)` מ-`valid_from/to` | טמפורלי |

**Reranking:** cross-encoder מקומי `BAAI/bge-reranker-v2-m3` (רב-לשוני) על chunks לפני החזרה, דגל `rerank=true`. נמדד בהערכה עם/בלי.

### 4.2 Router — שתי שכבות

1. **Pre-router דטרמיניסטי** (`route(question)`): regex keys → S3/lookup; תבניות תאריך/גרסה → S6; מילות אגרגציה ("most", "how many", "count", "כמה", "הכי") → S4; מילות תמה ("themes", "overview", "נושאים", "מגמות") → S5; ברירת מחדל S2. מחזיר `{strategy, reason, confidence}` — לוג בלבד, לא כופה.
2. **Router אגנטי:** הסוכן השואל (Claude) בוחר כלים לפי תיאורי ה-MCP, בהנחיה מ-`route()`. זה Agentic GraphRAG (מודול 8 בקורס). בהערכה מריצים גם מצב "אסטרטגיה קבועה" — כל אסטרטגיה על כל שאלה — כדי ללמוד "מתי מה" במספרים, לא לפי תחושה.

### 4.3 כלי MCP (FastMCP, `brain/mcp/server.py`)

| כלי | אסטרטגיה | קלט → פלט |
|---|---|---|
| `search_chunks` | S1 | `query, k, mode=hybrid|vector|fulltext, rerank` → chunks |
| `search_with_context` | S2 | `query, k, hops` → chunks + שכנים מובנים |
| `lookup` | — | `key` (KAFKA-/KIP-/sha/person) → צומת + שכונה |
| `local_search` | S3 | `query, kinds[], depth, k` → ישויות + קשתות + quotes |
| `get_schema`, `cypher_examples`, `run_cypher` | S4 | סכמה / few-shot לפי סוג / `cypher` → rows (מוגן). במצב B הסוכן השואל כותב את ה-Cypher בעצמו; במצב A (הערכה) `cypher-author` כותב אותו |
| `global_search` | S5 | `query, level, k` → community reports |
| `status_at`, `timeline`, `changes_between`, `assignees_over_time` | S6 | דטרמיניסטי |
| `impact` | S3+S4 | `key_or_name, depth` → issues פתוחים, tests, docs, commits על אותם קבצים |
| `explain_edge` | — | `src, dst` → provenance: quotes, chunk ids, batch, model — "למה המוח מאמין בזה" |
| `route` | 4.2 | `question` → אסטרטגיה מוצעת + סיבה |

Resources: `brain://schema`, `brain://stats`. Prompt: `answer_with_citations` (חובה לצטט `key`/`chunk_id` בכל טענה).
Transport: stdio ל-Claude Code (`.mcp.json` בפרויקט) + streamable HTTP ב-compose (port 8765).

### 4.4 אריזת הקשר (context packaging)

כל כלי מחזיר JSON אחיד: `items[]{kind, key, title, snippet, score, provenance[]}`, `cypher_used`, `latency_ms`, `truncated`. תקרה ~4k tokens לתשובה; מדיניות קיטום: לפי score, שומרים לפחות פריט אחד מכל kind. זה מה שמאפשר להשוות אסטרטגיות באותם תנאים.

### 4.5 אבטחה (מודול 10)

`run_cypher` רק במצב `READ` של הדרייבר (אכיפה בצד השרת; ב-Community אין משתמש read-only); deny-list + `EXPLAIN` + timeout + `LIMIT`; לוג של כל Cypher שנוצר. הרשאות ברמת צומת (`acl_groups[]` + פילטר בכל שאילתה) — מחוץ ל-scope, מתועד כ-hook עתידי ב-ADR.


## סעיף 5 — הערכה ✅ מאושר

עיקרון (מודול 9): מודדים כל שכבה בנפרד, baseline וקטורי חזק באותם תנאים, מספרים לפי סוג שאלה. ללא RAGAS כספרייה (דורשת LLM client) — מממשים את אותם מדדים עם סוכן-שופט, ומתעדים את המיפוי.

### 5.1 סט הערכה (`data/eval/questions.jsonl`, 32+ שאלות)

- 8 לכל סוג שאלה (4 סוגים). ~1/3 בעברית (cross-lingual). שדות: `id, type, lang, question, gold_answer, gold_evidence[] (keys/chunk_ids), difficulty, expected_strategy`.
- בנייה: 15 שאלות הכשירות + סוכן-מייצר שדוגם מסלולים אמיתיים מהגרף (למשל `Test-TESTS->Story<-RESOLVES-Commit`) וממלא תבניות → gold נגזר מהגרף (אמת דטרמיניסטית) או מ-`synthetic_spec` (אמת ידועה). המשתמש דוגם 10 לאישור.
- **הגנה מדליפה:** שאלות שנגזרו מהגרף נבדקות גם מול baseline S1 שלא רואה את הגרף — ההשוואה הוגנת כי ה-gold הוא עובדות מהמקורות, לא מהחילוץ.

### 5.2 מדידה בשכבות

| שכבה | מדדים | איך |
|---|---|---|
| 0. harvest/canon | ספירות, שדות לא ממופים, צפיפות קישורים, % refs מטקסט לעומת link | דוחות השלבים |
| 1. איכות גרף | resolution P/R על 100 זוגות זהב; כפילויות לפני/אחרי; % יתומים; % קשתות עם provenance; דיוק חילוץ על 100 `MENTIONS` אקראיים (סוכן-שופט: "ה-quote תומך?") | `brain eval graph` |
| 2. אחזור | לכל אסטרטגיה × שאלה: hit@k / recall של `gold_evidence`, context precision, latency p50/p95, context tokens, מספר Cypher | דטרמיניסטי מ-JSONL |
| 3. תשובה | faithfulness (טענות נתמכות בהקשר שהוחזר), correctness מול gold, citation validity (המפתחות המצוטטים קיימים ובהקשר), answer relevancy | סוכן-שופט, rubric 0–2, JSON |
| עלות | טבלה לכל אסטרטגיה: latency, tokens, tool calls, זמן-סוכן | JSONL |

### 5.3 מצבי ריצה

- **A. אסטרטגיה קבועה:** כל S1–S6 על כל שאלה (כשרלוונטי) → מטריצה אסטרטגיה × סוג שאלה. זה התוצר הלימודי המרכזי: "מתי מה" במספרים.
- **B. אגנטי:** סוכן-שואל עם כל כלי ה-MCP + `route` → תשובה מקצה לקצה עם ציטוטים.
- **Baseline:** S1 hybrid + rerank, אותו context budget, אותו שופט.

### 5.4 פרוטוקול סוכן-שופט

עיוור לשם האסטרטגיה; מקבל `question, gold, context, answer`; rubric קבוע לכל מדד עם דרישה לצטט ראיה; פלט JSON מאומת. השוואה pairwise (baseline מול גרף) בסדר אקראי להפחתת הטיה. המשתמש דוגם 10% מהשיפוטים.

### 5.5 בדיקת עדכון אינקרמנטלי

מוסיפים 10 פריטים מחוץ לפרוסה (תאריכים מאוחרים) → `brain harvest --since` → כל הפייפליין. מודדים: זמן, ישויות/קשתות שנוספו, קהילות שהשתנו, ו-5 שאלות על הפריטים החדשים.

### 5.6 דוח מסכם (`docs/report/eval-report.md`)

נוצר אוטומטית מה-JSONL: מטריצה, טבלת עלות, שכבה 1, אינקרמנטלי. בסופו סעיף חובה (מהקורס): **"מתי לא הייתי משתמש בגרף כאן"** + "מה הייתי משנה" — נכתב ע"י המתכנן אחרי קריאת המספרים.


## סעיף 6 — סוכנים ותהליך ביצוע ✅ מאושר

### 6.1 חלוקת תפקידים

- **Fable (סשן זה) = מתכנן.** כותב spec, תכנית, brief לכל שלב, מפרש דוחות, מחליט accept/fix, וכותב את שכבת ההסבר למשתמש. לא כותב קוד.
- **Opus 5 = מבצע**, דרך subagents ב-`.claude/agents/<name>.md` (`model: opus`). שלוש משפחות:

**א. סוכני הנדסה** (כותבים קוד, בדיקות, תשתית; כלים מלאים):

| סוכן | אחריות |
|---|---|
| `brain-infra` | docker-compose, Neo4j+plugins, Ollama+bge-m3, pyproject/uv, שלד CLI, `brain doctor`, git init |
| `brain-ingest-engineer` | harvest connectors, canon mappers, regex mentions, load, chunk+embed |
| `brain-graph-engineer` | validate+merge של פלטי חילוץ, resolve (3 שכבות), GDS communities, index |
| `brain-retrieval-engineer` | 6 אסטרטגיות, router, cypher guard, MCP server |
| `brain-eval-engineer` | eval harness, מדדים, שופט-batches, מחולל דוח |

**ב. סוכני-LLM** (ה"מודל" של הפייפליין; פלט JSON בלבד; כלים: Read/Write/Glob בלבד — בלי Bash, כדי שלא יוכלו לגעת ב-DB):

| סוכן | קלט → פלט |
|---|---|
| `synthetic-org-generator` | stories/KIPs אמיתיים + `synthetic_spec.md` → Xray/ADO קנוני עם רעש ידוע |
| `kg-extractor` | batch chunks + סכמה + דוגמאות → `entities[], relations[]` עם quotes |
| `entity-adjudicator` | זוגות מועמדים + quotes → `same/different/unsure + reason` |
| `community-summarizer` | חברי קהילה + chunks → community report |
| `cypher-author` | סכמה + שאלה/סוג → Cypher (ל-`cypher_examples` ובזמן שאילתה) |
| `question-forger` | מסלולים מהגרף + תבניות → שאלות הערכה עם gold |
| `eval-judge` | `question, gold, context, answer` (עיוור) → ציוני rubric JSON |
| `brain-analyst` | הסוכן השואל (מצב B): `Read` + כלי MCP בלבד (מחוברים ב-Plan 2) → תשובה עם ציטוטים |

**ג. סוכני איכות:**

| סוכן | אחריות |
|---|---|
| `brain-reviewer` | סוקר diff + דוח מול קריטריוני הקבלה ועקרונות הקורס (סכמה-קודם, provenance, סוכן-לא-נוגע-ב-DB, read-only Cypher). פלט: שורה לממצא |
| `lesson-writer` | מנסח טיוטת `docs/lessons/NN-<step>.md` בעברית מתוך הדוחות וה-diff (עובדות ומספרים); כלים Read/Grep/Glob/Write. המתכנן מוסיף את שכבת "מה זה מלמד" |

מוסכמות משותפות ב-`docs/agents/conventions.md`: חוזי קלט/פלט, פריסת batches, פורמט דוח יציאה (`done / numbers / deviations / open`), שפה (פרומפטים וקוד באנגלית; שיעורים ומסמכי משתמש בעברית).

### 6.2 פרוטוקול batches לסוכני-LLM

`data/batches/<task>/<shard>/<n>.in.json` → `<n>.out.json` לצידו; `status.json` לכל shard (done/failed). הסוכן: קורא `brain/<task>/schema.json`, מעבד batch אחר batch, מוודא בעצמו מול הסכמה לפני כתיבה, לא משנה קלט, לא מדלג בשקט (batch כושל נרשם). מקביליות = shards (3–4 סוכנים). קוד דטרמיניסטי מוודא שוב ומבצע merge.

### 6.3 לולאת ביצוע לכל שלב

1. **Brief** — המתכנן כותב `docs/planning/steps/NN-<step>.md`: מטרה, קלטים, פלטים, קריטריוני קבלה, מודול בקורס, "מה תלמד בשלב הזה".
2. **Dispatch** — Agent tool עם `model: opus`, במקביל כשאין תלות.
3. **Execute** — הסוכן כותב קוד+בדיקות, מריץ `brain <step>`, כותב `data/reports/<step>.json` + דוח יציאה.
4. **Review** — `brain-reviewer` על ה-diff והדוח.
5. **Decide** — המתכנן: accept / fix-loop / שינוי תכנית. מעדכן `docs/planning/progress.md` (מקור אמת יחיד ל"איפה אנחנו" — מאפשר המשך מכל סשן).
6. **Teach** — `lesson-writer` מנסח; המתכנן מסיים ומציג למשתמש: מה קרה, למה, מה נלמד, מספרים, נקודות הכרעה.
7. **Commit** — commit לכל שלב (הפרויקט מאותחל כ-git ב-`brain-infra`).

### 6.4 ניהול הקשר

כל שלב = subagent חדש עם brief מלא (לא נסמך על זיכרון סשן). שלבים ארוכים (extract) מפוצלים ל-shards. `progress.md` + `.claude/agents/` + ה-spec מספיקים להמשך גם מסשן Opus נפרד של המשתמש.


## סעיף 7 — בדיקות וטיפול בשגיאות ✅ מאושר

### 7.1 בדיקות (pytest, `tests/`)

| רמה | מה | איך |
|---|---|---|
| יחידה | mappers (raw→canonical), regex mentions, chunker, ולידטורים של פלטי סוכנים, cypher guard (deny-list, EXPLAIN, LIMIT), חוקי router, פונקציות טמפורליות | golden files ב-`tests/fixtures/`; ללא DB |
| אינטגרציה | פייפליין מלא על **קורפוס-מיני** (`data/fixtures/mini/`: ~20 issues, 3 KIPs, 50 commits, שכבה סינתטית קטנה) מול Neo4j מה-compose; חוזי כלי MCP (סכמת פלט אחידה) | `make smoke` < 5 דק'; רץ לפני כל commit של שלב |
| פלטי סוכנים | ולידציית סכמה = הבדיקה; + ספי שפיות (KIP עם 0 ישויות → דגל; batch עם >50% `unsure` → דגל) | בקוד ה-merge |
| הערכה | `brain eval` על קורפוס-מיני מייצר מטריצה תקינה (מבנה, לא איכות) | בדיקת עשן |

### 7.2 טיפול בשגיאות

- **קונקטורים:** retry+backoff, checkpoint לכל דף; תוצאה חלקית מותרת — נרשמת בדוח, השלב לא "מצליח בשקט".
- **load/merge:** טרנזקציה לכל batch; כשל → rollback של ה-batch + דוח; אף פעם לא חצי-מצב.
- **batches של סוכנים:** כשל ולידציה → `retry/` (עד 2), אחר כך `quarantine/` + דוח. הפייפליין ממשיך עם מה שתקין ומסמן חוסר.
- **embedding:** שם המודל והמימד נשמרים ב-metadata של האינדקס; אי-התאמה בזמן שאילתה = שגיאה קשה (לא fallback שקט). Ollama לא זמין → fallback ל-sentence-transformers **רק עם אותו מודל**, עם אזהרה בדוח.
- **cypher guard:** דחייה עם סיבה מובנית; לוג של כל Cypher שנדחה (חומר ל-few-shot).
- **MCP:** כלי מחזיר `{error, hint}` מובנה, לא exception; timeout לכל כלי.
- **דטרמיניזם:** seed קבוע לייצור סינתטי; גרסאות מוצמדות; `brain doctor` בודק Neo4j, plugins (APOC/GDS), Ollama, מודל ומימד, ושהשרת אכן דוחה כתיבה במצב READ.

