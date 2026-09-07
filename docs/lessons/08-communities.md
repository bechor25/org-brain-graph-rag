# שיעור 08 — `brain communities`: Leiden, 186 דוחות מצוטטים, ו-22 קהילות שנכתבו פעמיים

**תאריך:** 2026-09-07 · **תכנית:** Plan 1 (שלב 09) · **מודול בקורס:** 5 ("Microsoft GraphRAG מקצה לקצה", צעד 2: "להבין את פייפליין האינדוקס"), נספח LightRAG

## מה עשינו

שלב 09 בונה את התשתית ל-Global search: פרויקציה בזיכרון של GDS, Leiden היררכי מעליה, וסיכום LLM מצוטט לכל קהילה שגדולה מספיק כדי להצדיק אותו. הפרויקציה מכילה ארבע תוויות — `Entity` (9,038), `WorkItem` (1,416), `Document` (1,391), `Component` (30) — ומחזירה **11,875 צמתים ו-19,291 קשתות לא-מכוונות** (38,582 יחסים מאוחסנים, כי GDS שומר קשת לא-מכוונת כשני יחסים), density 0.00027362, ב-631ms. `Chunk`, `Person`, `Commit` ו-`PullRequest` אינם צמתים בפרויקציה; שני מסלולים **קורסו** לקשת אחת: `MENTIONS_PARENT` ו-`DELIVERS_KIP`. סינתטי נשאר בחוץ (`include_synthetic: false`).

Leiden רץ עם `randomSeed: 42`, `concurrency: 1`, `gamma: 1.0`, `maxLevels: 10` ו-`includeIntermediateCommunities: true`: **5 רמות, התכנס, modularity 0.906071** (29ms compute). מתוכן נבחרו ידנית `--level-indices 1,4` → רמה 0 (fine) עם **762 קהילות** ורמה 1 (coarse) עם **535** — סה"כ **1,297 קהילות ו-23,750 קשתות `IN_COMMUNITY`**. `--min-size 25` השאיר **186 קהילות לסיכום** (106 fine + 80 coarse) ו-**1,111 misc** שמקבלות צומת וחברוּת אבל לא דוח.

`brain communities batches` ארז את 186 הקהילות ל-**94 batches על 2 shards** (47+47), 4,650 שורות חברים ו-1,450 chunks ראייתיים, הגדול 36,481 בתים. שני סוכני `community-summarizer` כתבו את הדוחות, ו-`brain communities merge` אימת: **94/94 batches תקינים, 186 דוחות התקבלו, 0 נדחו, 1,527 findings (8.21 לדוח), 0 findings בלי ראיה**, 1,718 הפניות ל-`evidence_chunk_ids`. `community_embedding` (1024 מימדים, cosine) ONLINE ב-100%, ו-`IndexMeta` מדווח `live: 186`.

הריצה המתועדת של ה-merge היא **ריצה חוזרת**: 0 דוחות נכתבו, 186 `unchanged`, 0 embeddings נדרשו ו-0 נכתבו, `properties_set: 7`. הממצא הגדול של השלב הוא **22 סטים של חברים שקיבלו דוח בשתי הרמות** (44 קהילות) — קהילת coarse שהחזיקה בדיוק את חברי קהילת fine.

## למה ככה (הקישור לקורס)

**המודול מתאר את הפייפליין של MS GraphRAG בשש שורות טבלה, ושתיים מהן הן השלב הזה.** שורה 4: *"Communities — אלגוריתם Leiden בונה היררכיית קהילות מקוננת (C0 שורש → עלים)"*, ברירת מחדל `max_cluster_size=10`. שורה 5: *"Community reports — LLM כותב דוח לכל קהילה בכל רמה"*, עד 2,000 טוקנים לדוח. שורה 6: *"Embeddings — וקטורים לטקסט, לתיאורי ישויות ולדוחות קהילה"*. ולמה בכלל: *"Global Search — לשאלות על כלל הקורפוס. map: כל דוח קהילה (מהרמה שנבחרה, ברירת מחדל 2) מייצר נקודות ביניים עם ציון חשיבות 0–100; reduce: הנקודות המובילות מסונתזות לתשובה."* המחיר מופיע במודול 1: *"אינדוקס גרף מלא עולה בסדר גודל של פי ~1,000 מאינדוקס וקטורי בלבד"*.

המיפוי לקוד:

