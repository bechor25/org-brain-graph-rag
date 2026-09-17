# שיעור 13 — S5 ושרת MCP: חמישה-עשר כלים, מעטפת אחת, וה-reduce נשאר אצל השואל

**תאריך:** 2026-09-17 · **תכנית:** Plan 2 (Task 3) · **מודול בקורס:** 8 ("Agentic GraphRAG וגרפים טמפורליים") + 5 (Global Search על דוחות קהילה)

## מה עשינו

**S5 — חיפוש גלובלי.** `brain/retrieve/global_search.py` מטמיע את השאלה, שואל את `community_embedding` מי הדוחות הקרובים, ומחזיר לכל קהילה `title`, `summary`, `findings[]` (כל finding עם `evidence_chunk_ids`), `rank`, `level` ו-`size`. ה-**reduce** — הפיכת חמש תמות לתשובה אחת — נשאר אצל הסוכן השואל, בדיוק כפי שהבריף מגדיר ("ה-reduce אצל הסוכן השואל") וכפי שה-docstring מנמק: *"this does the map"*. הפעולה היחידה שהאסטרטגיה עושה מעבר לכך היא **איחוד לפי `member_hash`** — 22 סטים של חברים קיימים בשתי הרמות תחת שני מזהים, הרמה הדקה (`fine`) מנצחת, השורד מקבל את הציון הטוב מבין השניים, והמודחק נרשם ב-`props.duplicate_of` ולא נעלם בשקט.

**השרת.** `brain/mcp/server.py` הוא מעטפת FastMCP בלבד: **15 הכלים** של §4.3 בשמות המדויקים, אחד-לאחד מול פונקציות `brain/retrieve/`, כל אחד `async` ומעביר את העבודה החוסמת ל-worker thread, וכל תשובה היא `Result.model_dump()` שעבר `json.dumps(..., default=str)` פעם אחת לפני שהוא יוצא לחוט. כישלון לא נזרק החוצה כ-protocol fault אלא חוזר כאותה מעטפת עם `Row` שנושא `error` + `hint`. בנוסף: 2 resources (`brain://schema`, `brain://stats`), prompt אחד (`answer_with_citations`), ו-`/healthz` שעונה לפני שנוגעים ב-Neo4j.

**החיווט.** `.mcp.json` בשורש מריץ `uv run brain serve --stdio`; `.claude/agents/brain-analyst.md` מחזיק `tools:` עם 15 שמות `mcp__brain__*` + `Read` (16 פריטים); `tests/test_mcp_contract.py` קושר את שלושתם ל-`TOOL_NAMES` בקוד. השירות `brain-mcp` ב-`docker-compose.yml` מריץ את אותו שרת על streamable HTTP ב-`127.0.0.1:8765` תחת profile `mcp` — **לא הורם** בשלב הזה.

**המדידה.** `brain serve --check` (`brain/mcp/report.py`) מרים את השרת כתת-תהליך, מדבר איתו MCP בדיוק כמו `.mcp.json`, מודד **3 סבבים לכל כלי** וכותב את הסעיפים `global` ו-`mcp` לתוך `data/reports/retrieve.json` (merge, לא דריסה). הארגומנטים לא מוקלדים ביד — הם נשלפים מהגרף ומ-`data/eval/competency.jsonl`.

**איך מתחברים למצב B** (מ-`.mcp.json`, `docker-compose.yml` ו-README; `docs/planning/progress.md` מתעד רק את הדרישה "סשן חדש"):
1. `make up` — Neo4j עולה (`docker compose up -d --wait`).
2. `ollama serve` + `ollama pull bge-m3` — ההטמעות רצות על ה-host, לא ב-compose.
3. `uv run brain doctor` — חייב 7/7 OK.
4. לפתוח **סשן חדש** של Claude Code בשורש הריפו — `.mcp.json` נקרא רק בעליית סשן.
5. `/mcp` בסשן → השרת `brain` מחובר עם 15 כלים.
6. מי שרוצה HTTP במקום stdio: `docker compose --profile mcp up -d --wait`, ואז `curl http://127.0.0.1:8765/mcp` (בריאות: `/healthz`).

## למה ככה (הקישור לקורס)

