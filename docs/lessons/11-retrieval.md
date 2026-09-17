# שיעור 11 — ספריית האחזור: מעטפת אחת, ראוטר שממליץ, וארבע אסטרטגיות שמדווחות מה באמת רץ

**תאריך:** 2026-09-07 · **תכנית:** Plan 2 (Task 1) · **מודול בקורס:** 4 ("אסטרטגיות אחזור: הארסנל המלא")

## מה עשינו

Task 1 הוא הפעם הראשונה שהפרויקט **שואל** את הגרף במקום לבנות אותו: `brain/retrieve/` — **21 מודולים**, commit `f7262d8` — מממש את S1 (hybrid chunks), S2 (graph-enhanced vector), S3 (entity-anchored local) ו-S6 (ארבעה כלים טמפורליים דטרמיניסטיים), ולצידם `lookup`, `explain_edge`, `impact` ו-`route`, כולם מאחורי מעטפת `Result` אחת (`brain/retrieve/types.py`) וכולם נגישים גם מ-Python וגם מ-`brain ask "<שאלה>" [--strategy s1|s2|s3|s4|s6|lookup|impact] [--k] [--rerank] [--json]`.
כל קריאה — מ-CLI, מ-Python ומ-MCP אחר כך — נרשמת כשורת JSONL ב-`data/logs/retrieval.jsonl` עם `strategy, cypher, latency_ms, hit_ids, tokens_out, mode`, וכל תשובה נארזת לתקרת **~4,000 tokens** (`3.49` תווים ל-token, נמדד ב-`brain chunk --measure`) עם הכלל "פריט אחד מכל kind שורד".
סט הכשירות עצמו נבנה **בקוד** ולא ביד: 12 סלקטורי-על ב-`brain/retrieve/competency.py` בוחרים את העוגנים מהגרף (ה-issue שהכי הרבה טסטים מכסים, ה-KIP עם הכי הרבה קומיטים/החלטות/דחיות, ה-component עם הכי הרבה קומיטים פותרים), ומהם נכתבות **19 שאלות** ל-`data/eval/competency.jsonl` — 15 באנגלית ו-4 בעברית, שכל אחת מהן היא תרגום של שאלה אנגלית עם אותו `pair` ואותם עוגנים.
המדידה ב-`data/reports/retrieve.json` (נוצר `2026-09-07T15:06:07+00:00`) נתנה **18/18** שאלות-ראיה עם provenance תקף, **0** ציטוטים למזהי chunk שאינם קיימים, ראוטר שהתאים ל-`expected_strategy` ב-**19/19**, ו-p50 של 115–197ms לכל S1/S2/S3 מול תקציב של 1,500ms.
שני דברים לא עבדו כמתוכנן ונרשמו ככאלה: `SEARCH VECTOR INDEX` נדחה בשגיאת תחביר על ידי אותו שרת (`Neo4j Kernel 2026.06.0 (community)`) שמכריז על `db.index.vector.queryNodes` כ-deprecated לטובתו — ולכן נבחרה הפרוצדורה; ושלוש שאלות שהראוטר שלח ל-S4/S5, שעדיין לא היו קיימים ב-Task 1, רצו דרך `impact`/S3/S1 עם `fallback_from` מפורש בדוח.
דירוג S3 תוקן אחרי המדידה (`0472aae`): עד אז שתי שאלות שונות על אותו KIP החזירו רשימות זהות בייט-בייט.

## למה ככה (הקישור לקורס)

**המודול פותח בטבלה של שש אסטרטגיות ובמשפט שהוא הבריף של השלב הזה:** *"שש דרכים לאחזר מגרף — ואיך יודעים איזו מתאימה לאיזו שאלה"*. הספק שלנו (§4.1) הוא אותה טבלה בשמות אחרים, וההתאמה היא אחד-לאחד:

| בקורס (מודול 4) | אצלנו | צורת השאילתה | על מה עונה |
|---|---|---|---|
| "Vector → Graph expansion" — *"הדפוס הנפוץ ביותר בפרודקשן"* | S2, `graph_vector.py::search_with_context` | וקטור על `chunk_embedding` → parent (`WorkItem/Document/Commit`) → 1–2 hops על 9 קשתות עקיבות | ברירת המחדל כשהשאלה לא נוקבת בעוגן (cq05, cq07, cq17) |
| "Entity linking → k-hop" (ה-Local Search של MS GraphRAG, sub-retrievers של LlamaIndex) | S3, `local.py::local_search` | regex keys → עוגן ישיר; אחרת embedding שאלה → `entity_embedding` → שכונה עומק ≤2 | רציונל והשפעה (cq09–cq11, cq02, cq08) |
| "Text2Cypher" | S4 — Task 2 | — | אגרגציות (cq03, cq06) |
| "Global / Community" | S5 — Task 3 | — | תמטי (cq12) |
| הבסיס ההיברידי (*"ה-full-text תופס בדיוק את מה ש-embeddings מפספסים"*) | S1, `hybrid.py::search_chunks` | vector + fulltext על `chunk_text`, מיזוג RRF (`RRF_K=60`) | baseline ו"מה כתוב על X" |
| גרפים טמפורליים (מודול 8: *"'מה נכון עכשיו' ו'מה היה נכון במרץ' הן שתיהן שאילתות לגיטימיות"*) | S6, `temporal.py` | `StatusChange` + `ASSIGNED_TO{valid_from, valid_to}`, `<=` ו-`>` בלבד | cq13–cq15, cq19 |

**שני "כללי הברזל להרחבת שכנויות" של המודול הם בדיוק הקבועים ב-S2.** הקורס מזהיר: *"(1) תמיד להחריג קשתות מבניות … אחרת 'מסלול בגרף' יזלוג דרך השכבה הלקסיקלית לכל הקורפוס. (2) להגביל ל-1–2 hops."* אצלנו זה `CONTEXT_RELS` — רשימה סגורה של תשע קשתות דומיין (`REFERENCES, LINKS_TO, TESTS, HAS_RUN, RESOLVES, IMPLEMENTS_KIP, ASSIGNED_TO, IN_COMPONENT, FIX_VERSION`) — ועוד תקרה של `NEIGHBOURS_PER_ANCHOR = 25`, שהדוקסטרינג מנמק במספר ולא בתחושה: *"hop 3 from any Kafka issue reaches component `core` and from there most of the corpus."* אותו היגיון מופיע ב-`impact.py`, שם `TOUCHES` הוא **48,036 קשתות** ולכן סט הקבצים נחתך ל-20 לפני השימוש — *"a commit that touched 300 files (a formatting run) would otherwise make every commit in the repository 'impacted', which is true and useless."*

**הדירוג של S3 הוא בדיוק ה"אות המבני שקיים רק בגרף" שהמודול מדבר עליו** (*"דירוג לפי מרחק בגרף מצומת מוקד … אותות שקיימים רק כשיש גרף"*): degree **בתוך השכונה המעוגנת** × משקל קשת (`DECIDES/MOTIVATED_BY/REJECTS` = 1.0, `IMPLEMENTS/TESTS/RESOLVES` = 0.9, `DEPENDS_ON/INTRODUCES_RISK` = 0.8, `MENTIONS` = 0.35, `HOP_DECAY` = 0.55 להופ שני). degree גלובלי היה מחזיר את `Technology|kafka` לכל שאלה; degree מקומי מחזיר את הצומת שהעוגנים מסכימים עליו. הכרעה 8 בתכנית היא שהכניסה את `MENTIONS` לרשימה מלכתחילה: **81% מה-`Decision` שחולצו הם `weak=true`**, ובלי `MENTIONS` מסלול "למה הוחלט" היה עונה על 19% משאלות הרציונל.

