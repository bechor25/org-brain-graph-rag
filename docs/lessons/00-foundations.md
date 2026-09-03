# שיעור 00 — תשתית וסוכנים

**תאריך:** 2026-09-03 · **תכנית:** Plan 0 · **מודול בקורס:** 1 (הכנת סביבת העבודה), 2 (גרפי ידע ו-Cypher — הקמת Neo4j, אינדקסים וקטוריים), 8 (Agentic GraphRAG ו-MCP), 10 (פרודקשן — אבטחה: Text2Cypher כמשטח תקיפה)

## מה עשינו

בנינו את כל התשתית של ה-POC לפני שנגענו בשורת דאטה אחת: חבילת `brain` עם CLI (`brain/cli.py`) שבו כל שלב בפייפליין הוא תת-פקודה אידמפוטנטית — 10 שלבים (`harvest` … `eval`) הם עדיין stubs שיוצאים בקוד 2 ומצהירים באיזו תכנית הם מגיעים, ורק `doctor` ו-`version` ממומשים.
הועלה Neo4j `2026.06.0` דרך `docker-compose.yml` עם APOC ו-GDS ופורטים כפויים ל-`127.0.0.1`, ומעליו נכתב `GraphClient` (`brain/graph/client.py`) שקורא ב-`RoutingControl.READ`, כותב ב-`RoutingControl.WRITE`, ומבצע כתיבות המוניות ב-`write_batched()` (1000 שורות לטרנזקציה, `UNWIND $rows`).
לצדו `OllamaEmbedder` (`brain/embed/client.py`) מדבר עם `bge-m3` מקומי דרך `/api/embed` ואוכף גם את מספר הווקטורים החוזר (`EmbedCountMismatch`) וגם את מימדם (`EmbedDimMismatch`).
`brain doctor` (`brain/doctor.py`) מריץ 7 בדיקות שכולן בודקות **התנהגות** ולא קונפיג: חיבור ל-Neo4j, `apoc.version()`, `gds.version()`, ניסיון כתיבה דרך `read()` שאמור להידחות, זמינות Ollama, נוכחות המודל, והטמעה אמיתית של מחרוזת שמימדה נבדק.
הוגדר המודל הקנוני — חמישה טיפוסים ב-`brain/canon/models.py` (`WorkItem`, `Document`, `Person`, `Change`, `Container`) עם חילוץ הפניות דטרמיניסטי ב-`brain/canon/mentions.py`, ונכתב קורפוס fixtures מיני ב-`data/fixtures/mini/` שמאמת אותם offline.
לבסוף הוגדרו 15 סוכני Opus ב-`.claude/agents/` עם הענקות כלים לפי תפקיד, ומסמך מוסכמות משותף ב-`docs/agents/conventions.md`.

## למה ככה (הקישור לקורס)

**מודול 1 — "הכנת סביבת העבודה".** הקורס פותח באזהרה מפורשת: "האקוסיסטם של Graph RAG זז מהר… הצמידו גרסאות (pin) בכל פרויקט, וכשמשהו נשבר — בדקו קודם את ה-changelog של החבילה, לא את הקוד שלכם". אצלנו זה `pyproject.toml` + `uv.lock` (`neo4j 6.3`, `typer 0.27`, `pydantic 2.13`), וכלל ברזל 5 ב-`docs/agents/conventions.md` שאוסר על סוכן לשדרג תלות בלי brief שמבקש זאת. בנקודה אחת סטינו מהקורס במכוון: הוא מייצא `OPENAI_API_KEY`, וה-POC הזה הוא אפס API חיצוני — ה-LLM הוא סוכן Opus (`docs/decisions/0002-agents-as-llm.md`) וה-embeddings מקומיים (`docs/decisions/0003-local-bilingual-embeddings.md`).

**מודול 2 — "מקימים Neo4j — שתי דרכים".** הקורס מציע `docker run` על `neo4j:2026.06.0` עם `NEO4J_PLUGINS='["apoc"]'`, או AuraDB Free בענן. בחרנו בדרך הראשונה ב-`docker-compose.yml`, בשתי הרחבות: `'["apoc","graph-data-science"]'` — כי Leiden דרך GDS מגיע ב-Plan 1 ואנחנו לא רוצים לגלות אז שה-image לא תומך — והפורטים חשופים כ-`127.0.0.1:7474` ו-`127.0.0.1:7687` בלבד. ההסבר של הקורס לשני הפורטים (7474 = דפדפן, 7687 = Bolt) הוא בדיוק החלוקה ב-`Makefile`: `make up` מדפיס את כתובת הדפדפן, `Settings.neo4j_uri` מצביע על Bolt.