**המודול פותח בהבחנה שהשלב הזה מממש.** *"retriever קלאסי עושה מהלך אחד: שאלה נכנסת → הקשר יוצא. סוכן גרף עובד אחרת: הוא מקבל **כלים**, מריץ שאילתה, קורא את התוצאה, ומחליט מה לשאול הלאה — לולאת ReAct."* עד Task 2 הייתה לנו ספרייה של שש אסטרטגיות שמישהו קורא לה מ-Python; Task 3 הפך אותה לכלים שסוכן בוחר ביניהם. ההכרעה שמאפשרת את שניהם היא הכרעה 1 בבריף: *"ספרייה אחת, שני משטחים… אין לוגיקת אחזור בשרת"* — ויש לה בדיקה, `test_the_adapter_contains_no_cypher`, שמפילה את הבילד אם `MATCH (`, `RETURN `, `MERGE (` או `db.index.vector` מופיעים בקובץ השרת. הסיבה מפורשת: התנהגות שקיימת רק כשהקורא הוא סוכן היא התנהגות שההערכה של Plan 3 לא יכולה למדוד.

**הקורס מציג ערכת כלים בסדר עדיפות יורד — וזה בדיוק המיפוי אצלנו:**

| שכבה בהנחיה הרשמית (מודול 8) | הכלים שלנו |
|---|---|
| "שאילתות Cypher פרמטריות כתובות-מראש — דטרמיניסטי, מהיר, בטוח" | `lookup`, `status_at`, `timeline`, `changes_between`, `assignees_over_time`, `impact`, `explain_edge`, `route` |
| "הצגת סכמה — שהסוכן ידע מה יש בגרף לפני שהוא מנחש" | `get_schema` + ה-resource `brain://schema` (לא מקוצץ) |
| "חיפוש וקטורי/היברידי — לכניסות סמנטיות" | `search_chunks` (S1), `search_with_context` (S2), `local_search` (S3), `global_search` (S5) |
| "Text2Cypher כ-fallback בלבד — לזנב הארוך" | `run_cypher` (Task 2) — מאחורי ה-guard, עם `cypher_examples` לפניו |

**15 כלים מול 3.** הקורס משבח את `mcp-neo4j-cypher` על כך שהוא *"חושף לסוכן בדיוק שלושה כלים — וזו דוגמה מצוינת לעיצוב מינימליסטי נכון"*. אנחנו חושפים 15, והמספר הזה הוא לא נפיחות אלא הזזה של המשקל מטה בטבלה: 8 מ-15 הם השכבה הראשונה (דטרמיניסטית ופרמטרית), ו-Text2Cypher הוא אחד. הקורס גם אומר על הכלי השלישי שם: *"write_neo4j_cypher (כתיבה — שאפשר פשוט **לא להעניק** לסוכן, וכך פותרים חצי מבעיית האבטחה)"* — אצלנו זה לא "לא להעניק" אלא "לא קיים": אין כלי כתיבה ברשימה, ו-`tools:` של `brain-analyst` נבדק בקוד מול `TOOL_NAMES` כך שגם לא תיווסף אחת בטעות.

**"המחיר של סוכנות" — נמדד, לא הוערך.** הקורס: *"הכלים הזולים (שאילתות מוכנות, קאש סכמה) עושים את רוב העבודה, וה-LLM מתערב רק איפה שחייבים."* ה-p50 שלנו הוא בדיוק הטבלה הזאת: `route` 2 ms, `cypher_examples` 2 ms, `get_schema` 3 ms (קאש 10 דקות), `lookup` 9 ms — מול `local_search` 260 ms ו-`search_with_context` 187 ms, שבהם יש קריאת הטמעה ל-Ollama. ולכן גם `latency_ms` במעטפת הוא wall clock **כולל** ה-Ollama: *"a latency number that excludes the embedding call would flatter every vector strategy against the deterministic ones."*