| שלב בפייפליין של MS GraphRAG | איפה בקוד |
|---|---|
| 4 · Communities (Leiden) | `brain/community/projection.py` — `projection_query()`, `gds.leiden.stats/stream` · `partition.py::pick_levels/partition` |
| 5 · Community reports | `batches.py` (top-25 חברים + ≤8 chunks) → `.claude/agents/community-summarizer.md` → `merge.py` (שלושה שערי ולידציה) |
| 6 · Embeddings לדוחות | `merge.py::embed_text` = `title + summary` → `graph.py::write_embeddings` → `community_embedding` |
| ציון חשיבות ל-map step | `rank` (0–10) + `rank_reason` על כל `Community`, `graph.py::top_ranked` |

ההבדל הכמותי מהברירה של הקורס: MS GraphRAG כותב דוח **לכל קהילה בכל רמה**; אצלנו 186 מתוך 1,297 (14.3%) מקבלות דוח, ומתוך 5 רמות Leiden נשמרו 2. ההבדל החוזי: אצל MS אין דרישה שכל טענה בדוח תצטט ראיה; ב-`brain/community/schema.json` וב-`models.py::Finding` **finding בלי `evidence_chunk_ids` הוא שגיאת ולידציה**, ו-`merge.py` דוחה דוח כזה.

**1. מה בפנים ומה בחוץ בפרויקציה — ולמה `Chunk` בחוץ אבל ה-`MENTIONS` נשאר.** `EXCLUDED_LABELS` נושא את הסיבה בטקסט שהדוח מצטט: `Chunk` — *"collapsed into parent-entity edges; a paragraph is not a member of a theme"*; `Person` — *"who spoke is not what a theme is about (spec 3.7)"*; `Commit` — *"collapsed into WorkItem-Document via RESOLVES + IMPLEMENTS_KIP"*; `PullRequest` — *"an endpoint of REFERENCES only; not a projected node"*. שני הקריסות: `(parent)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(thing)` → `MENTIONS_PARENT` (9,184 שורות מ-9,390 יחסים), ו-`(w:WorkItem)<-[:RESOLVES]-(:Commit)-[:IMPLEMENTS_KIP]->(d)` → `DELIVERS_KIP` (122 שורות מ-213 יחסים). ה-docstring מנמק את הראשונה במספר משלב 07: *"This is the only edge 83% of `Technology` entities have … so without it a sixth of the extraction sits outside every community."* בדיעבד, 736 ה-Technology אכן נחתו: **0 בקהילות יחיד, 200 (27.2%) ב-misc**. הענף הראשון ב-`UNION ALL` מקרין גם צמתים בלי אף קשת, בכוונה: *"Dropping isolated nodes would be the comfortable choice and the dishonest one: 'how many entities are in no community' is a finding about the extraction."* `INTRODUCES_RISK` (402 קשתות) נשאר מחוץ לסט של הבריף — ונספר, כדי שההשמטה לא תהיה בלתי-נראית.

**2. קשתות מקבילות קורסות לאחת — 866 מהן.** כל ענף ממיין את שני הקצוות לפי `elementId` ומחזיר `DISTINCT`, וההסבר בדוח הוא ניסוח הקוד עצמו: *"duplicate relationships of one type between one pair — and reciprocal a->b/b->a pairs — project one undirected edge of weight 1. Different relationship types between the same pair stay separate edges: that is two reasons, not one edge counted twice."* הסיבה מפורשת ב-`Branch`: GDS שומר כל שורה שמוסרים לו, ולכן שורה שנייה לאותו זוג היא יחס מקביל שני ו**כפול מהמשקל** ש-Leiden רואה על הקשת. בפועל קרסו **866**: `REFERENCES` 470 (זוגות הדדיים `a→b` ו-`b→a`), `MENTIONS_PARENT` 206 (KIP שמזכיר ישות בשש סקציות = קשת אחת), `DECIDES` 93, `DELIVERS_KIP` 91, `DEPENDS_ON` 2, `MOTIVATED_BY` 2, `LINKS_TO` 1, `REJECTS` 1. הקריסה מתבצעת בשאילתה ולא נשענת על ניקוי הכפילויות של שלב 08 — שרץ במקביל.

**3. למה רמות 1 ו-4 ולא "האחרונה ואחת מעליה".** הבריף ביקש 2 רמות: fine = האחרונה, coarse = אחת מעליה. Leiden רץ 5 רמות עם modularity שמתכנס: **0.767373 / 0.887045 / 0.904601 / 0.905861 / 0.906071** — רמות 3 ו-4 כמעט זהות על הקורפוס הזה (הכרעת המתכנן ב-`progress.md`: "רמות 3–4 זהות"). לכן הריצה קיבלה `--level-indices 1,4` (`level_map`: `0 → gds level 1`, `1 → gds level 4`). `pick_levels()` בוחר לבד לפי **מה שהרמה מכילה** ולא לפי האינדקס — *"of the levels kept, the one with more communities is level 0 (fine)"* — כדי ששינוי במספור של GDS לא יחליף fine ו-coarse בשקט. `build.py` נושא שומר מוצהר, `DEGENERATE_LEVEL_RATIO = 0.10`: אם שתי הרמות רחוקות זו מזו בפחות מ-10% הוא מזהיר ש-*"the hierarchy has converged"*. בריצה הזאת `warnings.level_choice` ריק — 762 מול 535 (29.8% הפרש), ו-186 בתוך התקציב.

