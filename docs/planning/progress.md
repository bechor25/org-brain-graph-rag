# התקדמות — מוח ארגוני Graph RAG

מקור אמת יחיד ל"איפה אנחנו". מתעדכן ע"י המתכנן אחרי כל שלב.

**Plan 0 מוזג ל-`main` (2026-09-03). Plan 1 סגור (2026-09-17): שער 12/13 PASS, `make smoke` ירוק על DB שקט, 1,578 בדיקות; FAIL יחיד ומקובל: recall אנשים 0.779.** ריפו ציבורי: https://github.com/bechor25/org-brain-graph-rag · Plan 2 (אחזור + MCP): Tasks 1–3 ✅, Task 4 (שער, 19 שאלות במצב B) ממתין לסשן חדש של המשתמש. Plan 3: תכנית ב-`plans/2026-09-17-plan3-evaluation.md`.

| Plan | שלב | סטטוס | commit | הערות |
|---|---|---|---|---|
| 0 | 1 scaffold + CLI | ✅ | 4cfc272 | ruff 0.16 מפרמט code-blocks ב-Markdown → `extend-exclude=["docs"]` |
| 0 | 2 settings | ✅ | 14c92e8 | גרסאות שנפתרו: neo4j 6.3, typer 0.27, pydantic 2.13 — כולן תואמות |
| 0 | 3 compose neo4j | ✅ | f40c44c | `neo4j:2026.06.0` — APOC+GDS 2026.06.0 מובנים ב-image, healthy ב-11s |
| 0 | 4 GraphClient | ✅ | af24345 | read-mode guard אכוף בשרת: `Neo.ClientError.Statement.AccessMode` (driver 6.3, GQL status 08N03) |
| 0 | 5 embeddings | ✅ | 4dcb037 | Ollama 0.32 היה מותקן (לא brew) → `ollama serve` ידני; bge-m3 1.08GiB, pull 16s; 64 משפטים/0.94s |
| 0 | 6 doctor | ✅ | fab6d96, 0444366, f53793a | 7/7 OK; fix-ups מסקירות 1–4 (try/finally, loopback ports, cache_clear, docstring) |
| 0 | 7 canonical model | ✅ | 8b5d943 | 5 טיפוסים + regex mentions; עברית = `\w` ב-re → keys נמצאים גם בטקסט עברי |
| 0 | 8 mini fixtures | ✅ | 6eceb0b, 3fbc900 | 6 issues/1 KIP/3 persons/4 changes/4 containers; regex מוצא רק refs בטקסט — ה-PR חי ב-comment |
| 0 | 9 agents | ✅ | 35235e2 | 15 סוכנים: 5 הנדסה (כל הכלים), 7 סוכני-LLM (`Read, Write, Glob` בלבד), analyst (`Read`), reviewer, lesson-writer |
| 0 | 10 templates + README | ✅ | 8149f11 | README בעברית עם 5 עקרונות; `.env.example` תוקן |
| 1 | 01 probe | ✅ | — | 1,416 issues, 1,391 KIPs, 6,107 commits; search מחזיר changelog מלא; ADO לא נגיש; `"connect"` מילה שמורה |
| 1 | 02 harvest | ✅ | 79099a0…2c19ca2 | 1,416/1,391/6,107 ב-2m14s; ריצה שנייה 0; `--no-renames` הוריד git log מ-6+ דק' ל-0.44s; צפיפות links מלאה 36% (probe אמר 38%) |
| 1 | 03 canon | ✅ | 82db3e3…8d60a0c | 1,416/1,391(1,334 KIP+57 Page)/12,133/1,597/117 ב-6.9s; text-only refs 15% (any-mention 23.4%) — קריטריון תוקן; `[~user]` 1,972 אזכורים נוספים; resolution נוסף למודל |
| 1 | 04 synthetic | ✅ merge 27/27, 0 נדחו | d8b1a11…0f0eba2 | 1,849 items (817 tests, 897 ADO), 420 זהויות ל-308 אנשים, truth: 315 text-only/69 stale/255 renames; 17/19 ratios; **589/1,021 items כוסו** (באג קריאה בכלי — 42% לא נראו); הוחלט לקבל; supplement אופציונלי | 1,021 items נבחרו → 27 batches / 3 shards, 143 Epics מוקצים מראש; מרחבי מפתחות נפרדים לכל shard |
| 1 | 05 load | ✅ + סינתטי | cc2ad87…47abc94 | 35,410 צמתים / 109,623 קשתות; 2,385 סינתטיים עם provenance; TESTS 660 / HAS_RUN 609 / IN_PLAN 78; rerun 0/0 | 32,855 צמתים / 95,695 קשתות ב-3.6s; ריצה שנייה 0/0; LINKS_TO 298 זוגות; dangling issue refs 8,217 (גבול הפרוסה); StatusChange 7,607 (18.9k רעש הוסר); 725 מרווחי assignee בלי Person → canon ימנטס מ-changelog |
| 1 | 06 chunk+embed | ✅ (re-review; 651 בדיקות) | d1146be…636a83a | 12,915 chunks חיים (section 1,713 / desc 3,423 / comment 3,629 / commit 4,150) + 931 orphaned; embedding 416s+142s @ 6.5k tok/s; 3.49 chars/token; fences מוזחים 12→0; rerun 0/0; guard ברשת ב-conftest תפס בדיקות harvest שפנו לאינטרנט |
| 1 | 07 extract Phase A | ✅ (a1e6b55) merge: 195/195 valid, 42/14,930 רשומות נדחו (0.28%); 9,237 ישויות (Decision 3,837 / Problem 2,277 / Feature 1,118 / Tech 739 / Alt 670 / Risk 596), 5,492 יחסים, 9,390 MENTIONS, 0 בלי provenance; 81% Decisions weak (Feature נושא את הסיבות); 98.6% ישויות עם chunk ראייתי אחד ("restatement per chunk", לא איחוד) → resolve; **סקירה: accept**, מדגם 50: 46 נתמכים / 4 חלקיים / 0 לא (92%), kind נכון 45/50 | | 2,743 chunks (1,713 KIP sections + 1,030 descriptions) → 195 batches ≤40KB רב-שורתיים, ~24/סוכן, ~187k tokens קלט לסוכן; schema+examples אושרו ע"י המתכנן |
| 1 | 08 resolve | ✅ אחרי 08b (3bc6d32…25cb9f7): `username_stem` רץ → אנשים 1,229→**1,214**, **P=0.980 R=0.779 F1=0.868** (יעד 0.85 עדיין לא; 126 FN = הband החסום); 113 קשתות כפולות → **0** (111 מהן *לכיוון* השורד — dedupe חד-כיווני היה מפספס); provenance על כל שורד tier 3 (214 אנשים / 173 ישויות; מפתחות `resolution_*` נפרדים כדי לא לדרוס `opus:kg-extractor`); closure_overrides בדוח (unsure→merged 24 אנשים, 0 ישויות; different→0); `eval.entity.circular=true`; IndexMeta ×3; 7 באגים ב-e19179c שמעולם לא רץ (tier 2 קרס בכל ריצה). `db.awaitIndexes` = database-wide → poll לפי שם (593s→4s) **אנשים**: 2,187→1,229 (958 מיזוגים: 268/408/282 לפי tier; 50% מהזוגות רק דרך closure) — **P=0.979 R=0.744 F1=0.846** (תקרת הband 0.851; ל-0.85 דרוש band לא-חסום, +78 batches — נדחה כאופציה). ישויות: tier 1 = 7 קישורי KIP, tier 2 = 4 auto (gate מילים-זהות אחרי dry-run שהראה 244 מיזוגי-שרשרת שגויים); **ישויות ✅**: 9,237→9,038 (199 מיזוגים: 195 מהשופט, 4 auto, 7 קישורי KIP-title; 39 חוצי-מסמך; 2 boilerplate נדחו); eval ישויות מעגלי (gold = פסקי השופט) → spot-check 10 של המתכנן: 9/10 מסכימים; extract merge + load אחרי resolve = 0/0 | 79f75cc…21ac7e1 | 2,187→1,425 Persons (762 מיזוגים: 275 דטרמיניסטי, 487 embedding); P=0.847 R=0.263 לפני tier 3; תחום אפור 2,354 זוגות → מצמצמים ב-blocking + guard של ≥2 tokens בשם (P→0.955); embedding עם "מה עבד עליו" **הפך** את הסיגנל (true pairs 0.76→0.63) → display בלבד |
| 1 | 09 communities | ✅ (ae43551) merge: 94/94 batches תקינים, **186 דוחות** (106 fine / 80 coarse), 1,527 findings (8.2/דוח), **0 בלי ראיה**, 1,402 chunk ids מצוטטים — כולם קיימים; `community_embedding` ONLINE + IndexMeta; re-merge = 0/0; **22 זוגות fine=coarse עם אותו member_hash** (12% מתקציב הסוכנים) → מעכשיו batches מעתיק דוח קיים במקום לסכם שוב; top-3: rebalance protocol / share groups / KRaft quorum | 9ae7685…ce0138b | projection 11,875 צמתים / 19,291 קשתות (Chunk/Person/סינתטי בחוץ; 866 קשתות מקבילות/הדדיות קורסו לאחת); Leiden modularity 0.906, 5 רמות → נבחרו GDS 1 (fine, 762) + 4 (coarse, 535) כי רמות 3–4 זהות; `--min-size 25` → **186 לסיכום** (106 fine + 80 coarse), 1,111 misc; כיסוי 89% מהצמתים; דטרמיניזם: 3 ריצות = אותם member_hash, rerun 0/0; 94 batches ≤36.5KB; INTRODUCES_RISK (402) מחוץ לפרויקציה; 27% Technology / 34% Problem / 27% Feature ב-misc (fine) |
| 1 | 10 index + gate | ✅ (daf525b, 8de5646) | daf525b…8de5646 | 11 אינדקסים ONLINE (3 vector / 5 fulltext כולל `chunk_text` / 3 range), rerun 0; IndexMeta ×4 עם ספירה חיה; מפקד: 58,622 צמתים / 161,985 קשתות / 14,762 LLM עם **0 בלי provenance**; orphans 176; 12,915 chunks חיים; קהילות 186 מסוכמות (89% כיסוי); `docs/report/plan1-graph-census.md` נוצר מקוד; **שער 9/13**: FAIL כנה על recall אנשים 0.779; 3 FAIL של עבודה-בתהליך (smoke תחת עומס, שיעורים, שורות progress) — לריצה חוזרת אחרי סגירה |
| 1 | 11 modularity (registry, auth env, reset, guide) | ✅ אחרי 11b (0f23d83…f34198f): `brain chunk --stamp-synthetic` → Chunk.synthetic null 13,846→0 (1,939 true), Entity 0 (Phase A מדלג סינתטי); reset dry-run: 0 orphans; redaction על כל חריגה + `.git/config` אחרי clone (19 בדיקות); `sources.yaml` עם `id` ייחודי, `data/raw/<id>/`, `source_id` על רשומות (מושמט כש-id==type → קנוני byte-identical); מפתחות קנוניים בלי prefix (מגבלה מתועדת) | 48cf7f8, d8ce2c0, c265047 | `sources.yaml` + registry (canon byte-identical), auth דרך env (ריק = שגיאה), `brain reset` (--synthetic מסיר בדיוק 2,442 רשומות), מדריך + שלדי ADO/Xray; `yaml_mini` במקום pyyaml |

