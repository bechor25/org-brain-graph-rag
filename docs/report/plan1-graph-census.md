# מפקד הגרף — שער Plan 1

נוצר אוטומטית ע"י `brain index` ב-2026-09-17T09:53:19+00:00 מתוך `data/reports/index.json`. **אין כאן מספר שהוקלד ביד** — כל טבלה נגזרת מה-JSON, כך שריצה חוזרת של השלב מעדכנת את המסמך במקום להשאיר אותו מיושן.

**מצב השער:** לא עבר — 11/13 קריטריונים.

## שער היציאה של Plan 1

מקור הקריטריונים: `docs/superpowers/plans/2026-09-03-plan1-corpus-to-graph.md, Task 9 'Plan 1 exit gate'`. עברו 11 מתוך 13. טבלת ה-roadmap מחזיקה טיוטה מוקדמת יותר של שני ספים (issues: 1,500, kips: 80), שנכתבה לפני שה-probe מדד את הקורפוס; שניהם מדווחים ולא נבחר אחד בשקט.

| קריטריון | דרישה | ערך שנמדד | תוצאה |
|---|---|---|---|
| issues | >= 1400 real Jira issues | 1,426 | PASS |
| kips_embedded | >= 190 KIP documents with embedded chunks | 271 | PASS |
| kips_referenced_all_embedded | every referenced KIP that exists in the corpus is embedded | 271/271 | PASS |
| commits | >= 4000 commits | 6,107 | PASS |
| person_resolution_precision | >= 0.85 | 0.9801 | PASS |
| person_resolution_recall | >= 0.85 | 0.7789 | FAIL |
| entity_resolution_precision | >= 0.85 | 1.0 | PASS |
| entity_resolution_recall | >= 0.85 | 0.98 | PASS |
| llm_edges_without_provenance | == 0 | 0 | PASS |
| all_indexes_online | every managed index ONLINE | 12/12 ONLINE | PASS |
| make_smoke_green | `make smoke` exits 0 on this commit | STALE (PASS on 602f5bf) | STALE |
| lessons_01_09 | docs/lessons/01..09 present | 9/9 | PASS |
| progress_complete | every Plan 1 row in progress.md is done | 11/11 rows done | PASS |

### הערות לקריטריונים

- **issues** (PASS): the roadmap summary table asks for >= 1500, a pre-probe draft; the probe measured the whole 2023-2025 three-component slice at 1,416, so Task 9 lowered it to 1,400. Against the table's number this would be FAIL.
- **kips_embedded** (PASS): Phase A embeds the KIPs the harvested slice actually references, not all 1334 harvested pages. The roadmap summary table's draft asks for >= 80; both are met.
- **kips_referenced_all_embedded** (PASS): the 'all referenced' half of the Task 9 criterion.
- **person_resolution_recall** (FAIL): known and accepted by the planner (progress.md, 'הכרעות מתכנן — resolve'): the road to 0.85 is a wider adjudication band (~3,133 pairs, ~78 more batches), not a better judge. Reported as FAIL because the criterion is the criterion.
- **llm_edges_without_provenance** (PASS): counted over 14796 LLM-derived edges plus the adjudicated SAME_AS links, read back from the graph.
- **make_smoke_green** (STALE): the recorded result is from 2026-09-17T06:40:51+00:00, on commit 602f5bf; HEAD is now 81a2010. It says nothing about this tree — re-run `brain index --smoke`.

## הקורפוס

| מדד | ערך |
|---|---|
| issues אמיתיים (Jira) | 1,426 |
| work items סינתטיים (Xray/ADO) | 1,849 |
| מסמכים | 1,391 |
| מהם KIP | 1,334 |
| KIPs שמוזכרים ע"י issue/commit | 271 |
| KIPs מוטמעים | 271 |
| KIPs מוזכרים שמוטמעים | 271 |
| KIPs שמצוטטים ע"י כל דבר (כולל KIP→KIP) | 774 |
| commits | 6,107 |
| commits עם מפתח (chunked) | 4,150 |