**הראוטר הוא המקום היחיד שבו סטינו מהמימוש שהקורס מציג — במכוון.** תרגיל 4.2 ודוגמת הקוד בונים ראוטר על `gpt-4o-mini` עם `with_structured_output`; `brain/retrieve/route.py` הוא regex טהור, וה-docstring מסביר למה: *"a router that is itself a model cannot be a baseline for a model."* סדר הכללים גם הוא לא סדר הספק אלא סדר **ספציפיות**: טמפורלי (`iso-date`, `over-time`, `he-changed`…) לפני אגרגציה (`superlative`, `which-have`, `כמה `, `הכי `) לפני תמה (`themes`, `נושאים`, `מגמות`) לפני מפתחות — כי *"What was the status of KAFKA-15123 on 2024-03-01?"* נוקבת גם במפתח וגם בתאריך, ורק הכלל הטמפורלי יודע לענות עליה. כל כלל מחזיר גם את השם שירה (`route_rule`) וגם `confidence` שטוח (0.9 טמפורלי / 0.8 אגרגציה ותמה / 0.95 מפתח חשוף / 0.7 מפתח בתוך שאלה / 0.4 ברירת מחדל), וההמלצה **נרשמת ולא נכפית** — §4.2 אומר "לוג בלבד". השדרוג שהתרגיל מציע (*"הוסיפו את החלטת הניתוב ללוג של כל תשובה — תצטרכו את זה במודול 9"*) הוא בדיוק `log.py`, שקיים כדי ש-Plan 3 יוכל לעשות group-by על `strategy` במקום להתווכח.

**המעטפת האחידה היא מה שהופך את ההשוואה להוגנת.** הכרעה 1 בתכנית — "ספרייה אחת, שני משטחים" — אומרת ש-`brain/mcp/server.py` הוא מעטפת דקה בלבד, ולכן אותה פונקציה בדיוק מודדת בהערכת Plan 3 ומשרתת את הסוכן ב-MCP; §4.4 מוסיף את תקרת ה-4k. `pack.py` מנמק את שני הכללים: בלי תקרה *"S2 wins every evaluation by returning more text than S1, and the number would measure verbosity rather than retrieval"*, ובלי ההגנה על kind *"a traceability answer that drops its only `Change` because five chunks outscored it is not a shorter answer, it is a wrong one"*. `Result.cypher_used` הוא מאותה משפחה: *"paste it into the browser and you get the same rows, which is the difference between a demo and a measurement."*

## מספרים