**S5 ומודול 5.** הקורס מתאר את Global Search כ-*"map: כל דוח קהילה … מייצר נקודות ביניים עם ציון חשיבות 0–100; reduce: הנקודות המובילות מסונתזות לתשובה"*, ומזהיר ש-*"חיפוש גלובלי (map-reduce) לוקח שניות עד עשרות שניות, מול חיפוש וקטורי תת-שנייתי"*. הגרסה האגנטית שלנו מוציאה את ה-reduce מהקוד: ה-map כבר נעשה ב-Plan 1 (השלב `communities` כתב 186 דוחות), בזמן שאילתה רצה רק שאילתת וקטור אחת — **195 ms** ל-`any` — וה-reduce עובר לסוכן, כלומר לתקציב ההקשר שלו (3,867 tokens). זה גם מה שמסביר למה `global_search` לא זול כמו `lookup` אבל גם לא "עשרות שניות": שילמנו את ה-LLM מראש.

**אריזת ההקשר היא תנאי להערכה, לא נוחות.** §4.4 קובע תקרה של ~4k tokens לכל תשובה, ו-`brain/retrieve/pack.py` מממש שני כללים בסדר הזה: *"at least one item of every kind survives"*, ואז הכול לפי score. הנימוק כתוב בראש הקובץ — *"without a ceiling, S2 wins every evaluation by returning more text than S1, and the number would measure verbosity rather than retrieval."* ב-S5 נוספה שכבה שנייה, `fit_to_share`: דוח לא מקוצץ עולה ~1,400 tokens, כלומר packer שמקצץ פריטים שלמים היה מחזיר 2 תמות מתוך 5; לכן כל דוח מקבל `BUDGET_TOKENS // k` ומשיל לפי סדר ערך — findings עודפים, אחר כך chunk ids, אחר כך ה-summary — כשכותרת, finding אחד וציטוט אחד תמיד שורדים.

**ו-`.mcp.json` שלנו שונה מזה שבקורס בפרט אחד.** הדוגמה במודול 8 מעבירה `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD` ב-`env` של קובץ ה-JSON; אצלנו `"env": {}` וההגדרות מגיעות מ-`.env` דרך `brain.config` — קובץ שנמצא ב-git לא מחזיק סיסמה.

## מספרים

| מדד | ערך |
|---|---|
| commits בשלב | `0472aae` (S5 + דירוג S3) · `342ba80` (15 הכלים על stdio ו-HTTP) |
| דוח | `data/reports/retrieve.json`, סעיפים `global` ו-`mcp` · `generated_at` **2026-09-17T06:31:08+00:00** · `transport: stdio` · `repeats: 3` |
| קהילות מסוכמות | רמה 0 (`fine`) **106** מסוכמות / 106 מוטמעות · רמה 1 (`coarse`) **80** / 80 · אינדקס `community_embedding` |
| זוגות חוצי-רמות עם אותו `member_hash` | **22** (`cross_level_duplicate_pairs`) |
| S5 על cq12 (*"What are the main themes of open bugs in clients?"*) | `any`: **195 ms**, 5 פריטים, **3,867 tokens**, 5/5 עם ≥1 `chunk_id`, `truncated=false` · `fine`: 119 ms / 3,855 tokens · `coarse`: 114 ms / 3,882 tokens |
| חמשת הדוחות ב-`any` | `L0-85` **0.7709** — *"New async consumer: threading, metrics and behavioural parity"* (1,139 חברים, rank 8.5) · `L1-200` **0.7628** — *"New async consumer threading model and client observability gaps"* (1,214, 8.5) · `L0-1436` **0.7576** — *"OAuth and TLS client authentication defects and extensions"* (30, 7.5) · `L1-80` **0.7536** — *"SASL, OAuth and principal building in Kafka security"* (201, 8.0) · `L1-198` **0.7524** — *"Kafka Streams error handling, grace periods and deprecation debt"* (161, 7.5) |
| findings | **8** לכל אחד מחמשת הדוחות (`finding_count`) — נשלחים `MAX_FINDINGS = 5`, כל אחד עד `FINDING_CHARS = 200` תווים עם ראיה אחת; `SUMMARY_CHARS = 360`, `MAX_PROVENANCE = 5`, `OVERFETCH = 4` |
| `duplicate_of` בחמשת הפריטים | **null בכולם** — האיחוד לא נדרש לפעול על השאלה הזאת |
| בדיקת ה-dedupe | **5 זוגות נבדקו** (מתוך 22), **5 תאומים גסים הודחקו**, **0 הפרות** |
| checks של `global` | 3/3 ok: `s5_answers_the_thematic_question_with_evidence` · `no_duplicate_member_set_returns_twice` · `five_reports_fit_the_context_budget` |
| משטח ה-MCP | **15 כלים · 2 resources · 1 prompt** · `15/15 parse as Result` · `no_tool_answers_with_an_error: none` |
| resources | `brain://schema` **25,841 תווים** (8 מפתחות: `allowed_procedures, index_meta, labels, node_count, relationship_count, relationships, sample_values, search_indexes`) · `brain://stats` **859 תווים** (6 מפתחות) |
| prompt | `answer_with_citations` — **הודעה אחת, 1,127 תווים** |
| קיטום (`impact("clients", depth=2)`) | **46→17 פריטים, 10.1k→3.9k tokens** (שורת Task 3 ב-`progress.md`) · בדוח: 17 פריטים, **3,887 tokens** מול תקציב **4,000**, kinds ששרדו `Change, Document, Row, WorkItem` |
| כלים שחזרו `truncated=true` | **4 מתוך 15**: `search_with_context`, `get_schema`, `changes_between`, `impact` |
| תקציב אריזה | `BUDGET_TOKENS = 4000` · `CHARS_PER_TOKEN = 3.49` (נמדד ב-`brain chunk --measure`) · `SNIPPET_CHARS = 600` |
| לוג הקריאות | `data/logs/retrieval.jsonl` — `ts, question, strategy, cypher, latency_ms, hit_ids, tokens_out, mode, truncated`; כרגע **150 שורות עם `"mode": "mcp"`** |
| compose | `brain-mcp`, profile `mcp`, image `ghcr.io/astral-sh/uv:python3.11-bookworm-slim`, `127.0.0.1:8765:8765`, healthcheck על `/healthz` כל 10s עם `start_period: 180s` — **לא הורם** |
| HTTP (בדיקה חיה) | `brain serve --http --port <free>` → `/healthz` מחזיר `{"status": "ok", "tools": 15}`; `/mcp` מחזיר את 15 השמות |
| בדיקות | **1,565** (`make check`) · `tests/test_mcp_contract.py` **17 פונקציות** (ללא DB) · `tests/live/test_mcp_live.py` **10 פונקציות live** |