> הגדרת "מוזכר": named by a WorkItem or a Commit — the scope `brain chunk` embedded (brain/chunk/scope.py). kips_cited_by_anything additionally counts KIP-to-KIP citations, which Phase A does not chunk on.

## צמתים

סה"כ **58,560** צמתים (כל צומת נספר פעם אחת). עמודת `בלי דגל` = צמתים שאין עליהם `synthetic` בכלל — לא אותו דבר כמו `synthetic=false`.

| label | מקור | צמתים | אמיתי | סינתטי | בלי דגל |
|---|---|---|---|---|---|
| WorkItem | מבני | 3,275 | 1,426 | 1,849 | 0 |
| Document | מבני | 1,391 | 1,391 | 0 | 0 |
| Person | מבני | 1,217 | 1,118 | 99 | 0 |
| Commit | מבני | 6,107 | 6,107 | 0 | 0 |
| PullRequest | מבני | 6,026 | 6,026 | 0 | 0 |
| File | מבני | 8,594 | 0 | 0 | 8,594 |
| StatusChange | מבני | 7,666 | 7,666 | 0 | 0 |
| Component | מבני | 30 | 30 | 0 | 0 |
| Version | מבני | 86 | 86 | 0 | 0 |
| Sprint | מבני | 92 | 0 | 92 | 0 |
| Area | מבני | 24 | 0 | 24 | 0 |
| Space | מבני | 1 | 1 | 0 | 0 |
| Chunk | נגזר | 13,917 | 11,978 | 1,939 | 0 |
| Entity | נגזר | 9,064 | 9,064 | 0 | 0 |
| Community | נגזר | 1,066 | — | — | — |
| IndexMeta | נגזר | 4 | — | — | — |

### תוויות משנה של WorkItem

תוויות שנייה על אותם צמתים; חיבור שלהן ל-`WorkItem` סופר פעמיים.

| label | צמתים |
|---|---|
| Test | 817 |
| Bug | 779 |
| Improvement | 316 |
| Task | 303 |
| SubTask | 250 |
| Story | 232 |
| Epic | 143 |
| JiraTest | 124 |
| Feature | 121 |
| TestExecution | 78 |
| NewFeature | 46 |
| TestSet | 44 |
| TestPlan | 13 |
| Wish | 9 |

## חריגות סכמה

הכלל: spec §2.4 declares a closed node-label set; conventions rule 4 makes it binding.

**7** תוויות מחוץ למה ש-§2.4 הכריז, מהן 24 צמתים בתוויות-צומת חדשות. זה לא נספר כאן כטור רגיל: או שהתווית צריכה להיכנס ל-spec, או שהנתון לא אמור להיות בגרף — ורק אדם יכול להכריע.

A sub-label is a second label on a node that is already a WorkItem, so no node escapes the closed set — only the type vocabulary is wider than the spec wrote down. A new *node* label is the heavier finding.

טיפוסי ה-WorkItem ש-§2.4 מונה: `Bug` · `Epic` · `Issue` · `Story` · `Task` · `Test` · `TestExecution` · `TestPlan` · `TestRun` · `TestSet`.

| תווית | סוג | צמתים | הערה |
|---|---|---|---|
| Improvement | WorkItem sub-label | 316 | a Jira/ADO issue type §2.4 does not enumerate |
| SubTask | WorkItem sub-label | 250 | a Jira/ADO issue type §2.4 does not enumerate |
| JiraTest | WorkItem sub-label | 124 | a Jira/ADO issue type §2.4 does not enumerate |
| Feature | WorkItem sub-label | 121 | a Jira/ADO issue type §2.4 does not enumerate |
| NewFeature | WorkItem sub-label | 46 | a Jira/ADO issue type §2.4 does not enumerate |
| Area | node label | 24 | loaded as a container but not declared in spec §2.4's structured node list — the synthetic ADO layer emits area paths. |
| Wish | WorkItem sub-label | 9 | a Jira/ADO issue type §2.4 does not enumerate |