**4. `--min-size 25` והתקציב 100–200.** ברירת המחדל בקוד היא 5. לפי ה-buckets בדוח, min-size 5 היה מייצר 365 קהילות fine + 155 coarse = **520 לסיכום**; min-size 25 מייצר 60+32+8+6 = 106 fine ו-25+28+17+10 = 80 coarse = **186** — בתוך היעד של הבריף (הכרעה 2), ו-`level_warnings()` מזהיר במפורש כשהמספר יוצא מ-100–200: *"brief 09 decision 2 targets 100-200. Tune `--min-size`, `--gamma` or `--levels` rather than accepting it by default."* קהילת misc אינה נזרקת: היא מקבלת צומת ו-`IN_COMMUNITY` בשתי הרמות, ו-`unplaced_members_fine_level` הוא **0** לכל ארבע התוויות. המחיר: 106 הקהילות המסוכמות ברמת fine מחזיקות **8,537 מ-11,875 החברים**, וברמת coarse **10,602** — הכיסוי שהמתכנן קיבל כ-89%.

**5. פורמט ה-batch.** אותם כללים ששלב 04 שילם עליהם ושלב 07 כתב: JSON מוזח, ≤40,000 בתים לקובץ, כי כלי ה-Read של סוכן-LLM חותך שורה ארוכה אחת. לכל קהילה: **top-25 חברים לפי degree בפרויקציה** (לא ב-Neo4j — *"a `WorkItem`'s Neo4j degree is dominated by its `StatusChange` events"*), ועד **8 chunks ראייתיים של ≤600 תווים**, מדורגים לפי כמה מישויות הקהילה כל chunk מזכיר, עם נפילה לחזרה ל-chunks של ההורים כשאין מספיק. הדוגמה: `data/batches/communities/shard-01/003.in.json` — 2 קהילות, 50 חברים, 15 ראיות, 31,601 בתים; קהילה `L0-1352` (fine, size 39, `member_hash` `9cbb2114…`, `members_shown: 25`, החבר החזק `KIP-1150` ב-degree 52.0). ב-`003.out.json` הדוח שלה: כותרת **"Diskless topics: object storage replacing block storage"**, `rank` 9.5, 8 findings. שניים מהם, כלשונם ועם הראיות שלהם:

> *"KIP-1150 is Accepted, authored by Greg Harris, Ivan Yurchenko, Jorge Quilcate, Giuseppe Lillo, Anatolii Popov, Juha Mynttinen, Josep Prat and Filip Yonov, and tracked under KAFKA-19161 as the root issue for the sub-KIPs."* → `a59f4e3076bb3a6e9d9b05bf9bb61ed52dbf0c23`, `cfd270a4d85091d2802a14f8e44075f20827d4d1`

> *"Doing nothing is framed as the highest-stakes alternative: the KIP argues it would become the single most substantial missing feature upstream, push high-scale and cloud users to alternatives, and fragment or forfeit Kafka's control over the Kafka protocol."* → `bb206e47ace7fd926f435f37253f837f527b2b26`

הקהילה השנייה באותו batch, `L0-1410`, מחזיקה 611 חברים — ומקבלת בדיוק אותם 25 חברים ו-8 ראיות.

**6. ולידציה של ה-merge: שלושה שערים, ושתי בדיקות שדורשות את הקלט.** קודם JSON Schema (`jsonschema_mini` מול `brain/community/schema.json`, sha256 `ad20e98a…`), אחר כך pydantic (`BatchOutput`), ואז `screen()` — שם הכללים שסכימה לא יכולה לנסח: `unknown_community` (דוח על קהילה שה-batch לא שאל עליה), `duplicate_report`, `evidence_not_offered` (*"a citation of another community's chunk is a hallucinated link, not a cross-reference"*), `evidence_not_in_graph` (המזהה הוצע אבל ה-chunk כבר לא קיים — נבדק מול `existing_chunk_ids`), `finding_without_evidence`, ו-`not_answered` לקהילה שה-batch שאל עליה ולא נענתה. דוח שנופל עולה דוח אחד; batch שנופל עובר ל-`retry/` פעמיים ואז ל-`quarantine/`. התוצאה: **94/94 תקינים, 186/186 התקבלו, 0 נדחו, `rejection_reasons` ריק, `rejected_batches` ריק**, ו-`findings_without_evidence` = 0 על **כל** מה שהסוכנים כתבו, לא רק על מה שהתקבל. ה-provenance נחתם בקוד ולא ע"י הסוכן (`evidence_chunk_ids`, `batch_id`, `model: "opus:community-summarizer"`, `extracted_at` = mtime של קובץ הפלט) — ו-`provenance_gaps()` מוודא מול הגרף החי: **0 קהילות מסוכמות בלי provenance**.