**מודול 2 — "אינדקסים וקטוריים ו-full-text — הגשר אל RAG".** ההערה החשובה שם היא בהערת קוד: "המימדים חייבים להתאים למודל: text-embedding-3-small=1536, -large=3072". `CREATE VECTOR INDEX` עצמו יגיע ב-Plan 1 (`brain chunk`), אבל את החוזה אכפנו כבר עכשיו: `embed_model` ו-`embed_dim` יושבים זה לצד זה ב-`brain/config.py` (`bge-m3` / 1024), `OllamaEmbedder` זורק `EmbedDimMismatch` על כל ווקטור ברוחב אחר, ו-`brain doctor` מטמיע מחרוזת אמיתית ומשווה. אי-התאמה היא שגיאה קשה, לא fallback שקט.

**מודול 2 — "Cypher מזורז" ו-"חיבור מ-Python".** מלכודת ה-MERGE של הקורס ("תמיד `MERGE` כל צומת בנפרד למשתנה, ורק אז `MERGE` את הקשר") היא הסיבה ש-`write_batched()` מקבל טקסט Cypher מבחוץ ומסתפק באכיפה שהוא משתמש ב-`UNWIND $rows AS row` — הצורה הנכונה של ה-MERGE נשארת באחריות שלב הטעינה, וכלל האידמפוטנטיות ב-`docs/agents/conventions.md` הופך אותה לדרישת קבלה ולא להמלצה.

**מודול 10 — "אבטחה: Text2Cypher הוא משטח תקיפה".** הקורס מדרג את ההגנות מהחזקה לחלשה: (1) הרשאות DB — "ה-LLM לא יכול להשחית מה שהחיבור לא מורשה לכתוב", (2) ולידציה דטרמיניסטית לפני הרצה (denylist / `CyVer` / `CypherQueryCorrector`), (3) מגבלות שרת, (4) ארכיטקטורה של שאילתות פרמטריות. הוא גם מציין ש-RBAC עדין הוא פיצ'ר Enterprise. ב-Community Edition, לכן, ההגנה החזקה ביותר שזמינה לנו היא `RoutingControl.READ` ב-`GraphClient.read()` — והיא נאכפת **בשרת**, לא ב-regex בצד הלקוח שאפשר לעקוף דרך `CALL` או תת-שאילתה. שכבה 2 של הקורס מגיעה ב-Plan 2, ולכן `read_mode_guard` ב-`brain/doctor.py` מסומן `required=False` בכוונה: הוא ראיה, לא ההגנה כולה. גם הווקטור השני שהקורס מזכיר — "הרעלה בזמן האינדוקס", שבו מסמך זדוני מזריק ישויות כוזבות שמזהמות סיכומי קהילות — קיבל מענה מבני מראש: כלל ברזל 3 ב-`docs/agents/conventions.md` דורש `evidence_chunk_ids`, `batch_id`, `model` ו-`extracted_at` על כל דבר שיצא מ-LLM.

**מודול 8 — "Agentic GraphRAG" ו-MCP.** הקורס מתאר את `mcp-neo4j-cypher` שחושף לסוכן בדיוק שלושה כלים — `get_neo4j_schema`, `read_neo4j_cypher`, `write_neo4j_cypher` — ואומר על השלישי: "שאפשר פשוט לא להעניק לסוכן, וכך פותרים חצי מבעיית האבטחה". זה בדיוק עקרון החלוקה ב-`.claude/agents/`: חמשת סוכני ההנדסה מקבלים את כל הכלים; שבעת סוכני-ה-LLM (`kg-extractor`, `entity-adjudicator`, `community-summarizer`, `cypher-author`, `question-forger`, `eval-judge`, `synthetic-org-generator`) מקבלים `Read, Write, Glob` בלבד — אין להם Bash, ולכן אין להם גישה ל-DB בכלל, לא כהנחיה בפרומפט אלא כעובדה; `brain-analyst` מקבל `Read` בלבד עד ש-Plan 2 יחבר לו את שרת ה-MCP; ושני סוכני האיכות מקבלים כלים קריאה בלבד (`brain-reviewer`: `Read, Grep, Glob, Bash`; `lesson-writer`: `Read, Grep, Glob, Write`). גם ההערה של הקורס על "המחיר של סוכנות" תורגמה לארכיטקטורה: הסוכנים רצים כ-batch על קבצי JSON (`data/batches/<task>/<shard>/NNN.in.json`), לא כלולאת ReAct חיה.

## מספרים