**הכלים, שורה לכל אחד** (`p50` = round trip מלא מעל stdio, חציון של 3; `latency_ms` = האחזור עצמו כפי שהמעטפת דיווחה בסבב האחרון):

| כלי | מה הוא עושה | p50 | `latency_ms` | items (kinds) |
|---|---|---|---|---|
| `route` | הצעת ה-pre-router הדטרמיניסטי + סיבה — עצה, לא הוראה | **2 ms** | 0 | 1 (Row) |
| `cypher_examples` | few-shot מאומת לפי סוג שאלה (`rationale` נבדק) | **2 ms** | 0 | 2 (Row) |
| `get_schema` | תוויות, סוגי קשתות ואינדקסים — לפני כתיבת Cypher | **3 ms** (max 304) | 0 | 33 (Row), `truncated` |
| `status_at` | סטטוס פריט בתאריך, משחזור `StatusChange` (`KAFKA-15538 @ 2024-01-11`) | **5 ms** | 3 | 2 (Row, WorkItem) |
| `timeline` | כל שינוי שנרשם על פריט, מהישן לחדש | **6 ms** | 3 | 28 (Row, WorkItem) |
| `run_cypher` | Cypher קריאה בלבד מאחורי ה-guard | **7 ms** | 6 | 5 (Row) |
| `explain_edge` | כל קשת בין שני צמתים עם ה-provenance שלה (`KIP-1000` → `Decision\|add a listclientmetricsresource rpc…`) | **8 ms** | 5 | 1 (Row) |
| `assignees_over_time` | מרווחי ה-assignment של פריט (`KAFKA-14648`) | **8 ms** | 5 | 5 (Person) |
| `lookup` | צומת לפי מפתח + שכונה (`KIP-1`) | **9 ms** | 5 | 1 (Document) |
| `impact` | "אם נשנה — מה נשבר": issues פתוחים, טסטים, מסמכים, commits על אותם קבצים | **29 ms** | 26 | 17, `truncated` |
| `changes_between` | פריטי רכיב בין שתי גרסאות + ה-commits (`clients 3.5→3.6`) | **38 ms** | 34 | 14, `truncated` |
| `global_search` | S5 — דוחות קהילה לשאלה תמטית | **148 ms** | 122 | 5 (Community) |
| `search_chunks` | S1 — top-k chunks: hybrid / vector / fulltext | **156 ms** | 143 | 5 (Chunk) |
| `search_with_context` | S2 — chunks + ההורים והשכנים המובנים | **187 ms** | 225 | 4, `truncated` |
| `local_search` | S3 — עוגן ישויות, שכונה בעומק ≤2, ציטוטים | **260 ms** | 249 | 5 (Document, Entity) |