| מדד | ערך |
|---|---|
| commit | `f7262d8` (Task 1) · תיקון דירוג S3 ב-`0472aae` |
| מודולים | **21** ב-`brain/retrieve/` |
| הגרף שעליו נמדד | **13,846** chunks (12,915 חיים), **13,846** עם embedding · **9,038** ישויות, כולן עם embedding · אינדקסים: `chunk_embedding`, `chunk_text`, `entity_embedding` |
| embedding | `bge-m3`, dim **1024**, זהה ב-`chunk_embedding` וב-`entity_embedding` |
| תחביר וקטורי | שרת `Neo4j Kernel 2026.06.0 (community)` · `SEARCH` → `CypherSyntaxError: Invalid input 'SEARCH'` · `db.index.vector.queryNodes` → `ok` · **נבחר `queryNodes`** |
| סט הכשירות | **19 שאלות** (15 EN + 4 HE) ב-`data/eval/competency.jsonl` · 4 סוגים: traceability / impact / rationale / temporal + אחת `global` |
| **ראיה** | **18/18** שאלות-ראיה עם provenance תקף · **0** chunk ids שאינם קיימים · **0** תשובות ריקות |
| ניתוב | `route_matches_expected` **19/19** · `fallbacks: ["s4", "s5"]` (cq03 → `impact`, cq06 → S3, cq12 → S1) |
| **cross-lingual (4 זוגות)** | S3: **4/4 זהות**, mean jaccard **1.0** · S1 hybrid mean jaccard **0.479** · S1 vector בלבד **0.396** |
| פירוט cross-lingual ב-S1 | `change` (cq05/cq17) **0.0** · `tests` (cq01/cq16) **0.25** · `why` (cq09/cq18) **0.667** · `changed` (cq13/cq19) **1.0** |
| latency p50 (תקציב **1,500ms**) | S1 **139** (n=3) · S1(fixed) **141** (n=57, p90 161, max 172) · S2 **176** (n=9, p90 188, max 231) · S2(fixed) **197** (n=57, p90 214, max 367) · S3 **125** (n=33, p90 190, max 312) · S3(fixed) **115** (n=57, p90 220, max 326) · S6 **26** (n=12, p90 54, max 58) |
| החריגים בריצת השאלות | cq06 **2,755ms** · cq05 **2,306ms** מול cq15 **16ms** · cq14 **22ms** · cq13 **51ms** |
| אריזה | תקציב **4,000 tokens** · `CHARS_PER_TOKEN = 3.49` · `SNIPPET_CHARS = 600` · **7 מתוך 19** תשובות חזרו `truncated: true` (cq03, cq05, cq07, cq12, cq13, cq17, cq19) |
| `ITEM_KINDS` | 9 ערכים סגורים: `Chunk, WorkItem, Document, Person, Change, Container, Entity, Community, Row` |
| **עוגנים שנבחרו בקוד** (12 סלקטורים) | `issue_most_tests` = KAFKA-14597 (**3** טסטים) · `kip_most_commits` = KIP-848 (**64**) · `kip_most_decides` = KIP-932 (**178**) · `kip_most_rejects` = KIP-796 (**12**) · `kip_most_motivated` = KIP-1071 (**12**) · `component_most_resolves` = `streams` (**472**) · `component_most_open_bugs` = `clients` (**101**) · `ado_story_kip` = KIP-848 (**8** סיפורי ADO) · `entity_most_depends` = `Technology\|versioned state store` (**4**) · `issue_most_status_changes` = KAFKA-15538 (**13**) · `issue_most_assignees` = KAFKA-14648 (**5**) · `version_window` = `clients` (**897** פריטים, 32 גרסאות) |
| S1 | `RRF_K = 60` · overfetch ×4 · `KEY_BOOST = 8` למפתחות בשאילתת Lucene · 3 מצבים (`hybrid/vector/fulltext`) |
| S2 | **9** סוגי קשת · `NEIGHBOURS_PER_ANCHOR = 25` · `EVIDENCE_CHUNKS = 3` · `ASSIGNED_TO` מסונן ל-`valid_to IS NULL` |
| S3 | משקלים 1.0 / 0.9 / 0.8, `MENTIONS` **0.35** (9,390 קשתות) · `HOP_DECAY = 0.55` · `PER_ANCHOR = 250` · `ENTITY_ANCHORS = 6` · `NEUTRAL_FACTOR = 1.0`, `MIN_FACTOR = 0.05` |
| S6 | 4 כלים: `status_at`, `timeline`, `changes_between`, `assignees_over_time` — ללא embedding, ללא LLM, ללא דירוג |
| `lookup` | ≤**50** שכנים, ≤**12** לכל סוג קשת, **3** chunks ראייתיים (chunks מוצאים מרשימת השכנים) |
| `impact` | 40 פריטים קשורים / 20 טסטים / 10 מסמכים / 20 קבצים / 10 קומיטים · `TOUCHES` = **48,036** קשתות |
| `explain_edge` | **16** שדות provenance לקשת · מסלול חלופי עד **3** hops · ≤**5** chunks שמזכירים את שני הצדדים |
| checks בדוח | **6/6 `ok`**: provenance 18/18 · 0 ציטוטים שבורים · 0 תשובות ריקות · HE↔EN 4/4 ב-S3 · p50 מתחת ל-1,500ms · תחביר וקטורי הוכרע מול השרת החי |
| בדיקות | `tests/test_retrieve_{route,pack,log,temporal,vector,nodes,context,rank,cli}.py` — **78** פונקציות · `tests/live/test_retrieve_live.py` — **28** |
| לוג | `data/logs/retrieval.jsonl` — שורה לכל קריאה; כשל כתיבה **לא** מפיל אחזור (נרשם ל-`last_error` עבור `brain doctor`) |

## מה הפתיע