| מדד | ערך |
|---|---|
| commits בענף `plan0-foundations` | 14 |
| קבצים בדיף מול `main` | 55 (+2818 שורות) |
| בדיקות יחידה (offline, `make check`) | 22 |
| בדיקות live (`make smoke`) | 5 |
| בדיקות `brain doctor` | 7/7 OK |
| שלבי pipeline ב-CLI | 10 stubs (exit 2) + `doctor` + `version` |
| סוכנים ב-`.claude/agents/` | 15 — 5 הנדסה / 7 סוכני-LLM / 1 analyst / 2 איכות |
| Neo4j: `make up` עד `healthy` | 11 שניות |
| גודל `bge-m3` | 1.08 GiB |
| `ollama pull bge-m3` | 16 שניות |
| הטמעת 64 משפטים קצרים | 0.94 שניות (~68 לשנייה) |
| מימד embedding נאכף (`EMBED_DIM`) | 1024 |
| batch כתיבה ל-Neo4j | 1000 שורות לטרנזקציה |
| קורפוס fixtures מיני | 6 issues · 1 KIP · 3 persons · 4 changes · 4 containers |
| רשומות עם `refs` שה-regex שחזר מהטקסט | 11/11 (6 workitems + 1 document + 4 changes; 15 הפניות) |

## מה הפתיע

- **Ollama כבר היה מותקן נייטיב במכונה (גרסה 0.32) — לא דרך brew.** ה-README התכונן ל-`brew install ollama`, ובפועל הבינארי היה שם והחסר היחיד היה שהתהליך לא רץ. מכאן גם ההחלטה הפתוחה: `ollama serve` הוא תהליך ידני שצריך להעלות אחרי כל reboot, ו-`brain doctor` מדווח `[FAIL] ollama` כשהוא לא רץ.
- **APOC ו-GDS מגיעים בתוך ה-image של `neo4j:2026.06.0`.** `NEO4J_PLUGINS` רק מעתיק את ה-jars מ-`/var/lib/neo4j/labs` ו-`/products` בזמן עלייה — אין הורדה מהרשת ואין מטריצת תאימות גרסאות לנהל. התוצאה: `healthy` תוך 11 שניות.
- **ruff 0.16 מפרמט code fences של Python בתוך Markdown.** התיעוד שלנו התחיל להשתנות תחת `ruff format`, ולכן `extend-exclude = ["docs"]` ב-`pyproject.toml` — הפורמטר בלבד; ה-lint עדיין רץ על כל הקוד.
- **הסוכן המבקר תפס בדיקה חסרה שאף אחד לא חיפש:** `OllamaEmbedder.embed()` בדק את *רוחב* הווקטורים אבל לא את *מספרם*. אותו רוחב בכמות שגויה מזיז את ההתאמה chunk↔vector בשקט מוחלט — באג יקר בדיוק כמו מימד שגוי, אבל בלי הודעת שגיאה. מכאן `EmbedCountMismatch` (commit `f53793a`).
- **ה-regex ל-issue keys תופס `UTF-8` ו-`SHA-256`.** התבנית `[A-Z][A-Z0-9]{1,9}-\d+` היא בדיוק הצורה של מפתח Jira, וגם בדיוק הצורה של חצי מהתקנים שמוזכרים בכל דיון הנדסי. בצד השני של המטבע, הפתעה טובה: `\w` ב-`re` של Python הוא Unicode כברירת מחדל, ולכן `\b` עובד גם בטקסט עברי — `ראו KAFKA-100 ו-KIP-5 לפרטים` מחזיר את שתי ההפניות.
- **READ mode באמת נאכף בשרת.** הציפייה הייתה ש-Community Edition לא יאכוף כלום ונצטרך regex; בפועל `client.read("CREATE (:_DoctorTmp)")` מחזיר `Neo.ClientError.Statement.AccessMode` (driver 6.3, GQL status 08N03) — כלומר יש בסיס אמיתי ל-`run_cypher` מוגן, בלי RBAC של Enterprise.
- **`record.data()` מוחק labels ו-element ids.** צומת שחוזר מ-`read()` הופך למילון של properties בלבד. פירושו: כל שאילתה חייבת לעשות projection מפורש (`RETURN labels(n) AS labels, n.key AS key`) — זה תועד ב-docstring של `brain/graph/client.py` כדי שלא ניפול בזה ב-Plan 1.

## מה היינו משנים

- **`[dependency-groups] dev` במקום `[project.optional-dependencies] dev`.** היום `make check` דורש `uv sync --extra dev`, כלומר כלי הפיתוח הם extra של החבילה עצמה — סמנטית לא נכון, ומקשה על מי שמתקין את `brain` כתלות.
- **`write_jsonl` דורס את קובץ היעד.** הוא פותח `path.open("w")` ומתחיל לכתוב; ריצה שנופלת באמצע משאירה קובץ canonical חתוך. ברגע ש-`data/canonical/*.jsonl` הופך ל-artifact שמישהו נשען עליו — כתיבה לקובץ זמני ואז `rename` אטומי.
- **`Ref(kind="pr", key="14001")` בלי repo.** המפתח הוא מספר ה-PR בלבד. זה עובד כל עוד יש ריפו אחד; מהרגע שיש שניים, `#14001` משני מקורות יתמזג לצומת אחד. ההחלטה (`owner/repo#N`) צריכה ליפול לפני ingest ראשון של יותר מריפו אחד.
- **`--wait` ב-`docker compose up`** — `make up` חזר לפני ש-Neo4j היה `healthy`, כך ש-`brain doctor` מיד אחריו נכשל לסירוגין. תוקן בסקירה הסופית (commit `ce9cf4c`) ל-`docker compose up -d --wait`.