## Plan 2 — אחזור + MCP (התחיל 2026-09-07)

| Task | סטטוס | commit | הערות |
|---|---|---|---|
| 1 ספריית אחזור S1/S2/S3/S6 + `brain ask` | ✅ | f7262d8 | 21 מודולים; 18/18 שאלות עם ראיה תקפה; HE↔EN 4/4 זהות ב-S3 (S1 hybrid 0.48 — הגרף נושא את ה-cross-lingual, לא ה-embedding); p50 S1 139 / S2 176 / S3 125 / S6 26 ms; `SEARCH` לא נתמך ב-2026.06 CE → `queryNodes`; S3 דירג לפי degree בלבד → תיקון ב-0472aae |
| 2 Text2Cypher מוגן + reranker + בנק דוגמאות | ✅ | 602f5bf | guard: 42/42 נחסמו (deny-list 21 / allowlist 17 / static 4; `EXPLAIN CREATE` מתקבל ב-READ → שכבת plan-scan), 16/16 קריאות, timeout מוכח בשרת; בנק: author 19 → 15 validated → 13 accepted, 3 rationale נפלו על "Aggregation column contains implicit grouping" → batch 002 תיקן → **20 דוגמאות, 5/סוג, 20/20 רצות**; `sample_values` נוסף ל-`get_schema`; reranker bge-reranker-v2-m3: 19/19 top-1 השתנה (RRF ~0.002 מרווח), +1.6s p50, ברירת מחדל off; S4 מצב A על cq03/cq06 = similarity 1.0 (הבנק מכיל את השאלה) — Plan 3 מודד על שאלות חדשות; `merge` exit 1 כשיש rejects (קוסמטי — לתקן: exit 0 כשהבנק מלא) |
| 3 Global search S5 + שרת MCP | ✅ | 0472aae, 342ba80 | 15 כלים + 2 resources + prompt על stdio ו-HTTP; p50 lookup 9 ms / local 260 / global 148; truncation 46→17 פריטים (10.1k→3.9k tokens) עם כל kind; S5 מאחד 22 זוגות member_hash; cq12 → "New async consumer…", "OAuth/TLS client auth…", "Streams error handling…"; `.mcp.json` + `brain-mcp` ב-compose (profile `mcp`, לא הורם — build ארוך); 1,565 בדיקות |
| 4 שער: 19 שאלות במצב B (15 EN + 4 HE; ה-roadmap אמר 15 — הורחב ב-4 עבריות) | ⬜ brief `steps/12-plan2-gate.md` · **מוכן — ממתין למשתמש** | | דורש סשן חדש של המשתמש (`.mcp.json` נטען בעלייה) |