- **`SEARCH` לא נתמך — על ידי אותו שרת שמכריז שהוא מחליף את `queryNodes`.** הכרעה 4 בתכנית ביקשה לבדוק ולתעד, ובדיוק בגלל זה יש בדוח `vector_syntax` עם שתי הבדיקות זו לצד זו: `SEARCH VECTOR INDEX … FOR $v` מחזיר `CypherSyntaxError: Invalid input 'SEARCH'` על `Neo4j Kernel 2026.06.0 (community)` — גם ב-CYPHER 5 וגם ב-CYPHER 25 — בזמן ש-`db.index.vector.queryNodes`, שמדפיס אזהרת deprecation לטובת `SEARCH`, פשוט עובד. אזהרת deprecation אינה הבטחה שהחלופה קיימת במהדורה שבה אתה רץ; המדידה מול השרת החי היא היחידה שקובעת.
- **הגרף הוא זה שנושא את הרב-לשוניות, לא ה-embedding.** בחרנו `bge-m3` דווקא כי הוא רב-לשוני (ADR-0003), אבל המספרים אומרים שמה שהפך את ארבע השאלות בעברית לזהות לאחיותיהן באנגלית הוא **המפתח שהשאלה נוקבת בו**: S3 החזיר **4/4 רשימות עוגן זהות** (mean jaccard 1.0), בעוד S1 hybrid הגיע ל-**0.479** ו-S1 vector בלבד — כלומר `bge-m3` לבדו, בלי Lucene — ל-**0.396**. הזוג `change` (cq05/cq17) הוא הקיצון: **jaccard 0.0**, שתי רשימות זרות לחלוטין (`KIP-1075, KAFKA-14565, KAFKA-14699…` מול `ADO-10060, ADO-10031, ADO-10226…`) לאותה שאלה בשתי שפות. הזוג `changed` (cq13/cq19) דווקא הגיע ל-1.0 ב-hybrid — כי המחצית ה-full-text מצאה את אותם מפתחות לטיניים בשתי השפות; הדוח מציין במפורש שאין כאן analyzer שמגשר בין עברית לאנגלית, ולכן `s1_vector` הוא המדד הכן.
- **שתיים מ-15 השאלות באנגלית לא ניתבו לשום דבר שהיה קיים — והדוח אמר את זה במקום להסתיר.** S4 נולד רק ב-Task 2 ו-S5 רק ב-Task 3, ולכן cq03 ("Who owns component `streams`…", `route_rule: superlative`) רץ דרך `impact`, cq06 דרך S3 ו-cq12 ("main themes…", `route_rule: themes`) דרך S1 — כל אחד עם `fallback_from` בשורה שלו. ההערה בדוח נוקבת: *"No question's routing was weakened to fit what is implemented."* קל יותר היה לשנות את `expected_strategy` ולקבל 19/19 נקי.
- **אין `Chunk-[:MENTIONS]->Component` בגרף הזה — ולכן עוגן שהוא component לא הולך לשום מקום ב-S3.** החילוץ ייצר `MENTIONS` רק ל-`Entity`, `WorkItem` ו-`Document`. התוצאה המעשית: שאלת אגרגציה שנוקבת ב-component (`streams`) נופלת ל-`impact`, שהוא היחיד שעובר דרך `IN_COMPONENT`. מגבלה של שלב החילוץ, שלושה שלבים אחורה, שמתגלה רק כשמישהו שואל.
- **S3 החזיר שתי רשימות זהות בייט-בייט לשתי שאלות שונות.** *"Why was the design in KIP-848 chosen?"* ו-*"Which commits fixed the bug behind KIP-848 rollout issues?"* קיבלו את אותה תשובה בדיוק, כי המפתח בחר את השכונה ו**שום דבר אחרי זה לא קרא את השאלה** — הדירוג היה degree × משקל קשת בלבד. `0472aae` הוסיף גורם שלישי, `(1 + cos(שאלה, ישות))`, מחושב ב-DB דרך `vector.similarity.cosine` (לא שליחת 1,024 floats לכל שכן חזרה ל-Python), עם פיזור של בערך `[1.3, 1.7]` — צר במכוון לעומת פיזור משקלי הקשת, כדי שהשאלה תשבור שוויון ולא תהפוך את הגרף. בדוח הזה עדיין נראית התמונה שלפני התיקון: cq02, cq04 ו-cq08 — שלוש שאלות שונות שנוקבות ב-KIP-848 — מחזירות את אותם חמישה עוגנים בדיוק.
- **התיקון תוקן חלקית, וההודעה של ה-commit הקדימה את המדידה.** שתי בעיות נשארו פתוחות ב-`progress.md`: (1) `local_search` **לא מחזיר את מסמך העוגן עצמו** — KIP-848 נשאר מחוץ ל-top-10 של שאלה שנשאלה עליו, כי עוגן ללא embedding מקבל גורם ניטרלי **1.0** ושכן עם שלושה מסלולים מגיע ל-**1.43**; *"הודעת ה-commit 0472aae טענה שזה תוקן — המדידה אומרת לא"*. (2) top-1 עדיין לא תלוי בשאלה עבור צמתים בלי `entity_embedding` (`Document`/`WorkItem`/`Person`/`Component`): KIP-932 יוצא ראשון בשתי שאלות שונות ב-**1.43 בדיוק**. ההכרעה: עוגן מוצמד ראשון בסבב הסקירה של Plan 2, וגורם מ-fulltext על `document_text`/`workitem_text` שיימדד ב-Plan 3.
- **"ה-issue שהכי הרבה טסטים מכסים" מכוסה ב-3 טסטים.** הסלקטורים חשפו כמה שטוח הזנב של הקורפוס: KAFKA-14597 עם **3** טסטים, `entity_most_depends` עם **4** תלויות, KIP-796 עם **12** דחיות — מול KIP-932 עם **178** החלטות ו-`streams` עם **472** קומיטים פותרים. שאלה "קלה" ושאלה "קשה" נבדלות כאן בשני סדרי גודל, וזה נקבע על ידי הקורפוס ולא על ידי מי שכתב את השאלה — וזו בדיוק הסיבה שהכרעה 7 דרשה לבחור בקוד.
- **p50 עבר בקלות, אבל קריאה בודדת הגיעה ל-2,755ms.** פרופיל ה-latency (3 חזרות לכל מדידה) נותן 115–197ms לכל S1/S2/S3, ובכל זאת בריצת השאלות עצמה cq06 לקח **2,755ms** ו-cq05 **2,306ms** — פי 20 מהחציון — בזמן ש-S6, שלא נוגע ב-embedding בכלל, סיים ב-**16–54ms**. מה שיקר זה הקריאה הראשונה ל-Ollama, לא הגרף.
- **`18/18` נשען גם על provenance מסוג חלש יותר.** ההערה האחרונה בדוח מודה בכך: פריטים שנוצרו בטראברסל (S6, `impact`) נושאים את **ה-chunk של התיאור של צומת העוגן** כראיה — *"It is weaker evidence than a quote"*. זה נראה במספרים: ב-cq15, 5 פריטי `Person` נושאים 10 רשומות provenance עם **chunk ייחודי אחד**, וב-cq14 שני פריטים עם chunk ייחודי אחד. הציטוט תקף, `brain eval cite-check` יאשר אותו — אבל הוא מצביע על העוגן, לא על המשפט שמוכיח את התשובה.