**7. אידמפוטנטיות: `member_hash` ולא `Community.id`.** מזהה הקהילה הוא `L<level>-<leidenId>`, וה-id של Leiden נגזר ממזהי צמתים פנימיים שזזים בכל פרויקציה מחדש. `member_hash` — sha1 על רשימת `Label:key` ממוינת — לא זז, ולכן הוא המפתח של "לא לסכם שוב": `read_reports()` נקרא **לפני** שהפרטיציה מוחלפת ו-`carry_reports()` מחזיר את הדוח והווקטור אחריה. הריצה החוזרת של ה-build כתבה 1,297 קהילות ו-23,750 חברויות עם **0 nodes_created, 0 מחיקות** (`properties_set` 60,470), והריצה החוזרת של ה-merge כתבה **0 דוחות, 186 unchanged, 0 embeddings** ו-`properties_set: 7` — שבע התכונות של צומת `IndexMeta` אחד. `report_is_current()` מסביר למה לא פשוט `SET c += props`: *"would happily rewrite 186 identical nodes and report 186 writes, which reads as work and is noise."*

**8. האינדקס ו-`IndexMeta`.** `apply_community_schema()` מצהיר constraint על `Community.id`, אינדקסים על `level` ועל `member_hash`, ואת ה-vector index `community_embedding` (1024, cosine). ההמתנה היא **polling לפי ארבעת השמות של השלב** ולא `db.awaitIndexes`, עם הנימוק המדוד מהשלב הקודם: *"`CALL db.awaitIndexes` is database-wide … this call sat for four minutes on a `_ResetTestbrain_file_path_key` stuck at POPULATING 100% while three agents shared one Neo4j."* בדוח: `awaited_indexes: 4`, `await_ms: 4`. `write_index_meta()` רושם ליד האינדקס מי ייצר את הווקטורים — `model: bge-m3`, `dim: 1024`, `similarity: cosine`, `label: Community`, `live: 186`, `state: ONLINE` — כי *"a question embedded by a different model returns nonsense rather than an error"*, ו-`live` נקרא מהגרף כדי שאפשר יהיה לזהות אינדקס חצי-מוטמע בלי להאמין לחשבון של הריצה עצמה.

## מספרים