### פתוח ל-Plan 2 (מ-Task 3, S3 = קובץ של Task 1)
- `local_search` **לא מחזיר את מסמך העוגן עצמו** (KIP-848 מחוץ ל-top-10 של השאלה עליו): עוגן = 1.0, שכן עם 3 מסלולים = 1.43. הודעת ה-commit 0472aae טענה שזה תוקן — המדידה אומרת לא. הכרעה: לתקן בסבב הסקירה של Plan 2 (עוגן מוצמד ראשון).
- top-1 לא תלוי בשאלה: צמתים בלי `entity_embedding` (Document/WorkItem/Person/Component) מקבלים גורם ניטרלי 1.0 → KIP-932 ראשון בשתי השאלות ב-1.43 בדיוק. הכרעה: Document/WorkItem יקבלו גורם מ-fulltext score של השאלה על `document_text`/`workitem_text` (זול), ימדד ב-Plan 3.

### סקירת Plan 2 Tasks 1–3 (2026-09-17, brain-reviewer) — fix-required ×3, שער Task 4 **לא** מוכן
- 🔴 `run_cypher` מעתיק שורה שלמה ל-`props` בלי קיטום: `RETURN d.body_md` → פריט אחד, **66,289 tokens, `truncated=false`**. תקרת 4k לא נאכפת על השדה הרחב ביותר.
- 🔴 דוגמת ה-temporal בבנק מסננת `s.at <= datetime($date)` (חצות) בעוד `status_at` = סוף היום → על cq14 שני כלים של אותו שרת עונים "Reopened" מול "Resolved".
- 🟠 `seed-impact-01` סופר `run IS NULL` כ-"failing_tests"; 4/17 רשומות בבנק = העתקים של seeds (rationale = דוגמה ייחודית אחת); `INSERT` (GQL) עובר את ה-deny-list ונתפס רק בשכבת ה-plan; חלקי Task 1 ב-`retrieve.json` ללא sha/STALE ומכילים fallback ל-S4/S5 שכבר נבנו; קריטריון cross-lingual "S1/S3" צומצם ל-S3 בבדיקה; provenance "טקסט הצומת עצמו" (S6/impact) לא מסומן → cite-check ינפח.
- 🟡 allowlist לא מוחל על roots לא-מוכרים; `SHOW SETTINGS/TRANSACTIONS/PROCEDURES` עוברים; לוג כפול על refusal ב-MCP; `pack.truncated` לא מסמן חריגת budget בלי השמטה; `impact` לא דטרמיניסטי (slice של collect לא ממוין) ומסמן Test כ-WorkItem; `route` פותח driver; `inject_limit` שובר `UNION`/`FINISH`; `cq01/cq16` נבחרו ל-issue שאין לו `HAS_RUN` בכלל; `s5` חסר ב-help; `traceability-cq04` בלי סינון ADO; compose לא הורם; `explain()` לא תופס `Neo4jError`.
- **לקחי הסוקר:** (1) הגנה לעומק מוכחת רק כששכבה נכשלת — `INSERT` הוכיח את שכבת ה-EXPLAIN ואת המגבלה של טבלת 42; (2) תקרת הקשר שלא נאכפת על "כל השאר" איננה תקרה; (3) בנק few-shot הוא מימוש שני של אותה שאלה — צריך אותה שאלת זהב ואותה תשובה כמו הכלי שלידו; (4) שינוי שם של בדיקה הוא הדרך השקטה ביותר להחליש אותה.
- **הכרעה:** סבב תיקון לפני Task 4 (שני ה-blockers + כל ה-majors + minors זולים), בשני סוכנים עם גבולות קבצים.
- **סבב התיקון נסגר (38e1f4a…e5c1eb6, 2026-09-17):** S3 — עוגן מוצמד ראשון (9/9 שאלות עם מפתח; שני שאלות KIP-848 כבר לא זהות), גורם fulltext ל-Document/WorkItem; guard — 56/56 נחסמו (`INSERT` + allowlist על כל root + `SHOW` allowlist + backticks), `run_cypher` מקטם props (KIP-932: 66,289→389 tokens, `truncated=true`, `_clipped`), rows עם provenance `source_kind=row`; בנק — dedupe לפי Cypher+שאלה, seed temporal = סוף היום (cq14: S6 ובנק מסכימים "Reopened"), impact seed סופר רק FAIL אמיתי (cq06 = 0 — לשכבה הסינתטית אין ריצות לטסטים האלה), seed ADO עם `source`; 3 מעברים של cypher-author על rationale (הכלל: אין `ORDER BY` לפני `WITH` מצטבר) → **בנק 15 ייחודיות, 4/4/4/3, 15/15 רצות**; אריזה — `truncated` על חריגת budget, קיטום רקורסיבי (169k→1.2k tokens דרך MCP); provenance kinds quote 86 / node-text 73 / row 24; דוחות עם sha + `stale`; `write_report` ממזג; בדיקות `chunk_provenance` 16/18 (לא gated) + **`auditable` 18/18** (gated); cross-lingual: `s3_anchor_identical` **4/4**, top-5 Jaccard 0.917, S1 hybrid 0.524 > vector 0.500 → `partial`; עוגן cq01/cq16 → KAFKA-14649 (3 טסטים, 6 ריצות); impact דטרמיניסטי, Test כ-Row; MCP: לוג פעם אחת, `route` בלי driver, compose `brain-mcp` healthy ב-6s (teardown `rm -sf` — `down` הפיל את Neo4j ל-60s פעם אחת, נחסם בבדיקה). `make check` 1,680; `brain serve --check` 8/8; `brain competency` exit 0. **שער Task 4 מוכן.**