## קשתות

סה"כ **162,330** קשתות: 147,534 דטרמיניסטיות (קונקטורים, mentions ב-regex, GDS) ו-14,796 מ-LLM.

### קשתות מ-LLM

| סוג | קשתות |
|---|---|
| MENTIONS | 9,404 |
| DECIDES | 2,699 |
| MOTIVATED_BY | 1,338 |
| REJECTS | 641 |
| INTRODUCES_RISK | 404 |
| DEPENDS_ON | 252 |
| IMPLEMENTS | 58 |

### קשתות דטרמיניסטיות

| סוג | קשתות |
|---|---|
| TOUCHES | 48,032 |
| IN_COMMUNITY | 23,822 |
| HAS_CHUNK | 13,917 |
| AUTHORED | 13,524 |
| REFERENCES | 8,020 |
| HAS_CHANGE | 7,666 |
| HAS_COMMIT | 6,020 |
| IN_COMPONENT | 4,613 |
| COMMENTED | 3,716 |
| ASSIGNED_TO | 2,887 |
| REPORTED_BY | 2,830 |
| FIX_VERSION | 2,230 |
| PARENT_OF | 1,419 |
| IN_SPACE | 1,391 |
| AFFECTS_VERSION | 1,323 |
| MENTIONS_PERSON | 1,291 |
| LINKS_TO | 1,131 |
| RESOLVES | 1,009 |
| IMPLEMENTS_KIP | 693 |
| TESTS | 660 |
| EXECUTED_IN | 613 |
| HAS_RUN | 609 |
| IN_PLAN | 78 |
| VARIANT_OF | 24 |
| CHILD_OF | 9 |
| SAME_AS | 7 |

## Provenance

הכלל: conventions rule 3: every LLM-derived node and edge carries ['evidence_chunk_ids', 'batch_id', 'model', 'extracted_at'] with a non-empty evidence list.

### צמתים

| label | צמתים | מהם מ-LLM | עם provenance | חסרים | % | מתי נחשב LLM |
|---|---|---|---|---|---|---|
| Entity | 9,064 | 9,064 | 9,064 | 0 | 100.0% | every node of this label |
| Community | 1,066 | 124 | 124 | 0 | 100.0% | n.summary IS NOT NULL |

### קשתות

| סוג | קשתות | עם provenance | חסרים | % |
|---|---|---|---|---|
| MENTIONS | 9,404 | 9,404 | 0 | 100.0% |
| DECIDES | 2,699 | 2,699 | 0 | 100.0% |
| MOTIVATED_BY | 1,338 | 1,338 | 0 | 100.0% |
| REJECTS | 641 | 641 | 0 | 100.0% |
| DEPENDS_ON | 252 | 252 | 0 | 100.0% |
| IMPLEMENTS | 58 | 58 | 0 | 100.0% |
| INTRODUCES_RISK | 404 | 404 | 0 | 100.0% |

### SAME_AS

tiers 1-2 are deterministic (name rules, cosine) and carry rule/score instead of provenance; they are surviving links, not merges — a merged pair leaves one node and no edge.

| tier | כלל | קשתות | עם provenance |
|---|---|---|---|
| 1 | kip_title_alias | 7 | 0 |

## איחוד ישויות (resolution)

מקור: `data/reports/resolve.json` (נוצר 2026-09-17T09:48:08+00:00); יעד 0.85.

| סוג | צמתים לפני | צמתים אחרי | זהויות | זהויות/צומת | שיעור כפילות | זוגות זהב | P | R | F1 |
|---|---|---|---|---|---|---|---|---|---|
| person | 2,187 | 1,217 | 2,191 | 1.8 | — | 633 | 0.9801 | 0.7789 | 0.868 |
| entity | 9,237 | 9,064 | 9,274 | 1.023 | — | 100 | 1.0 | 0.98 | 0.9899 |