| מדד | ערך |
|---|---|
| commits בשלב | בסיס `9ae7685` → `4d0f06e` (איחוד קשתות מקבילות, p95), `ce0138b` (density נמדד ולא אפס בברירת מחדל), `ae43551` (merge + כלל ההעתקה); סגירה `88c7456` |
| פקודות | `brain communities build [--levels 2] [--level-indices FINE,COARSE] [--min-size 25] [--seed 42] [--gamma 1.0] [--include-synthetic] [--dry-run]` · `batches [--shards 2] [--top-members 25] [--evidence 8] [--evidence-chars 600] [--resummarize]` · `merge` |
| **פרויקציה** | **11,875 צמתים · 19,291 קשתות לא-מכוונות** (38,582 יחסים מאוחסנים) · density **0.00027362** · project 631ms (GDS 444ms) · `brain_communities`, undirected |
| חברים לפי תווית | `Entity` **9,038** · `WorkItem` **1,416** · `Document` **1,391** · `Component` **30** |
| מחוץ לפרויקציה | `Chunk`, `Person`, `Commit`, `PullRequest`; `synthetic=true` (`include_synthetic: false`); `INTRODUCES_RISK` **402 קשתות** |
| שורות לפי ענף | `MENTIONS_PARENT` **9,184** · `DECIDES` 2,698 · `REFERENCES` 2,387 · `IN_COMPONENT` 2,316 · `MOTIVATED_BY` 1,336 · `REJECTS` 641 · `LINKS_TO` 297 · `DEPENDS_ON` 252 · `DELIVERS_KIP` **122** · `IMPLEMENTS` 58 |
| קשתות מקבילות/הדדיות שקרסו | **866** סה"כ: `REFERENCES` **470** · `MENTIONS_PARENT` **206** · `DECIDES` 93 · `DELIVERS_KIP` 91 · `DEPENDS_ON` 2 · `MOTIVATED_BY` 2 · `LINKS_TO` 1 · `REJECTS` 1 |
| **Leiden** | `randomSeed 42`, `concurrency 1`, `gamma 1.0`, `maxLevels 10`, `includeIntermediateCommunities` · **5 רמות, `didConverge: true`** · **modularity 0.906071** · compute 29ms / stats 40ms / stream 250ms |
| modularity לפי רמת GDS | **0.767373 · 0.887045 · 0.904601 · 0.905861 · 0.906071** (רמה אחרונה: 535 קהילות) |
| הרמות שנבחרו | `gds_levels_used: [1, 4]` → level 0 = fine (**762**), level 1 = coarse (**535**) |
| **סה"כ קהילות** | **1,297** (`min_size` 25) · **186 לסיכום** (106 fine + 80 coarse) · **1,111 misc** · **23,750** חברויות |
| גדלים — fine | min 1 / p50 **4** / p90 34 / p95 53 / max **1,139** / mean 15.58 · buckets `1-1`:355, `2-4`:42, `5-9`:131, `10-24`:128, `25-49`:60, `50-99`:32, `100-249`:8, `250+`:6 |
| גדלים — fine מסוכמות | 106 קהילות / **8,537 חברים** · min 25 / p50 45 / p90 140 / p95 282 / max 1,139 / mean 80.54 |
| גדלים — coarse | min 1 / p50 **1** / p90 51 / p95 98 / max **1,214** / mean 22.2 · מסוכמות: 80 / **10,602 חברים** / mean 132.53 |
| שיבוץ (fine) | `Entity` 2,277 ב-misc (**25.2%**), 0 בקהילת יחיד · `WorkItem` 318 (22.5%) · `Document` 733 (**52.7%**) + **355 בקהילת יחיד** · `Component` 10 (33.3%) |
| שיבוץ לפי kind | `Problem` **33.7%** · `Technology` 27.2% · `Feature` 26.8% · `Alternative` 26.2% · `Risk` 23.4% · `Decision` **19.3%** |
| חברים בלי קהילה ברמת fine | **0** (Entity/WorkItem/Document/Component) · `nodes_without_a_key` **0** |
| כתיבת ה-build | `communities_written` 1,297 · `communities_deleted` 0 · `memberships_written` 23,750 · `memberships_deleted` 0 · `reports_carried_over` 0 · `reports_dropped` 0 · `properties_set` **60,470** · `nodes_created` **0** · 2,264ms |
| **batches** | **94 batches / 2 shards** (47+47) · 186 קהילות · **4,650 שורות חברים** · **1,450 chunks ראייתיים** · 380ms |
| גודל batch | max **36,481B** · min 16,382B · mean 31,119B · total 2,925,175B · שורה ארוכה ביותר **769B** · `over_budget` **ריק** |
| פרמטרי אריזה | top-25 חברים לפי degree בפרויקציה · ≤8 ראיות · ≤600 תווים · חוזה `brain/community/schema.json` (sha256 `ad20e98a…`, 4,970B) · shards מאוזנים לפי תווי ראיות+חברים |
| דוגמה | `shard-01/003`: 2 קהילות (`L0-1352` size 39, `L0-1410` size 611) / 50 חברים / 15 ראיות / 31,601B → 2 דוחות, 8 findings כל אחד |
| **merge — batches** | `found` **94** · `valid` **94** · `invalid` **0** · `envelope_valid_rate` **1.0** · `reported_failed` 0 |
| **merge — דוחות** | `seen` **186** · `accepted` **186** · `rejected` **0** · `rejection_reasons` **ריק** · לפי רמה 106/80 · `community_nodes_missing` **0** |
| findings | **1,527** · **8.21 לדוח** · **0 בלי ראיה** · **1,718** הפניות `evidence_chunk_ids` (progress: 1,402 מזהים ייחודיים, כולם קיימים בגרף) |
| provenance | **0** קהילות מסוכמות בלי provenance · נדרש: `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at` · `model: opus:community-summarizer` |
| אינדקס | `community_embedding` · `VECTOR` · **ONLINE**, `population_percent` 100.0 · dim **1024** · cosine · `awaited_indexes` 4 ב-**4ms** |
| `IndexMeta` | `model: bge-m3` · `dim: 1024` · `similarity: cosine` · `label: Community` · **`live: 186`** · `state: ONLINE` · `updated_at 2026-09-07T18:04:57+00:00` |
| חסינות (merge חוזר) | `written` **0** · `unchanged` **186** · embedding `needed` 0 / `written` 0 (0ms, 0 בקשות, 0 טוקנים) · `properties_set` **7** · nodes/relationships **0** |
| דטרמיניזם | 3 ריצות build עם אותו seed = אותם `member_hash` (progress); `test_a_second_build_with_the_same_seed_gives_the_same_member_hashes` |
| **כפילויות בין רמות** | **22 סטים של חברים / 44 קהילות** · הגדול `f7b70ab9…` size **173** (`L0-280` = `L1-351`) · הקטן size **25** (`L0-845` = `L1-81`) · **7 מתוך 22** בעלי כותרת זהה מילה-במילה |
| census אחרי merge | level 0: 762 קהילות / 106 מסוכמות / 656 misc / **106 embedded** · level 1: 535 / 80 / 455 / **80** · `IN_COMMUNITY` 11,875 בכל רמה, 23,750 סה"כ |
| שלושת הדוחות המדורגים ביותר | `L0-581` rank **9.5** (357 חברים) "Next generation consumer rebalance protocol and its rollout" · `L1-367` 9.5 (354) "Share groups and the queue semantics ecosystem around them" · `L1-388` 9.5 (284) "Replacing ZooKeeper with a Raft-based metadata quorum" |
| הערות סוכנים | **12 batches** עם `notes` — קהילות לא-קוהרנטיות, עוגני-עמוד, וזיהוי עצמי של כפילות בין רמות |
| בדיקות | **84** unit (`tests/test_community_partition.py` 22 · `test_community_merge.py` 23 · `test_community_projection.py` 17 · `test_community_batches.py` 13 · `test_community_schema.py` 9) + **23** live (`tests/live/test_communities_live.py`) |