## Plan 3 — הערכה + דוח (התחיל 2026-09-17)

| Task | סטטוס | commit | הערות |
|---|---|---|---|
| 1 סט השאלות (כלים + forger ×2 + gold בקוד) | ✅ | 2fb8686, 546adff, 7f283b2 | **32/32**: 7/7/6/6/6 לפי סוג, 11 עברית, 0 נדחו; 13 מהforger (17 נכתבו, 4 עודף), 19 הכשירות עם gold שנגזר **בקוד** מהגרף (`gold_query` על כל שורה, 154 ראיות, כולן קיימות); "8 לכל סוג" בלתי אפשרי ב-32 → החלוקה הכי שווה; תיקוני builder לבנייה הבאה (members לפי degree, snippets מ-findings, grounds של REJECTS, spare לכל batch, `open_items_shown`); **חולשות ידועות ב-gold:** cq06 = "שום דבר לא נכשל" (אמת בקורפוס — כל אסטרטגיה "מנצחת" בריק), cq09/cq18 = 178 החלטות ו-4 נקובות
| 2 מצב A + שכבה 2 | ✅ (38cbbb9) — **המטריצה הראשונה** | 38cbbb9 | 32 שאלות × 7 אסטרטגיות = 224 ריצות, rerun = 224 unchanged; recall של gold_evidence: **S3 0.23** (precision 0.56, hit@10 0.81) · S6 0.56 על 8 שאלות שנקשרו → **אחרי תיקון (8a2474f, d5ebc44): 18/32 נקשרות** (KIP = היסטוריה נגזרת מ-commits מיישמים + issues מפנים, `props.via`), 14 n/a כנות (אין מפתח/תאריך/גרסה); סדר טוטאלי בכל שאילתת זמן · S5 0.19 על גלובלי · S4 0.15 (15 ms, 1.3k tokens) · S1 0.08 / S1+rerank 0.08 · S2 0.05; **reranker: hit@1 0.125→0.156 בלבד, ×13 latency (137→1,774 ms p50)**; S1/S2 שורפים 3.8k tokens ב-precision 0.09–0.11; cross-lingual: S3/S4/S6 אינווריאנטים לשפה (Jaccard 1.0 ב-3/4 זוגות), S1/S2 סוטים; S5 מחוץ לתבנית: recall ≤0.12 — כלל ה-n/a מוצדק במדידה; שני recall מפורסמים: `recall` (chunk שה-parent שלו הוא צומת gold נספר) ו-`recall_strict` — S1 0.077 מול 0.014, השאר זהים |
| 3 תשובות + שופט עיוור | 🟡 כלים ✅ (4da025c); עונים/שופטים טרם פוזרו | 4da025c | 184 מקרים (32×s1/s1r/s2/s3/s4 + 6 s5 + 18 s6) → 44 batches ≤40KB ב-4 shards (~4 מקרים/batch — ההקשר הארוז הוא המדידה, לא מקצצים); judge: 184 יחידים + 120 pairwise מול s1r, 20% חופפים בין 2 shards להסכמה, blind map מחוץ לעץ ה-batches עם בדיקה מבנית (לא grep); merge בודק `context_sha256` (drift), `cited ⊆ context`, ציטוט מילולי בכל הצדקה; סוכן חדש `answer-writer` (Read/Write/Glob) למצב A |
| 4 אינקרמנטלי | 🟡 הכנה רצה (probe, דגל slice, runbook; בלי כתיבה לגרף) · **ההרצה אחרי מצב B** (הגרף משתנה) | | |
| 5 דוח + שער | ⬜ | | |