### מיזוגים לפי tier

| סוג | מיזוגים |
|---|---|
| person | 1: 284, 2: 408, 3: 282 |
| entity | 2: 4, 3: 195 |

> Precision and recall are computed over the labelled gold pairs only. Merges between identities the gold says nothing about (the real Jira / git / Confluence duplicates) are counted as `ungraded_merges` and left out of both, because calling them right or wrong would be a guess.

## קהילות

**1,066** קהילות, מהן 124 מסוכמות (11.63%). 11,911 חברים ייחודיים דרך 23,822 קשתות `IN_COMMUNITY`; 47.5% מהחברים נמצאים בקהילה מסוכמת.

| רמה | קהילות | גודל p50 | גודל p95 | מינ׳ | מקס׳ | חברים | מסוכמות |
|---|---|---|---|---|---|---|---|
| 0 | 533 | 1 | 100 | 1 | 1,262 | 11,911 | 62 |
| 1 | 533 | 1 | 100 | 1 | 1,216 | 11,911 | 62 |

## Chunks

**13,917** chunks בסך הכל: 12,986 חיים ו-931 יתומים (טקסט שהוחלף — נשמר כי `brain extract` מצטט אותו). 13,917 מוטמעים (100.0%), מהם 12,986 חיים (100.0% מהחיים).

### לפי סוג (חיים)

| סוג | chunks |
|---|---|
| message | 4,150 |
| comment | 3,662 |
| description | 3,433 |
| section | 1,741 |

### לפי צומת-אב (חיים)

| אב | chunks |
|---|---|
| WorkItem | 7,095 |
| Commit | 4,150 |
| Document | 1,741 |

## אינדקסים

`brain index` מנהל 12 אינדקסים; 12 מהם ONLINE. בריצה הזו נוצרו 0 חדשים (12 כבר היו). עמודת `מנוהל` מבדילה בין מה שהשלב הזה יוצר לבין אינדקסים של שלבים אחרים.