## מה הפתיע

- **22 קהילות coarse החזיקו בדיוק את חברי קהילת fine — 12% מתקציב הסוכנים הלך על אותו סט חברים פעמיים.** `cross_level_duplicates()` מזהה אותן לפי `member_hash` זהה: 44 קהילות, מ-173 חברים (`L0-280` / `L1-351`) עד 25 (`L0-845` / `L1-81`). מה שמדיד בפלט: **7 מתוך 22 הזוגות קיבלו כותרת זהה מילה-במילה** (למשל `L0-7` ו-`L1-389`, שתיהן "Connect error handling, dead letter queues and errant records"), ו-15 קיבלו ניסוח שונה לאותו תוכן ("ZooKeeper to KRaft migration and metadata transactions" מול "ZooKeeper to KRaft migration with dual writes and transactions"). הסוכנים עצמם דיווחו על זה תוך כדי עבודה — ב-`shard-02/036`, `040`, `043`, `044` יש `notes` כמו *"L1-86 carries the same member_hash as fine-level community L0-1031 summarised in batch shard-02/001; this is its coarse-level counterpart."* התיקון (`ae43551`) הוא `split_copyable()` + `copy_reports()`: הדוח השני **מועתק** עם `copied_from` שמצביע על הקהילה שעבורה נכתב, ושומר את ה-provenance של הריצה שכתבה אותו — *"Nothing here claims a second agent said the same thing twice."* הריצה המתועדת בדוח קדמה לכלל: `selection` שלה מראה `selected: 186` ואין בה `copied_from_another_level`.
- **710 קהילות בגודל 1 — וכולן `Document`.** ב-`sizes_all_levels` יש 710 קהילות בדלי `1-1`, 355 בכל רמה, ו-`placement` מראה שהן כולן מסמכים: `Document.in_singleton_community` = **355**, בעוד `Entity`, `WorkItem` ו-`Component` הם **0**. 52.7% מהמסמכים נחתו ב-misc — הכי גבוה מכל תווית, ופי שניים מ-`Entity` (25.2%). המסמך שאין לו chunk שמזכיר ישות משותפת, ואין לו `LINKS_TO`, הוא ממש רכיב קשיר בגודל 1.
- **הקהילה הגדולה ברמת fine מחזיקה 1,139 חברים — 9.6% מהגרף בקהילה אחת.** `L0-85`: 612 `Entity`, 508 `WorkItem`, 14 `Component`, 5 `Document`. היא **כן** מסוכמת (מעל min-size), ומקבלת בדיוק אותם 25 חברים ו-8 ראיות כמו קהילה של 25. הסוכן כתב את זה בעצמו ב-`notes` של `shard-02/024`: *"L0-85 has 1139 members and covers the whole client component; the top-25 members and eight evidence chunks describe only the new-consumer threading and metrics thread, so the report is scoped to that."* אותה תופעה ב-`L1-422` (422 חברים) לפי `shard-02/039`.
- **עמודי אינדקס ב-Confluence מייצרים קהילות.** `shard-02/005`: *"L0-1600 is held together mainly by the 'Dormant/inactive KIPs' Confluence page, which links many unrelated old KIPs (quotas, log retention, namespaces, partition assignment)."* וכן `shard-02/039` על "Kafka Streams KIP Overview" ו-"Discarded KIPs". עמוד ריכוז אחד עם עשרות `LINKS_TO` הוא hub שמושך קבוצה שאין לה תמה משותפת — והסוכנים סימנו את זה במקום להמציא תמה. סה"כ **12 מתוך 94 batches** החזירו `notes`, מהן ארבע אומרות במפורש שהקהילה אינה נושא אחד (`L1-92`, `L0-113`, `L0-746`, `L1-186`).
- **866 קשתות מקבילות והדדיות — 470 מהן `REFERENCES` דו-כיווניות.** בלי המיון לפי `elementId` הן היו נכנסות ל-GDS כשתי שורות ומכפילות את המשקל ש-Leiden רואה על הקשת. וב-`DELIVERS_KIP` היחס הוא 213 יחסים → **122 קשתות** (91 קרסו): כמה commits שסוגרים את אותו issue ומיישמים את אותו KIP הם קשת אחת, לא ארבע.
- **ה-merge המתועד לא כתב כלום — וזה הפלט התקין.** `written: 0`, `unchanged: 186`, `embedding.needed: 0`, `usage.requests: 0`, `properties_set: 7`. שבע התכונות הן של `IndexMeta` — הצומת היחיד שכן מתעדכן בכל ריצה, כי `updated_at` שלו הוא בהגדרה חדש. `report_is_current()` משווה את כל `COMPARED_PROPS` **חוץ מ-`reported_at`**, בדיוק כדי שריצה חוזרת לא תיראה כמו עבודה.
- **מודולריות 0.906 עם 5 רמות שמתכנסות אחרי השלישית.** ההפרש בין רמה 3 לרמה 4 הוא 0.000210 (0.905861 → 0.906071). הבריף הניח היררכיה של "אחרונה + אחת מעליה"; על הקורפוס הזה שתי הרמות האלה כמעט זהות, ולכן `--level-indices 1,4` ניתן ידנית. השומר בקוד (`DEGENERATE_LEVEL_RATIO`) **מדווח ולא מתקן**: *"which levels to summarise is a planner decision, not a silent correction."*