**כלי שער Plan 2 (Task 4):** `brain eval cite-check` + `gate-report` ✅ (f917e05): regex לציטוטים בסוגריים בלבד (לא בקוד/לינקים), אימות מול הגרף (מפתחות מדויקים; sha/chunk לפי prefix; person לפי suffix רק אם יחיד), ספירה לפי ציטוטים ייחודיים, `planner_verdicts` נשמרים בין ריצות, דוח בעברית עם פסקת מתכנן משומרת. brain-analyst עודכן לפורמטים המדויקים.

## החלטת תהליך (2026-09-03, המשתמש)
- מ-Plan 1 והלאה: התכנית = brief + חוזים + קריטריונים. **הסוכנים מתכננים וכותבים את הקוד**; המתכנן סוקר ומכריע. (ב-Plan 0 הקוד היה בתכנית והסוכנים הקלידו.)

## שיעורים
- Plan 0: `docs/lessons/00-foundations.md`
- Plan 1 שלבים 01–02: `docs/lessons/01-harvest.md`
- Plan 1 שלב 03: `docs/lessons/02-canon.md`
- Plan 1 שלב 04: `docs/lessons/03-synthetic.md`
- Plan 1 שלב 05: `docs/lessons/04-load.md`
- Plan 1 שלב 06: `docs/lessons/05-chunk.md`
- Plan 1 שלב 07: `docs/lessons/06-extract.md`
- Plan 1 שלב 08: `docs/lessons/07-resolve.md`
- Plan 1 שלב 09: `docs/lessons/08-communities.md`
- Plan 1 שלב 10: `docs/lessons/09-index.md`
- Plan 1 שלב 11: `docs/lessons/10-modularity.md`
- Plan 2 Task 1: `docs/lessons/11-retrieval.md`
- Plan 2 Task 2: `docs/lessons/12-text2cypher.md`
- Plan 2 Task 3: `docs/lessons/13-mcp.md`