## מה זה מלמד (המתכנן)

**1. חוזה פלטפורמה לפני דאטה — וזה חוזה שרץ, לא README.** `brain doctor` הוא ההבדל בין "התקנתי הכל" ל"המכונה הוכיחה שיש לה APOC, GDS, מצב READ שנאכף, ומודל embedding במימד הנכון". ב-Plan 1, כשמשהו ייכשל, השאלה הראשונה תהיה "doctor ירוק?" — ואם כן, הבעיה בקוד, לא בסביבה. זה חוסך את סוג הדיבוג הכי יקר: זה שמחפש באג בקוד כשהבעיה היא קונטיינר שלא עלה.

**2. שלוש ערבויות שקיימות עוד לפני שורת דאטה אחת — וכולן מבניות, לא הבטחות.**
- Read-only לסוכן שמייצר Cypher: **השרת** דוחה (`AccessMode`), לא regex שלנו. regex אפשר לעקוף עם `CALL`; שרת לא.
- מימד + מספר וקטורים: **חריגה**, לא אזהרה. שיבוש embedding הוא הבאג הכי שקט ב-RAG — האינדקס "עובד", התשובות פשוט מתדרדרות.
- סוכן-LLM לא נוגע ב-DB: **אין לו Bash**. לא "אנא אל תכתוב ל-Neo4j" — פיזית אין דרך.
העיקרון הכללי, והוא רלוונטי ישירות ל-NessBot: כל כלל בטיחות לסוכן שמסתמך על פרומפט יישבר בריצה ארוכה. כלל ששורד הוא כזה שנאכף ע"י המבנה (הענקת כלים, הרשאות, סכמה).

**3. התשובה לשאלה "איך מאנדקסים ADO/Jira/Xray/Confluence" מתחילה כאן, לא ב-LLM.** חמישה טיפוסים קנוניים (`WorkItem`, `Document`, `Person`, `Change`, `Container`) — כל מערכת חדשה = mapper חדש, לא סכמת גרף חדשה. ו-regex על `KAFKA-123`/`KIP-848`/`#PR`/URL לפני כל LLM מביא את רוב העקיבות החוצה-מערכות בחינם ובאופן ניתן לביקורת. המחיר שראינו כבר עכשיו: דיוק (`UTF-8` נתפס כ-issue). הפתרון הוא allowlist של project keys מה-harvest — לא מודל. זו תבנית שתחזור: **דטרמיניסטי קודם, LLM רק למה שדטרמיניסטי לא יכול.**

**4. "סוכן כ-LLM" — מה זה עולה ומה זה קונה.** עולה: איטיות (batches, לא API), ואי-אפשר להריץ מסגרות שדורשות endpoint. קונה: אפס עלות, replay ו-diff על כל batch (`.in.json` ↔ `.out.json`), provenance מובנה (`batch_id`, `model`), ובעיקר — הפרדה חדה בין "מי חושב" (סוכן, JSON) ל"מי כותב" (קוד דטרמיניסטי, `MERGE`). בארגון אמיתי תחליף את הסוכן ב-API; הארכיטקטורה של batches+ולידציה+merge נשארת זהה.

**5. שיעור על תהליך: ממצא סקירה שורד רק אם הוא נכתב במקום שהסוכן הבא קורא.** הסקירות תפסו שני פגמים אמיתיים (בדיקת count, דיוק ה-regex). שלושה מחמשת הממצאים ל-Plan 1 קופלו ישירות לתוך פרומפטים של הסוכנים (`brain-ingest-engineer`, `brain-graph-engineer`) — שם הם יבוצעו. השניים שנשארו כפרוזה ב-`progress.md` (מפתח PR עם repo, `write_jsonl` אטומי) הם בדיוק אלה שהכי קל לשכוח. לכן ה-brief של Plan 1 יצטט אותם במפורש.

**6. מה לעקוב אחריו ב-Plan 1.** (א) throughput של embedding על chunks אמיתיים באורך 500–800 tokens — לא 68/שנייה של משפטים קצרים; המספר הזה קובע batch size ו-timeout. (ב) allowlist של project keys לפני שה-refs הופכים לקשתות. (ג) אידמפוטנטיות של `load`: הרצה שנייה = אפס צמתים חדשים. אם אחד משלושת אלה לא נמדד — השלב לא הסתיים.