## מה הפתיע

- **שתי משבצות מתוך חמש הלכו לאותה תמה — וה-dedupe צדק שלא התערב.** בשאלה cq12 ב-`level="any"`, המקומות הראשון והשני הם `L0-85` ("New async consumer: threading, metrics and behavioural parity", 1,139 חברים) ו-`L1-200` ("New async consumer threading model and client observability gaps", 1,214 חברים) — שני דוחות על אותו נושא בשתי רמות Leiden, בציונים 0.7709 ו-0.7628, ששניים משלושת ה-chunk ids שלהם **זהים** (`673e1488…`, `114d619f…`). `member_hash` שלהם שונה (1,139 ≠ 1,214 חברים), ולכן החוק — "סט חברים זהה, משבצת אחת" — לא חל. האיחוד תופס **זהות** של סטים, לא **דמיון** של תמות; בשאלה הזאת ההבדל עלה 20% מהתשובה.
- **`duplicate_of: null` בכל חמשת הפריטים — ההוכחה היחידה לאיחוד היא בדיקה נפרדת.** 22 הזוגות קיימים בגרף, אבל אף אחד מהם לא נכנס ל-top-5 של cq12, ולכן הדוח של השאלה עצמה לא מראה את המנגנון עובד. מה שכן מראה הוא `_dedupe_check`, ששואל עם הכותרת של כל דוח כפול אם שני התאומים חוזרים: **5 זוגות נבדקו מתוך 22, 5 הודחקו, 0 הפרות**. פיצ'ר שהתוצאה הרגילה לא מדגימה חייב בדיקה משלו, אחרת אין דרך לדעת שהוא חי.
- **הסכמה עצמה לא נכנסת בתקציב.** `get_schema` חזר עם `truncated=true` על 33 פריטים — כלומר הכלי שנועד למנוע מהסוכן להמציא תווית מקצץ את עצמו. התשובה המלאה קיימת, אבל רק ב-resource `brain://schema` (25,841 תווים), ומי שלא יודע לקרוא אותו יראה סכמה חלקית בלי שנאמר לו מה חסר — פרט ל-`truncated` במעטפת.
- **ה-fallback שנכתב ולא נדרש.** השרת מייבא את שלישיית S4 (`get_schema`, `cypher_examples`, `run_cypher`) **בתוך הקריאה**, עם מעטפת שגיאה שאומרת במפורש `"{name} is built in Plan 2 Task 2 and is not importable here"` ו-`route.fallback_from: "s4"` — כדי ששרת יוכל לעלות לפני שהמשימה השכנה נחתה. Task 2 נחת בזמן, ולכן **0 מתוך 15 הכלים** החזירו שגיאה. השאלה מה קורה כשמשימה מקבילה מאחרת נענתה בקוד ולא בהמתנה.
- **`latency_ms` ו-p50 לא מסתדרים — וזה ארטיפקט של הדוח.** `search_with_context` מדווח round trip p50 **187 ms** ולידו `latency_ms` **225 ms**, כלומר האחזור לכאורה ארוך מהמסע כולו. הסיבה ב-`report.py`: הלולאה שומרת את **המעטפת האחרונה** מתוך שלושת הסבבים בעוד שה-round trip הוא החציון, וה-max של אותו כלי הוא 228 ms. המספרים נכונים, ההשוואה ביניהם לא.
- **שירות ה-HTTP לא הורם, וההוכחה הגיעה ממקום אחר.** `brain-mcp` יושב תחת profile `mcp` ולא נכנס ל-`make up`; ההפעלה הראשונה בונה את הסביבה מ-`uv.lock` בתוך הקונטיינר (`start_period: 180s`), ולכן לא הורם בשלב הזה. מה שכן רץ הוא בדיקה חיה שמריצה `brain serve --http` על פורט פנוי ומאמתת ש-`/healthz` מחזיר `{"status": "ok", "tools": 15}` ושה-`/mcp` מחזיר את 15 השמות. קריטריון הקבלה בבריף (*"compose `up` → healthy"*) נשאר לא מסומן.
- **בדיקת החוזה נכתבה מתוך המפרט, ביד, בכוונה.** ההערה ב-`tests/test_mcp_contract.py` מסבירה: *"if this and `TOOL_NAMES` are edited together by accident the test is worthless — so it is written from the spec, not imported from it."* אותה בדיקה גם קוראת את `.mcp.json` ואת שורת `tools:` של `brain-analyst` ומוודאת `== {mcp__brain__<name>} | {Read}` באורך 16. הכישלון שהיא מיועדת למנוע מתואר שם: *"a tool renamed on one side of the contract and not the other is exactly the failure a session notices as 'the agent has no tools', hours later, with no error anywhere."*
- **שתי הערות סקירה מ-Task 3 נשארו פתוחות, ואחת מהן סותרת הודעת commit.** `local_search` **לא מחזיר את מסמך העוגן עצמו** — `KIP-848` נשאר מחוץ ל-top-10 של השאלה עליו, כי עוגן מקבל 1.0 ושכן עם 3 מסלולים מקבל 1.43; הודעת `0472aae` טענה שזה תוקן, המדידה אומרת שלא. ובאותה משפחה: top-1 לא תלוי בשאלה — צמתים בלי `entity_embedding` (Document/WorkItem/Person/Component) מקבלים גורם ניטרלי 1.0, ולכן `KIP-932` יצא ראשון בשתי שאלות שונות **באותו ציון 1.43 בדיוק**.