## מה היינו משנים

- **כלל ההעתקה לפני הריצה ולא אחריה.** `split_copyable()` נכתב ב-`ae43551`, אחרי ש-186 הדוחות כבר נכתבו; לו היה קיים ב-`batches`, הסוכנים היו כותבים 164 דוחות ולא 186 (22 = 11.8% מהתקציב). מה שהיה חסר כדי לדעת את זה מראש הוא בדיוק מה ש-`cross_level_duplicates()` עושה היום — שאילתה של `member_hash` כפול — אבל היא רצה ב-**merge**, אחרי התשלום, ולא ב-**batches**, לפניו.
- **הרמה הגסה כמעט לא הוסיפה מבנה על הקורפוס הזה.** 80 דוחות coarse, מהם 22 (27.5%) הם דוח fine תחת מזהה אחר. ההכרעה שנרשמה: להשאיר כמו שהוא, ו-S5 ב-Plan 2 יאחד לפי `member_hash` בזמן אחזור. האופציה שהקוד כבר מציע ולא נוסתה היא `--gamma` מתחת ל-1.0 (`level_warnings` מציע אותה בשם), או `--levels`/`--level-indices` אחרים — כלומר להזיז את הרמה הגסה במקום לאחד אותה בדיעבד.
- **מסמכים ראויים לענף פרויקציה משלהם.** 355 מסמכים בקהילת יחיד ו-733 ב-misc (52.7%) הם התווית הכי גרועה בפרויקציה, כי הקשתות שלהם הן `LINKS_TO` (297 שורות בלבד) ו-`MENTIONS_PARENT` דרך chunks שמזכירים ישות. הענף שהיה משנה את התמונה הוא Document↔Document דרך **ישות משותפת** — קשת שלא הייתה בסט של הבריף ולא נמדדה כאן.
- **תקציב הראיות אינו תלוי בגודל הקהילה.** 8 chunks ו-25 חברים הם המספר גם לקהילה של 25 וגם ל-`L0-85` עם 1,139 חברים ו-`L1-422` עם 422 — ובשני המקרים הסוכן כתב ב-`notes` שהדוח מכסה רק את החוט שיש לו ראיות. תקציב שגדל עם `size` (או פיצול של קהילת-ענק לפני הסיכום) הוא שינוי ב-`collect()` ולא בחוזה עם הסוכן.
- **`INTRODUCES_RISK` בחוץ — 402 קשתות שלא נמדד מה הן היו משנות.** הן נספרות ב-`edges_left_out_by_type`, אבל לא רצה ריצת השוואה (`--dry-run` עם ובלי) שתראה את ההפרש ב-modularity ובמספר הקהילות. `--dry-run` נבנה בדיוק בשביל מדידה כזאת: הוא מקרין, מריץ Leiden, מדווח ומשחרר בלי לגעת ב-Neo4j.
- **`min-size 25` נבחר לפי תקציב הסיכום, ומחירו לא נמדד בשאלות.** 1,111 קהילות ו-2,277 ישויות (25.2%) נמצאות מחוץ לכל קהילה מסוכמת, כולל 33.7% מ-`Problem`. זו מגבלה ידועה של Global search שנרשמה כהכרעה — ומה שחסר כדי לתמחר אותה הוא הערכת האחזור של Plan 3, לא מספר נוסף מהשלב הזה.

