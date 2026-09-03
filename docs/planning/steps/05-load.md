# שלב 05 — `brain load` (Plan 1, Task 4)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-graph-engineer` · **מודול בקורס:** 2 ("Cypher מזורז" — מלכודות MERGE), 3 ("ומה עם דאטה מובנה?")

## מטרה
הגרף המובנה, בלי LLM, idempotent: ריצה שנייה = 0 צמתים ו-0 קשתות חדשות.

## קלטים
- `data/canonical/*.jsonl` (אחרי שלב 03 + שלב 04 הסינתטי — אם 04 טרם רץ, load חייב לעבוד גם על canonical ללא synthetic)
- `data/reports/canon.json` — link types, containers, אזהרות
- spec §2.4, Task 4 בתכנית, `brain/graph/client.py`, `data/fixtures/mini/`

## פלטים
- `brain/graph/schema.py` (constraints/indexes), `brain/graph/loaders/*.py`, פקודת `brain load [--schema-only] [--canonical-dir]`, `data/reports/load.json`

## הכרעות מתכנן
1. **מפתחות ייחודיים (constraints):** `WorkItem.key`, `Document.key`, `Person.id`, `Commit.sha`, `PullRequest.number`, `Component.name`, `Version.name`, `Sprint.name`, `File.path`, `Chunk.id`, `StatusChange.id` (= sha1(item_key|field|at|to)), `Community.id`, `Entity.id` (= kind|norm_name).
2. **Labels:** `WorkItem` + label משני לפי `type` מנורמל: `Bug|Improvement|Task|SubTask|Story|Epic|NewFeature|Wish|JiraTest` — **Jira `Test` (123 issues) ≠ Xray `Test`**: Jira → `JiraTest`; סינתטי (`source=xray`) → `Test|TestExecution|TestPlan|TestSet`. `Document{kind}`. `Change` → `Commit` או `PullRequest`.
3. **`LINKS_TO{type}` — אוצר מילים סגור** (mapping table בקוד, כל השאר → `relates` + `raw_type`). **הערכים האמיתיים ב-Jira של Kafka** (מ-`canon.json` → `link_types`): `reference`→`relates`, `duplicate`→`duplicates`, `blocker`→`blocks`, `problem/incident`→`causes`, `cloners`→`clones`, `supercedes`→`supersedes`, `issue split`→`splits`, `dependent`→`depends_on`, `required`→`depends_on`; סינתטי: `tests`, `executes`, `defect`, `related`→`relates`. `parent`→לא קשת אלא `PARENT_OF`. **קישורים הדדיים = קשת אחת** (כיוון לפי הסמנטיקה: `blocks` מ-blocker ל-blocked; `relates` — סדר לקסיקוגרפי).
4. **`REFERENCES{via: link|text, kinds[]}`** מ-`refs` (issue/kip/pr) — לא ליצור צומת יעד שלא קיים; refs ליעד לא קיים נספרים בדוח (`dangling_refs`). refs מסוג `user` → `MENTIONS_PERSON` רק אם ה-Person קיים.
5. **`RESOLVES`** Commit→WorkItem לכל ref issue בהודעת commit; **`IMPLEMENTS_KIP`** Commit→Document לכל ref kip. `PullRequest`→Commit: `HAS_COMMIT` **מ-`Change.pr`** (ה-PR שה-commit *הוא*), לא מ-refs.
6. **`StatusChange`** צומת לכל רשומת changelog בשדות (שמות תצוגה של Jira!) `status`/`assignee`/`resolution`/`Fix Version`/`Component`/`priority`; **להתעלם** מ-`RemoteIssueLink` (17.7k רשומות רעש) ומ-`Link`; **`assignee` לפי `to_id`/`from_id`** (מפתחות זהות), לא לפי מחרוזת תצוגה; `HAS_CHANGE` מ-WorkItem. **`ASSIGNED_TO{valid_from, valid_to}`** נגזר מרצף שינויי assignee (ה-assignee הנוכחי = `valid_to: null`); אם אין changelog של assignee — `valid_from = created`.
7. **`Person`** לכל זהות (`id = source:key`, `resolved=false`, `display`, `source`); `REPORTED_BY`, `AUTHORED` (Commit/PR/Document/comment-authors כ-`COMMENTED{at}`).
8. **`ancestors`** של Document → `CHILD_OF` רק אם היעד קיים; אחרת נספר. **`kip_of`** (וריאנט של KIP) → `VARIANT_OF`. עמודים עם label `ambiguous-kip` הם KIPs אמיתיים שהודחו — נטענים כרגיל.
9. **`resolution`/`resolved_at`** נשמרים כ-properties על WorkItem.
10. כל Cypher עם projection מפורש. `UNWIND $rows MERGE` ב-batches 1,000 דרך `GraphClient.write_batched`. אין `CREATE`.

## קריטריוני קבלה
- [ ] `make smoke` כולל טעינת `data/fixtures/mini/` ובדיקת ספירות + מסלול `Test-TESTS->WorkItem<-RESOLVES-Commit` + `StatusChange` של KAFKA-101 (3 מעברים) + `ASSIGNED_TO.valid_from` של KAFKA-100.
- [ ] טעינה מלאה: ספירות תואמות canonical (WorkItems, Documents, Persons, Commits, PRs, Components, Versions); `LINKS_TO` = מספר זוגות ייחודיים (לא ×2); `REFERENCES{via:text}` ≥ `via_text` מהדוח של canon (פחות dangling).
- [ ] **ריצה שנייה: 0 nodes_created, 0 relationships_created** (מהמונים של הדרייבר).
- [ ] `data/reports/load.json`: צמתים/קשתות לפי label/type, `dangling_refs` לפי kind, orphans לפי label, `unknown_link_types`, משך.
- [ ] `make check` ירוק; בדיקות יחידה על: mapping של link types, dedupe הדדי, נגזרת `ASSIGNED_TO`, `StatusChange.id` דטרמיניסטי.

## מה תלמד בשלב הזה
כמה מהגרף הארגוני נבנה בלי LLM (הרוב), למה MERGE-על-מפתח הוא הכלי המרכזי ל-idempotency, ואיך זמן נכנס לגרף כצמתי-אירוע במקום כ-property שנדרס.

## הערות למבצע
- הסוכן מתכנן וכותב. אין LLM, אין embeddings בשלב זה (זה 06).
- `GraphClient.read()` מחזיר `record.data()` — labels נעלמים; לעולם לא `RETURN n`.
- אם canonical מכיל רשומות `synthetic=true` — לטעון אותן כרגיל עם property `synthetic:true` על הצומת.