## ממצאים (findings)
- (2026-09-06 21:11) שינה של המק + מכסת session של Opus הפילו 4 סוכנים באמצע; status.json לכל shard אפשר להמשיך מהנקודה. **אין למנוע שינה (caffeinate) על דעת המתכנן** — המשתמש הכריע (2026-09-07): שינה = השהיה, ממשיכים מ-status.json.
- **(synth) כלי Read של הסוכנים חותך קובץ ~49KB בשורה אחת** — `.in.json` של 80–98KB בשורה אחת נקרא חלקית (הסוכן ראה 21–27 מ-40 פריטים, לא ראה `documents/persons`). חובה לכל batch builder עתידי (extract!): JSON מודפס-יפה רב-שורות (Read עם offset/limit עובד לפי שורות) ו/או batches ≤40KB. shard-02 פיצה מ-`persons.jsonl` ומיצג יחסים לפי 40.
- (synth) blocker בסקירה: ה-spec אמר "מונה גלובלי" בעוד ה-build חילק בלוקים לכל shard — תוקן ב-`98d98c1` והודעה נשלחה ל-3 המחוללים תוך כדי ריצה. לקח: כשסוטים מחוזה מחייב, תיקון החוזה הוא חלק מהסטייה.
- סוכני `.claude/agents/` נטענים רק בתחילת סשן — בסשן הזה dispatch נעשה דרך general-purpose עם ההגדרה מודבקת; מסשן חדש הם זמינים ישירות.
- `neo4j:2026.06.0` מכיל APOC ו-GDS מקומית (`/var/lib/neo4j/labs`, `/products`) — אין תלות ברשת בזמן עלייה.
- Neo4j Community: `RoutingControl.READ` נאכף בצד השרת — בסיס אמיתי ל-`run_cypher` מוגן.
- ruff ≥0.16 מפרמט Python fences בתוך Markdown — לכן docs מוחרג מהפורמטר (lint עדיין רץ על הכל).