| שם | סוג | מצב | populationPercent | בעלים | מנוהל | labels | properties |
|---|---|---|---|---|---|---|---|
| chunk_embedding | VECTOR | ONLINE | 100.0 | chunk | כן | Chunk | embedding |
| entity_embedding | VECTOR | ONLINE | 100.0 | resolve | כן | Entity | embedding |
| community_embedding | VECTOR | ONLINE | 100.0 | communities | כן | Community | embedding |
| person_embedding | VECTOR | ONLINE | 100.0 | resolve | כן | Person | embedding |
| workitem_text | FULLTEXT | ONLINE | 100.0 | index | כן | WorkItem | title, description |
| document_text | FULLTEXT | ONLINE | 100.0 | index | כן | Document | title, body_md |
| entity_text | FULLTEXT | ONLINE | 100.0 | index | כן | Entity | name, description |
| person_text | FULLTEXT | ONLINE | 100.0 | index | כן | Person | display, aliases |
| chunk_text | FULLTEXT | ONLINE | 100.0 | index | כן | Chunk | text |
| brain_statuschange_at_idx | RANGE | ONLINE | 100.0 | load | כן | StatusChange | at |
| brain_workitem_created_idx | RANGE | ONLINE | 100.0 | load | כן | WorkItem | created |
| brain_commit_at_idx | RANGE | ONLINE | 100.0 | load | כן | Commit | at |
| brain_area_name_key | RANGE | ONLINE | 100.0 | other | לא | Area | name |
| brain_chunk_id_key | RANGE | ONLINE | 100.0 | other | לא | Chunk | id |
| brain_chunk_kind_idx | RANGE | ONLINE | 100.0 | other | לא | Chunk | kind |
| brain_chunk_parent_key_idx | RANGE | ONLINE | 100.0 | other | לא | Chunk | parent_key |
| brain_commit_sha_key | RANGE | ONLINE | 100.0 | other | לא | Commit | sha |
| brain_community_id_key | RANGE | ONLINE | 100.0 | other | לא | Community | id |
| brain_community_level_idx | RANGE | ONLINE | 100.0 | other | לא | Community | level |
| brain_community_member_hash_idx | RANGE | ONLINE | 100.0 | other | לא | Community | member_hash |
| brain_component_name_key | RANGE | ONLINE | 100.0 | other | לא | Component | name |
| brain_document_key_key | RANGE | ONLINE | 100.0 | other | לא | Document | key |
| brain_document_kind_idx | RANGE | ONLINE | 100.0 | other | לא | Document | kind |
| brain_entity_id_key | RANGE | ONLINE | 100.0 | other | לא | Entity | id |
| brain_entity_kind_idx | RANGE | ONLINE | 100.0 | other | לא | Entity | kind |
| brain_entity_norm_name_idx | RANGE | ONLINE | 100.0 | other | לא | Entity | norm_name |
| brain_file_path_key | RANGE | ONLINE | 100.0 | other | לא | File | path |
| brain_indexmeta_name_key | RANGE | ONLINE | 100.0 | other | לא | IndexMeta | name |
| brain_person_id_key | RANGE | ONLINE | 100.0 | other | לא | Person | id |
| brain_person_resolved_idx | RANGE | ONLINE | 100.0 | other | לא | Person | resolved |
| brain_pullrequest_number_key | RANGE | ONLINE | 100.0 | other | לא | PullRequest | number |
| brain_space_name_key | RANGE | ONLINE | 100.0 | other | לא | Space | name |
| brain_sprint_name_key | RANGE | ONLINE | 100.0 | other | לא | Sprint | name |
| brain_statuschange_field_idx | RANGE | ONLINE | 100.0 | other | לא | StatusChange | field |
| brain_statuschange_id_key | RANGE | ONLINE | 100.0 | other | לא | StatusChange | id |
| brain_version_name_key | RANGE | ONLINE | 100.0 | other | לא | Version | name |
| brain_workitem_key_key | RANGE | ONLINE | 100.0 | other | לא | WorkItem | key |
| brain_workitem_source_idx | RANGE | ONLINE | 100.0 | other | לא | WorkItem | source |
| brain_workitem_status_idx | RANGE | ONLINE | 100.0 | other | לא | WorkItem | status |
| index_1b9dcc97 | LOOKUP | ONLINE | 100.0 | other | לא | — | — |
| index_460996c0 | LOOKUP | ONLINE | 100.0 | other | לא | — | — |

### IndexMeta

Decision 5: an IndexMeta row advertises the LIVE count — what the index can actually return. `brain index` writes it, so for chunk_embedding `stored_live` now excludes the orphaned chunks (superseded text whose vectors stay indexed because `brain extract` cites them as evidence); `stored_total` keeps those visible. `live_vectors`/`total_vectors` are this step's own count of the same two things, so a drift between them and the stored pair is a bug the census can see.

| אינדקס | label | מודל | מימד | וקטורים חיים | סה"כ וקטורים | יתומים | שמור: live | שמור: total |
|---|---|---|---|---|---|---|---|---|
| chunk_embedding | Chunk | bge-m3 | 1,024 | 12,986 | 13,917 | 931 | 12,986 | 13,917 |
| entity_embedding | Entity | bge-m3 | 1,024 | 9,038 | 9,038 | 0 | 9,038 | 9,038 |
| community_embedding | Community | bge-m3 | 1,024 | 124 | 124 | 0 | 124 | 124 |
| person_embedding | Person | bge-m3 | 1,024 | 1,214 | 1,214 | 0 | 1,214 | 1,214 |

## יתומים והפניות מתות

**176** צמתים בלי אף קשת (0.3% מ-58,556). IndexMeta is excluded: it is bookkeeping and has no edges by design.

| label | יתומים | מתוך | % |
|---|---|---|---|
| Person | 60 | 1,217 | 4.93 |
| Sprint | 92 | 92 | 100.0 |
| Area | 24 | 24 | 100.0 |