## מה היינו משנים

- **להצמיד את העוגן לראש התוצאה של S3 מיד, לא בסבב סקירה.** שאלה שנוקבת ב-KIP-848 ומקבלת תשובה שלא מכילה את KIP-848 היא באג שנראה כמו דירוג. התיקון זול (העוגן נכנס ראשון ואז הדירוג עובד על השאר), ומה שמעניין בו הוא איך הוא התגלה: לא בבדיקה אלא במדידה של Task 3 על קובץ של Task 1. **הודעת commit אינה ראיה; שורה בדוח היא.**
- **גורם סמנטי גם לצמתים בלי embedding.** `NEUTRAL_FACTOR = 1.0` הוא הכרעה הגיונית ("היעדר וקטור אינו ראיה לחוסר רלוונטיות") שמייצרת תופעה לא הגיונית: `Document`, `WorkItem`, `Person` ו-`Component` — כלומר רוב מה שמשתמש מזהה בשם — לא מושפעים מהשאלה כלל. הפתרון שכבר הוכרע (ציון fulltext של השאלה על `document_text`/`workitem_text`) היה צריך להיכנס יחד עם הגורם הסמנטי, באותו commit.
- **למדוד cross-lingual גם ל-S2, לא רק ל-S1 ול-S3.** ברירת המחדל של הראוטר היא S2, ואחת מארבע השאלות בעברית (cq17) נופלת עליה — אבל טבלת `cross_lingual` בודקת רק S1 (בשתי וריאציות) ו-S3. המסקנה "הגרף נושא את הרב-לשוניות" נשענת כרגע על האסטרטגיה שהכי קל לה ועל זו שהכי קשה לה, ומדלגת על זו שתרוץ בפועל כשאין מפתח בשאלה.
- **להפריד "ראיה מצוטטת" מ-"ראיה בטראברסל" בדוח.** `18/18` ו-`with_quote` באותה טבלה גורמים לשתי איכויות ראיה שונות להיראות זהות. שדה אחד (`provenance.source = "anchor-description"` מול ציטוט אמיתי) היה הופך את השורה הזאת ממדד כיסוי למדד איכות — וזה בדיוק מה ש-Plan 3 יצטרך.
- **לדווח latency קר וחם בנפרד.** `latency_profile` מריץ 3 חזרות ולכן מודד מערכת חמה; טבלת השאלות מודדת את מה שמשתמש מרגיש. שתיהן בדוח, אבל רק אחת מהן נבדקת מול התקציב. `p50 חם` ו-`max קר` הם שני מספרים שונים, ושניהם צריכים שער.