## ל-Plan 1 (מסקירת 7–8)
- (canon) 74% מ-refs ל-issues מצביעים מחוץ לפרוסה (pre-2023) → load מקשר רק ליעדים קיימים, סופר dangling. Jira `Test` type (123) ≠ Xray Test. 9 KIPs אמיתיים חולקים מספר עם KIP אחר (`ambiguous`).
- (harvest) `KAFKA-1` ב-69 עמודי KIP; 21 מספרי KIP על 45 עמודים; `KIP-1001` = שני KIPs שונים; `KIP-929` body ריק; `raw_layout` = חוזה dedupe בין base ל-`since-*/`.
- regex issue תופס `UTF-8`, `SHA-256`, `COVID-19` → allowlist של project keys ידועים (מה-harvest) לפני טעינה לגרף.
- `Ref(kind=pr, key=N)` ללא repo → להחליט `owner/repo#N` לפני ingest של יותר מריפו אחד.
- `Link.direction` על שני הקצוות → loader חייב לאחד זוגות הדדיים לקשת אחת.
- `Person.id` לא מסמן אם עבר resolution → להוסיף שדה/סמן לפני שלב resolve.
- `write_jsonl` דורס → temp+rename כשהפלט הופך ל-artifact.

## מסקירה סופית של Plan 0
- `read_mode_guard` הוא `required=False` בכוונה — Plan 2 מוסיף guard שני (deny-list + EXPLAIN). לא לסמוך על doctor לבד.
- `make check` דורש `uv sync --extra dev`; אופציה עתידית: `[dependency-groups] dev`.

## הכרעות מתכנן מ-load
- `StatusChange.id` כולל `from` (אחרת 26→10 התנגשויות) — מאושר.
- `IN_PLAN`/`HAS_RUN{status}`: load יפרסר דטרמיניסטית את שורות `"XT-n: PASS|FAIL (…)"` ב-comments של TestExecution → `HAS_RUN{status,reason}`; `IN_PLAN` מ-`parent`=TestPlan או links `tests` מה-plan. מיושם אחרי המיזוג הסינתטי.
- `IN_SPACE` Document→Space (Space היה orphan).

## הכרעות מתכנן — resolve
- recall אנשים 0.744 < 0.85: מקבל ומתעד. הדרך ל-0.85 היא band רחב יותר (3,133 זוגות, ~78 batches נוספים) — לא שופט טוב יותר. אופציונלי אחרי Plan 3 אם שאלות-אנשים נפגעות.
- auto-merge ישויות = מילים זהות (מלבד articles); "token משותף" לבד יצר שרשראות (8 metrics → צומת אחד).
- Floor 0.80 + blocking (1,188 זוגות) — השורה היחידה שמשאירה תקרת recall ≥0.85; כל אופציה ≤600 זוגות מגבילה ל-≤0.78. דיוק ב-auto-tier שווה יותר מ-recall (ל-recall יש הזדמנות שנייה ב-tier 3, לדיוק אין).
- embedding ל-Person = display בלבד (לא + פריטים): הקשר של "מה עבד עליו" מודד עבודה משותפת, לא זהות משותפת.

## הכרעות מתכנן — סינתטי
- לקבל את השכבה כמות שהיא (589/1,021 items מכוסים): ההערכה צריכה gold, לא כיסוי מלא. supplement ל-432 הפריטים שלא נראו — אופציונלי אחרי Plan 2 אם ההערכה תראה פערים.
- 3 אוצרות-מילים ארגוניים (Team A / KFK2 / Kafka\…) נשארים — זה בדיוק "צוותים שקוראים לאותו דבר בשמות שונים"; לא נרשם ב-truth, לא ייבדק ב-resolution.

## ל-Plan 2 (מסקירת extract)
- Decision עם `weak=true` (81%) = עובדה מצוטטת, לא רשומת החלטה: לנתב דרך MENTIONS ("מה KIP-N אומר"), לא דרך מסלול "למה הוחלט".
- 35% ישויות ללא קשת סמנטית (Technology 83%) → קהילות: Technology דרך MENTIONS בלבד או מחוץ לפרויקציה.
- מדד חדש לכל שלב חילוץ: **consolidation rate** (mentions/entity, entities/chunk).