### הפניות מחוץ לפרוסה (dangling)

מקור: `data/reports/load.json`. 74% of issue refs point before the 2023-01-01 slice boundary (canon review). Loading them would mean minting empty WorkItems, so they are counted, not made. בנוסף: 284 מתוך 3,000 קישורים פורמליים הצביעו מחוץ לפרוסה.

| סוג | הפניות מתות | מתוך סה"כ הפניות |
|---|---|---|
| issue | 8,225 | 12,244 |
| user | 1,438 | 2,732 |
| pr | 476 | 1,164 |
| kip | 7 | 3,320 |

## טביעת אצבע של הקורפוס הקנוני

מ-`data/canonical`. המפקד עונה על "מה יש בגרף"; הטבלה הזו עונה על "ממה" — בלעדיה שתי ריצות עם אותם מספרים יכולות היו לקרוא קבצים שונים. ה-sha256 מקוצר ל-16 תווים לקריאוּת; הערך המלא נמצא ב-`canonical.files` שב-JSON.

| קובץ | sha256 (16 ראשונים) | בייטים | רשומות |
|---|---|---|---|
| changes.jsonl | f6ed9e7d91392ca8 | 11,000,717 | 12,133 |
| containers.jsonl | e770477371896bf5 | 41,700 | 290 |
| documents.jsonl | 6cc6f6bab15ec0d1 | 16,037,914 | 1,391 |
| persons.jsonl | 0ec22ed55d02a25a | 422,320 | 2,191 |
| synthetic_merged.json | f33a56052f1d2501 | 326,948 | — |
| synthetic_truth.json | 703e69e6d2db2e9c | 89,958 | — |
| workitems.jsonl | 4e6f220b14c82378 | 19,285,754 | 3,275 |

## גרסאות

| רכיב | גרסה | פרטים |
|---|---|---|
| Neo4j | 2026.06.0 | community |
| GDS | 2026.06.0 |  |
| APOC | 2026.06.0 |  |
| Ollama | 0.32.6 | http://localhost:11434 |
| מודל הטמעה | bge-m3:latest | 566.70M · F16 · digest 790764642607 |

## `make smoke`

**PASS** — יצא עם קוד 0 אחרי 45.7 שניות, ב-2026-09-17T06:40:51+00:00.

## ספי שפיות

KIPs שעברו chunking: **271**, מהם **4** בלי אף ישות (1.48%). a KIP the extractor found nothing in is either a stub page or a gap in the extraction, and the census cannot tell which — so it is reported, not filtered.

## אזהרות

ספי שפיות מדווחים ולא נאכפים: מספר שנראה חשוד מופיע כאן, אף אחד לא מסונן בשקט.

| חומרה | נושא | פירוט |
|---|---|---|
| warn | synthetic_flag_missing | labels whose nodes carry no `synthetic` property at all: {'File': 8594}. `brain reset --synthetic` decides what to delete by that flag. |
| info | community_report_coverage | 124 of 1066 communities carry a report (11.63%), and they cover 47.5% of members (5658 of 11911). The unsummarised rest are the `misc` communities `brain communities` left below its size threshold — see data/reports/communities.json. |
| warn | kip_with_zero_entities | 4 of 271 chunked KIPs produced no Entity (1.48%). a KIP the extractor found nothing in is either a stub page or a gap in the extraction, and the census cannot tell which — so it is reported, not filtered. |
| info | every_sprint_is_an_orphan | all 92 Sprint nodes have no relationship — the loader creates the container but nothing points at it yet. |
| info | every_area_is_an_orphan | all 24 Area nodes have no relationship — the loader creates the container but nothing points at it yet. |

---

מה המפקד הזה *לא* יכול לענות: הוא סופר מבנה, לא איכות תשובות. האם קשת `DECIDES` נכונה, האם קהילה מספרת סיפור אמיתי, והאם אחזור מוצא את הראיה הנכונה — כל אלה נמדדים ב-Plan 3, לא כאן.