## מה היינו משנים

- **איחוד לפי חפיפת חברים, לא רק לפי hash זהה.** `member_hash` הוא sha1 על מפתחות החברים — בדיקת שוויון, כן/לא. בפועל התופעה שעולה למשתמש היא **חפיפה גבוהה**: 1,139 מול 1,214 חברים על אותו נושא הם שני דוחות שהסוכן יסנתז לאותה פסקה. מדד Jaccard על סטי החברים, עם סף שנמדד פעם אחת ומדווח, היה תופס את `L0-85`/`L1-200` בלי לאחד קהילות שבמקרה מדברות על אותו מודול. היום הגבול בין "אותו דבר" ל"נושא דומה" מוכרע ע"י פונקציית hash, וזה גבול שלא נבחר — הוא נגזר.
- **לבדוק את כל 22 הזוגות, לא את חמשת הראשונים.** `_dedupe_check` עוצר על `duplicates[:5]`, וזה נקבע כדי שהדוח יישאר מהיר. אבל 5/22 זה 23% כיסוי על המנגנון היחיד שמונע כפילות בתשובה תמטית, והבדיקה עולה שאילתת וקטור אחת לזוג — כלומר ~17 קריאות של ~120 ms. עלות שלא הייתה מורגשת, כיסוי שהיה עולה ל-100%.
- **המעטפת שנשמרת בדוח צריכה להיות זו שמתאימה ל-p50.** לשמור את שלושת הסבבים (או לפחות את זה שהזמן שלו הוא החציון) היה מייתר את ההערה "אל תשוו בין שתי העמודות". במצב הנוכחי טבלת ה-latency היא שני מדדים שונים באותה שורה, ומי שיקרא אותה בעוד חודש יסיק ממנה overhead שלילי.
- **`get_schema` צריך להצביע על ה-resource כשהוא מקצץ.** הכלי יודע שהוא חתך (`truncated=true`) ויודע שיש גרסה מלאה ב-`brain://schema`; הסוכן לא יודע אלא אם קרא את `INSTRUCTIONS`. שורת `hint` בתוך התשובה — *"the full schema is `brain://schema`"* — הייתה הופכת את הידיעה הזאת מהסתמכות על קריאת הוראות למשהו שמגיע עם הנתון.
- **להרים את `brain-mcp` פעם אחת ולתעד את הזמן.** הקריטריון "compose up → healthy" נשאר פתוח כי הבנייה הראשונה ארוכה. זה בדיוק הסוג של דבר שנשאר פתוח לנצח: 180 שניות פעם אחת, מספר בדוח, וסעיף שאפשר לסמן. כרגע התשובה לשאלה "האם ה-HTTP עובד ב-compose" היא "כנראה — הבדיקה החיה מריצה את אותו קוד בלי הקונטיינר".
- **עוגן מוצמד ראשון ב-`local_search`, לפני שהשער רץ.** ההכרעה כבר רשומה כפריט פתוח ("לתקן בסבב הסקירה של Plan 2"), אבל השאלות של Task 4 הן בדיוק השאלות שיפגשו את הבאג: שאלה שמזכירה `KIP-848` ולא מקבלת את `KIP-848` בתשובה נראית לסוכן כמו קורפוס חסר, לא כמו דירוג שגוי — והוא ימשיך לכלי הבא במקום לצטט את המסמך שמולו.