## מה זה מלמד (המתכנן)

**1. קהילות על גרף ידע ≠ clustering של embeddings.** Leiden רואה רק קשתות: מי מוזכר עם מי, מה מחליט על מה, מה מפנה למה. לכן "Diskless topics" ו-"Tiered storage" הן קהילות שכנות אבל נפרדות, למרות שהטקסט שלהן דומה מאוד. embedding היה מאחד אותן. הקהילה עונה על "מה קשור למה בפועל", לא "מה נשמע דומה" — וזה בדיוק מה ש-Global search צריך.

**2. הפרויקציה היא ההחלטה הכי חשובה, והיא לא בקוד של Leiden.** מה בפנים (Entity, WorkItem, Document, Component), מה בחוץ (Chunk, Person, Commit, סינתטי), ואיך קורסים קשתות מקבילות — 866 קשתות שקרסו לאחת הן ההבדל בין קהילה שמשקפת נושא לקהילה שמשקפת "מי כתב הכי הרבה תגובות". modularity 0.906 נראה מצוין; הוא לא מדד לאיכות הפרויקציה, רק לאיכות החלוקה *בתוכה*.

**3. "הרמה האחרונה ואחת מעליה" הייתה הנחה שלי, והמספרים הפריכו אותה.** רמות 3–4 היו זהות (535 = 535). הסוכן מדד, בחר 1+4, ודיווח. הלקח מהקורס על MS GraphRAG: ההיררכיה שם היא פרמטר של הקורפוס, לא של האלגוריתם — ובקורפוס של 12k צמתים יש בפועל שתי רמות משמעותיות, לא חמש.

**4. מה עולה סיכום היררכי — במספרים.** 186 דוחות × ~31KB קלט = 2.9MB קריאה לסוכנים, ~1.5M tokens, כשעה לשני סוכנים. ו-12% מזה (22 דוחות) היה כפול: coarse שהוא בדיוק fine. כלל ה-copy מתקן קדימה. בארגון אמיתי עם 100k צמתים זה המקום שבו התקציב נשרף — ולכן `member_hash` + ledger (re-summarize רק על שינוי) הם לא אופטימיזציה, הם התנאי לעדכון אינקרמנטלי.

**5. 0 findings בלי ראיה — כי הולידציה בקוד, לא בפרומפט.** 1,527 findings, 1,402 chunk ids מצוטטים, כולם קיימים וכולם *הוצעו* לאותה קהילה. הסוכן לא יכול להמציא ראיה כי המיזוג בודק שהמזהה היה בקלט שלו. זה אותו עיקרון מ-extract (quote מילולי) בגרסה של דוח: "למה המוח מאמין בסיכום הזה" = רשימת chunks שאפשר לפתוח.

**6. מה הקהילות לא מכסות — במפורש.** 11% מהצמתים ב-misc, ובמיוחד ~30% מ-Technology/Problem/Feature. שאלה גלובלית על טכנולוגיה שולית לא תמצא דוח. זה לא באג; זה גבול של Global search שצריך להיות ידוע ל-router: S5 לנושאים גדולים, S3 לכל השאר.

**מה זה קובע ל-Plan 2:** `global_search` מאחד לפי `member_hash` (22 הזוגות), מחזיר findings עם evidence ids כדי שהסוכן השואל יצטט chunks ולא "את הדוח"; ה-reduce נשאר אצל הסוכן — כי בגרסה אגנטית אין map-reduce קבוע, יש בחירת כלים.