## מה זה מלמד (המתכנן)

**1. ספרייה אחת, שני משטחים — זו ההחלטה שהופכת את Plan 3 לאפשרי.** אותה פונקציית `local_search` נקראת מ-Python (הערכה head-to-head, מצב A) ומ-MCP (הסוכן השואל, מצב B). אם היו שני מימושים, ההשוואה "אסטרטגיה קבועה מול אגנטי" הייתה משווה גם שני קודים. ה-`Result` האחיד (9 kinds, provenance על כל פריט, `cypher_used`) הוא החוזה שמאפשר לשופט עיוור לקבל הקשר בלי לדעת מאיפה הגיע.

**2. ה-router הדטרמיניסטי הוא baseline, לא מוצר.** הקורס (מודול 4) מדבר על router-LLM. כאן: regex ומילות מפתח בעברית ובאנגלית, confidence קבוע, "מציע ולא כופה". הסיבה בקוד: "router שהוא בעצמו מודל לא יכול להיות baseline למודל". במצב B הסוכן רשאי לעקוף עם סיבה — וההערכה תמדוד כמה פעמים הוא צדק לעקוף.

**3. cross-lingual עובד — אבל לא בגלל ה-embedding.** S3 מחזיר 4/4 עוגנים זהים לעברית ולאנגלית כי המפתח `KIP-848` הוא אותו מפתח בשתי השפות. S1 hybrid מגיע ל-0.48 Jaccard בלבד, ו-vector-only ל-0.40. bge-m3 "תומך בעברית" — אבל הקורפוס באנגלית, ופרפראזה בעברית של "אם נשנה את X" מקבלת 0.0 חפיפה. הלקח לארגון דו-לשוני: **הגרף נושא את הרב-לשוניות, לא הווקטור**; ולכן מפתחות ומזהים חייבים לשרוד כל תרגום.

**4. `SEARCH` לא קיים בשרת שמכריז שהוא מחליף את `queryNodes`.** Neo4j 2026.06 Community מדפיס deprecation על `db.index.vector.queryNodes` ומצביע על `SEARCH` — שלא מפורסר. הסוכן בדק, תיעד, ובחר את הישן. זה "מה עובד מתי" בגרסה הכי מילולית: תיעוד של ספק ≠ מה שרץ אצלך.

**5. אריזה ל-4k tokens עם "פריט אחד מכל kind" — זו החלטת הוגנות, לא אופטימיזציה.** 7/19 תשובות נחתכו. בלי הכלל, שאלת השפעה הייתה מחזירה 40 commits ו-0 tests, והשופט היה מעניש את האסטרטגיה על מה שהאריזה זרקה. אותו budget לכל אסטרטגיה = התנאי להשוואה.

**6. דירוג לפי degree בלבד מייצר תשובה זהה לכל שאלה על אותו מפתח.** "למה נבחר KIP-848" ו-"אילו commits תיקנו את KIP-848" החזירו רשימות זהות בייט-בייט. התיקון (0472aae) הוסיף cos(שאלה, ישות) — ותיקן ישויות בלבד; מסמכים עדיין מקבלים גורם ניטרלי, ומסמך העוגן עצמו לא בתוך ה-top-10 שלו. הודעת ה-commit טענה יותר ממה שנמדד — הסוכן הבא מדד ותפס. הלקח: **תיקון דירוג נמדד על זוג שאלות, לא על אחת.**

**מה זה קובע ל-Task 4 ול-Plan 3:** הסוכן השואל מתחיל ב-`lookup` (העוגן), לא ב-`local_search`; S4/S5 מתועדים כ-fallback ב-`route.fallback_from` עד שנבנו — ולכן במדידה של Plan 3 אפשר להפריד "הכלי לא היה" מ"הכלי טעה".