## ל-Plan 2 (מסקירת chunk)
- `db.index.vector.queryNodes` deprecated ב-Neo4j 2026.06 → `SEARCH`. `IndexMeta.chunk_count` סופר orphans (13,846) — להשתמש ב-live (12,915).

## הכרעות מתכנן — קהילות (2026-09-07)
- רמות Leiden 1 (fine, 762) + 4 (coarse, 535) במקום "אחרונה + אחת מעליה": רמות 3–4 זהות על הקורפוס הזה. `--min-size 25` → 186 לסיכום (בתקציב 100–200), כיסוי 89%.
- 22 דוחות coarse כפולים ל-fine נשארים כפי שהם; S5 (Plan 2) יאחד לפי `member_hash` בזמן אחזור. הרמה הגסה מרוויחה מעט על הקורפוס הזה — לשקול מחדש אחרי הערכה.
- INTRODUCES_RISK (402) מחוץ לפרויקציה ב-v1; Technology/Problem/Feature ~30% ב-misc — מגבלה ידועה של Global search.

## סקירת סגירת Plan 1 (2026-09-07, brain-reviewer)
- אומת live: 0 קשתות LLM בלי provenance (14,762), 0 קשתות מקבילות זהות בכל הגרף, 186/186 קהילות עם provenance ו-0 findings בלי ראיה, `Chunk.synthetic` null = 0, כל ה-kinds/types בסט הסגור, אין כתיבה ל-DB מחוץ ל-`GraphClient`.
- **08b resolve: accept** (follow-ups: `tier1.last_applied` נעלם אחרי rerun; scope של dedupe בדוח; residue של live tests ב-DB).
- **09 communities: fix-required → ✅ (a26869f)** — `carried_rows` לפי `member_hash` בלבד → rebuild היה דורס 22 דוחות coarse (22/22 batch_id שגוי, 15/22 כותרת שגויה). תוקן למפתח `(member_hash, level)`; rebuild חי: 186/186 דוחות זהים שדה-שדה; `extracted_at` מה-ledger (touch על 94 קבצים = 0 restamp); באג-אח: id ממוחזר שמר summary ישן → REMOVE לפני carry.
- **10 index: fix-required → ✅ (2656e91)** — smoke נושא sha; בלי sha או sha אחר = STALE (נספר FAIL); `IndexMeta.chunk_count` 13,846→12,915 (+`total`); `person_embedding` managed (12/12); סעיף "סטיות סכמה": `Area` (24, מהשכבה הסינתטית) + 6 תת-labels של WorkItem. **שער 12/13 (2026-09-17, ריצה שקטה ע"י המתכנן)**: smoke PASS; FAIL כנה יחיד — recall 0.779.
- **11b modularity: fix-required (דוח בלבד) → ✅ (נכנס ב-2656e91 — הסוכן של index שלב קבצים שהיו staged של סוכן אחר; הפרה קלה של כלל ה-git המקבילי)** — `python -m brain.modularity --rerun`: canon רץ מחדש מה-raw האמיתי (7.5s) → 5 sha1 זהים ל-baseline; `synthetic_stamp` (null 0, derived==stored 1,939, rerun 0/0 + מנגנון ב-namespace `_ModCheck`), `reset_dry_run` (0 orphans), `redaction` (token לא בבתים/אובייקט/stdout; basic base64 מוסתר), `same_type_sources`; golden test אמיתי על `tests/fixtures/canon/`.
- **לקחים של הסוקר (נכנסים למוסכמות):** (1) מדידה שנגררת קדימה היא שקר עם חותמת זמן — לגרור רק עם sha/run id או לסרב; (2) "idempotent" שהוכח ע"י rerun שקרא 0 — ה-rerun מחק את הרשומה של הריצה שעבדה (פעמיים, בשני דוחות) → ראיית idempotency נכתבת למקום שה-rerun לא בבעלותו; (3) digest של קובץ committed אינו golden test אם שום קוד לא מייצר אותו.

## ממצא תהליך (2026-09-07)
- 5 סוכנים במקביל על Neo4j+Ollama אחד: live suites מתנגשות (fixtures נכשלים), `db.awaitIndexes` database-wide חוסם על אינדקסים של namespace אחר. כלל: `make smoke` סופי רץ פעם אחת, בשקט, ע"י המתכנן. תקלת API (20:40–20:50) עצרה את כולם — ההמשך מ-git + status.json עבד.

## החלטות פתוחות
- Ollama רץ כתהליך ידני (`ollama serve`), לא כ-service — README יסביר; LaunchAgent אופציונלי בהמשך.