## מה זה מלמד (המתכנן)

**1. Agentic GraphRAG = הסוכן בוחר כלים; ה-MCP הוא הממשק, לא הקסם.** 15 כלים, כל אחד עוטף פונקציה שכבר נמדדה ב-Python. הקורס (מודול 8) ממליץ על "שלושה כלים מינימליים"; אנחנו נתנו 15 — כי כל אסטרטגיה צריכה להיות ניתנת למדידה בנפרד. המחיר: הסוכן צריך לבחור; הרווח: Plan 3 יראה בדיוק אילו כלים נבחרו ומתי, מהלוג (`mode: mcp`).

**2. אין כלי כתיבה בכלל.** בקורס: "`write_neo4j_cypher` — you can simply not grant it". אצלנו זו לא הרשאה שאפשר להעניק בטעות — הכלי לא קיים, ו-`run_cypher` יושב מאחורי ארבע שכבות. חוזה בקוד (`test_mcp_contract.py`) קושר את `.mcp.json` ואת רשימת הכלים של הסוכן ל-`TOOL_NAMES`: שינוי שם באחד = כישלון `make check`, לא "לסוכן אין כלים".

**3. Global search בזול — כי המפה שולמה ב-Plan 1.** MS GraphRAG עושה map-reduce על כל הקהילות בזמן השאלה ("שניות עד עשרות שניות"). כאן: embedding של השאלה → top-5 דוחות קהילה → 195 ms, 3.9k tokens. ה-reduce עבר לסוכן, כי בגרסה אגנטית הסוכן ממילא קורא ומסכם. 22 זוגות fine=coarse מאוחדים לפי `member_hash` — אבל שני מהחמישה של cq12 הם אותו נושא בשתי רמות עם member sets *שונים*, וה-dedupe שותק בצדק. Jaccard במקום שוויון-hash — לרשימת השינויים.

**4. אריזה מדודה: 46 → 17 פריטים, 10.1k → 3.9k tokens, כל kind שרד.** זה מה ש-§4.4 דורש, וזה מה שמאפשר לשופט להשוות. 4/15 כלים חזרו `truncated=true` — הסוכן רואה את זה ויכול לבקש עוד. סוכן שלא יודע שקוצץ = סוכן שמנחש.

**5. `.mcp.json` בלי סיסמה בקוד.** `"env": {}` — ההגדרות מגיעות מ-`.env` של הפרויקט דרך `Settings`. הקורס מראה `mcp-neo4j-cypher` עם `NEO4J_PASSWORD` ב-JSON שנכנס ל-git. אצלנו התשובה לשאלת המודול 10 "איפה הסוד" היא: לא בקובץ שמפרסמים.

**6. compose service שלא הורם הוא קריטריון קבלה שלא סומן.** `brain-mcp` מוגדר ומפורסר, HTTP הוכח על ה-host, אבל הקונטיינר לא רץ (build של דקות). זה מתועד כמו שהוא. בארגון: שירות שמעולם לא עלה הוא שירות שלא קיים.

**מה זה קובע ל-Task 4:** סשן חדש (`.mcp.json` נטען בעלייה) → `/mcp` מראה `brain` → `brain-analyst` ×3 על 19 שאלות → `cite-check` בקוד. הצעד הזה הוא של המשתמש; המתכנן מפזר אחרי אישור החיבור.
