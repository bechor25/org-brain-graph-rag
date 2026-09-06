# שיעור 04 — `brain load`: הגרף המובנה, MERGE-על-מפתח, וזמן כצמתים

**תאריך:** 2026-09-03 · **תכנית:** Plan 1 (שלב 05) · **מודול בקורס:** 2 ("Cypher מזורז — כל מה שהקורס צריך" — מלכודות MERGE), 3 ("ומה עם דאטה מובנה?"), 8 ("Graphiti: גרף ידע ביטמפורלי")

## מה עשינו

שלב 05 לוקח את חמשת קבצי ה-JSONL הקנוניים של שלב 03 וכותב מהם גרף Neo4j שלם — **33,025 צמתים ו-97,325 קשתות** — בלי שורת LLM אחת ובלי embeddings.
הסדר הוא כל התכנון: קודם `brain/graph/schema.py` (15 constraints + 8 range indexes, ואז `db.awaitIndexes`), אחר כך כל הצמתים, ורק בסוף כל הקשתות — כי אף שאילתת קשת לא רשאית ליצור את הקצה השני שלה (`brain/graph/cypher.py`), ולכן קשת שנכתבה לפני שהיעד קיים הייתה נבלעת בשקט במקום להיספר.
שישה loaders (`brain/graph/loaders/{persons,containers,documents,workitems,changes,refs}.py`) יושבים על שלוש צורות כתיבה בלבד — `MERGE` צומת על מפתחו, `MATCH`+`MERGE` קשת בין שני צמתים קיימים, וקשת שנושאת property-מפתח — כולן `UNWIND $rows … MERGE` ב-batches של 1,000 דרך `GraphContext.write_rows`, ואף אחת מהן אינה `CREATE`.
כל אוצר המילים (label משני לפי type, מיפוי טיפוסי link, זהות `StatusChange`, גזירת מרווחי `ASSIGNED_TO`) רוכז ב-`brain/graph/mapping.py` — פונקציות טהורות בלי דרייבר, כדי שההכרעות ייבדקו בלי מסד נתונים.
`brain load [--schema-only] [--canonical-dir]` כותב `data/reports/load.json` שקורא את המפקד **חזרה מהגרף** ולא סופר את השורות ששלחנו, מוסיף `inputs` עם sha256 לכל קובץ קנוני שנקרא, ומריץ 16 קריטריוני קבלה — **16/16 ירוקים**.
הריצה החוזרת: `nodes_created: 0`, `relationships_created: 0`, ב-3.27 שניות.

## למה ככה (הקישור לקורס)

**"ומה עם דאטה מובנה?" (מודול 3) — הסעיף שמצדיק את קיומו של השלב.** הקורס: *"שימו לב שכל מה שראינו חילץ גרף מטקסט חופשי — אבל בארגון אמיתי חלק גדול מהגרף כבר קיים כדאטה מובנה … הדרך הנכונה היא לטעון אותו ישירות (למשל `LOAD CSV` של Neo4j או כתיבת MERGE מתוך ETL) ולתת ל-LLM לחלץ רק את מה שבאמת לא מובנה. גרף היברידי — ליבה מובנית ואמינה + העשרה מחילוץ — עדיף כמעט תמיד על גרף שחולץ כולו ב-LLM."*

`brain load` הוא ה-ETL הזה, וה"ליבה המובנית" שלו מדידה: 33,025 צמתים ו-97,325 קשתות לפני שנקראה שורת LLM אחת. בתוך הליבה הזאת עצמה הגבול בין "מוצהר" ל"מטקסט" נשמר על הקשת: `REFERENCES{via}` (`brain/graph/loaders/refs.py`) מפריד **5,903 קשתות `via: "text"` מול 594 `via: "link"`** — כלומר גם בליבה המובנית, 91% מהעקיבות קיימת רק כי מישהו הקליד מפתח במשפט. `merge_via()` ב-`brain/graph/mapping.py` קובע ש-`link` מנצח `text` כששני האזכורים מצביעים לאותו יעד, כי ההצהרה הפורמלית היא העדות החזקה.

**"Cypher מזורז" (מודול 2) — מלכודת ה-MERGE.** הקורס מזהיר: *"`MERGE` על תבנית של נתיב שלם יוצר את כל הנתיב מחדש אם חלק כלשהו ממנו לא קיים — כולל צמתים כפולים. לכן: תמיד MERGE כל צומת בנפרד למשתנה, ורק אז MERGE את הקשר בין המשתנים הקשורים."* זה בדיוק החוזה של `brain/graph/cypher.py`: `edge_merge()` פולט `MATCH (a…) MATCH (b…) MERGE (a)-[r]->(b)` — שני `MATCH` ואז `MERGE` על הקשר בלבד. התוצאה היא לא רק היעדר כפילויות אלא **מדידה**: ref ל-`KAFKA-9999` (issue אמיתי, מחוץ לפרוסת 2023–2025) לא מייצר צומת ריק אלא נספר תחת `dangling_refs` — 8,217 refs מסוג issue, 1,438 user, 476 pr ו-7 kip.

הקורס מוסיף בתרגיל 2.1: *"הריצו פעמיים את סקריפט הבנייה וודאו שלא נוצרו כפילויות"*. הבדיקה הזאת היא קריטריון קבלה של השלב (`second_run_creates_nothing`) והיא גם בדיקת live (`tests/live/test_load_live.py::test_second_run_creates_nothing`). מה שהופך אותה לזולה הוא ה-constraints: ה-docstring של `brain/graph/schema.py` מנסח את זה — *"`MERGE` on a property with no unique constraint is both slow (a full label scan per row) and unsafe (two concurrent transactions can create two nodes with the same key)"*. שלושה מפתחות (`Chunk.id`, `Community.id`, `Entity.id`) נוצרים כאן למרות שאף loader לא כותב אותם, כדי ששלבים 06–08 יירשו סכמה במקום להמציא אחת.

הקורס גם מראה `ON CREATE SET` בדוגמת ה-MERGE הראשונה — וזה הפרט שהתברר כקריטי כאן. `node_merge(..., on_create=True)` קיים בשביל שדה אחד: `Person.resolved`. הקנוני תמיד אומר `False`, ושלב 08 (`brain resolve`) יהפוך אותו ל-`True` — ריצה חוזרת של `load` שהייתה כותבת `resolved` מחדש הייתה **מבטלת בשקט את החלטות השלב שאחריה**.

**"Graphiti: גרף ידע ביטמפורלי" (מודול 8) — למה זמן הוא צומת ולא property.** הקורס: *"דריסת העובדה הישנה מוחקת היסטוריה שלפעמים היא בדיוק מה שנשאל עליו"*, ואצל Graphiti *"כשעובדה חדשה סותרת קיימת, הקשת הישנה לא נמחקת אלא מסומנת כפגת-תוקף (ה-`invalid_at` שלה נקבע). כך 'מה נכון עכשיו' ו'מה היה נכון במרץ' הן שתיהן שאילתות לגיטימיות."*

אצלנו אותו רעיון מיושם בשתי צורות, שתיהן דטרמיניסטיות ובלי LLM:

- **`StatusChange` — אירוע כצומת.** כל שורת changelog ששרדה את הסינון הופכת לצומת עם זהות משלה (`brain/graph/loaders/workitems.py`, `statuschange_id()` ב-`mapping.py`): 7,607 צמתים ו-7,607 קשתות `HAS_CHANGE`. "מה היה הסטטוס ב-2024-03-01" הוא טראברסל, לא property שהעריכה הבאה דרסה.
- **`ASSIGNED_TO{valid_from, valid_to}` — עובדה עם חלון תוקף.** זה ציר ה-T של Graphiti (`valid_at`/`invalid_at`) בלבד; ציר ה-T' (מתי המערכת למדה) לא קיים כאן, וה-invalidation אינה שיפוט LLM אלא גזירה מרצף שינויי ה-assignee ב-`assignment_history()`. 1,564 מרווחים, מהם 864 שה-identity שלהם הגיעה מ-`WorkItem.assignee` ולא מה-changelog.

ההבדל מ-Graphiti הוא בדיוק ההבדל שהקורס מלמד: שם העובדות מחולצות מטקסט ומבוטלות-תוקף ע"י LLM; כאן הן כתובות במקור ורק צריך לא לאבד אותן. אותו מודל, מחיר שונה בסדרי גודל.

## מספרים

| מדד | ערך |
|---|---|
| commits בשלב (`b596ea8..47abc94`, נוגעים ב-`brain/graph` או ב-`tests`) | 8 — `cc2ad87`, `048bb3a`, `9b5cc26`, `766ebe6`, `fba0ae9`, `9675296`, `2dad49b`, `47abc94` |
| **צמתים / קשתות** | **33,025 / 97,325** |
| סכמה | 15 constraints + 8 range indexes (`constraints_added: 0`, `indexes_added: 0` בריצה חוזרת) |
| צמתים לפי label | WorkItem 1,416 · Document 1,391 · Person 1,767 · Commit 6,107 · PullRequest 6,026 · File 8,594 · StatusChange 7,607 · Component 30 · Version 86 · Space 1 |
| labels משניים ל-WorkItem | Bug 556 · Improvement 314 · SubTask 250 · **JiraTest 123** · Task 119 · NewFeature 45 · Wish 9 |
| labels/קשתות שממתינים לשכבה הסינתטית | Sprint 0 · Area 0 · TESTS 0 · EXECUTED_IN 0 · IN_PLAN 0 · HAS_RUN 0 |
| קשתות מובילות | TOUCHES 48,032 · AUTHORED 13,524 · HAS_CHANGE 7,607 · REFERENCES 6,497 · HAS_COMMIT 6,020 · COMMENTED 3,610 · IN_COMPONENT 2,316 |
| והשאר | REPORTED_BY 1,416 · ASSIGNED_TO 1,564 · IN_SPACE 1,391 · MENTIONS_PERSON 1,285 · FIX_VERSION 1,005 · RESOLVES 1,009 · AFFECTS_VERSION 911 · IMPLEMENTS_KIP 693 · LINKS_TO 298 · PARENT_OF 114 · VARIANT_OF 24 · CHILD_OF 9 |
| `REFERENCES` לפי `via` | **text 5,903 · link 594** |
| `AUTHORED` לפי יעד | Commit 6,107 · PullRequest 6,026 · Document 1,391 |
| `LINKS_TO` לפי type | relates 191 · duplicates 34 · blocks 26 · causes 22 · depends_on 16 · clones 6 · supersedes 2 · splits 1 |
| מסלול הקישורים | 884 הצהרות → 276 dangling → 608 קשתות לפני dedupe → **298 קשתות על 297 זוגות לא-מכוונים** |
| טיפוסי link לא מוכרים (→ `relates` + `raw_type`) | 177 הצהרות ב-9 שמות: `completes` 60 · `testing` 38 · `incorporates` 32 · `child-issue` 17 · `container` 11 · `blocked` 9 · `dependency` 8 · `parent feature` 1 · `regression` 1 |
| **changelog** — נשמר / הוסר | **7,607 / 18,932** (`RemoteIssueLink` 17,718 + `Link` 1,214) |
| נשמר לפי שדה | status 2,330 · Fix Version 1,745 · assignee 1,205 · resolution 1,150 · Component 884 · priority 293 |
| נזרק (לא ברשימה, נספר) | description 1,163 · labels 846 · Version 361 · summary 345 · issuetype 274 · Parent 210 … |
| `StatusChange.id` — התנגשויות | **0** עם `from` בנוסחה; 26 שורות → 10 ids בלעדיו |
| `ASSIGNED_TO` | 1,564 מרווחים · 864 מהשדה · 0 באורך אפס · 0 עם Person לא ידוע |
| `alias_candidates` ל-`brain resolve` | **131 זוגות זהויות על 486 issues** — הגדול: `jira:JIRAUSER302322` ↔ `jira:lucasbru` (69 issues) |
| refs תלויים באוויר | issue 8,217 · user 1,438 · pr 476 · kip 7 |
| dangling נוספים | `RESOLVES` 3,235 · `parent` 136 · `IMPLEMENTS_KIP` 1 · `HAS_COMMIT` 0 |
| `ancestors` של Confluence | 2,795 סה"כ — **2,786 מחוץ לקורפוס** → 9 קשתות `CHILD_OF` |
| אזכורים חוצי-מקור (`@name` בדף שנפתר לאדם מ-Jira) | 11 |
| יתומים | Person 119 |
| `invariants` | `links_to_same_type_both_directions: 0` · `extra_edges: {}` |
| checks בדוח | **16/16 `ok: true`** |
| טעינה מלאה ראשונה | ~5.0s |
| ריצה חוזרת (הדוח הנוכחי) | 3.27s — schema 0.03 · read_canonical 0.17 · nodes 1.46 · edges 1.49 · census 0.11 |
| מוני הדרייבר בריצה החוזרת | `nodes_created: 0` · `relationships_created: 0` · **`properties_set: 262,146`** |
| שיא זיכרון (RSS) | ~410MB (כל הקורפוס בזיכרון, כמו ב-`canon`) |
| `inputs` (sha256 + records) | `workitems.jsonl` 1,416 · `documents.jsonl` 1,391 · `persons.jsonl` 1,767 · `changes.jsonl` 12,133 · `containers.jsonl` 117 |
| `synthetic_provenance` | `not_available` — ה-ledger `data/canonical/synthetic_merged.json` טרם נכתב |
| `skipped_container_kinds` | `{}` — `testplan`/`testset` מוגדרים לדילוג, ואין עדיין רשומות כאלה |
| פונקציות בדיקה | 401 → 500 יחידה, ועוד **16 בדיקות live** על `data/fixtures/mini/` |

## מה הפתיע

- **נוסחת המפתח של ה-brief התנגשה בקורפוס האמיתי.** ה-brief קבע `StatusChange.id = sha1(item_key|field|at|to)`. במדידה, הנוסחה הזאת מכווצת **26 שורות changelog ל-10 ids**: הסרה של כמה Fix Versions או Components בעריכה אחת מייצרת שורות שחולקות מפתח, שדה, חותמת זמן **ו-`to` ריק**, ונבדלות רק ב-`from` — למשל KAFKA-16423 שמסירה את 3.6.2, 3.8.0 ו-3.7.1 באותה מילישנייה (`2024-03-26T12:11:59.791Z`). הוספת `from` הפכה את כל 7,607 השורות לייחודיות; הסטייה מה-brief מתועדת ב-`notes` של הדוח ובבדיקה `id_collisions: 0`, ואושרה ע"י המתכנן.
- **Jira כותב את אותו אדם בשני איותים באותו issue.** ה-`note` בדוח מנסח את המקרה: `kirktrue` בשדה ה-assignee ו-`JIRAUSER298607` ב-changelog. זה לא מקרה קצה — **131 זוגות זהויות על 486 issues**, כשהגדול (`JIRAUSER302322` ↔ `lucasbru`) מכסה 69 issues לבדו. השלב לא ממזג אותם (מיזוג הוא שלב 08 ונמדד מול gold pairs), רק רושם אותם ב-`alias_candidates` עם דוגמאות.
- **486 מרווחי assignment באורך אפס — מכלל "לסגור ולפתוח מחדש".** הגרסה הראשונה של `assignment_history()` סגרה את המרווח של הזהות מה-changelog ופתחה מרווח חדש לזהות מהשדה. אבל זו אותה כהונה בשני איותים, ולכן נוצרה קשת שמצהירה על **מסירה שמעולם לא קרתה**, באורך אפס. הכלל הנוכחי *מחליף* את המרווח במקום לסגור אותו, והדוח מראה `assignments_zero_length_dropped: 0`; אותם 486 issues הם בדיוק אלה שיצאו כ-`alias_candidates`.
- **`extra_edges` תפס את הקשתות הישנות של השלב עצמו.** הבדיקה `no_edges_beyond_what_canonical_asks_for` משווה את מפקד הקשתות בגרף לשורות שהריצה הזאת כתבה. בריצה ראשונה היא נדלקה — ומה שהיא מצאה לא היה באג בקנוני אלא **קשתות שריצה קודמת של `load` עצמו השאירה מאחור אחרי שהמיפוי השתנה**. `brain load` לא גוזם אף פעם, ולכן ה"בדיקה על עצמך" היא הדבר היחיד שהופך שארית כזאת לגלויה.
- **ה-mini fixture של Plan 0 הצפין את הניסוח, לא את הטיפוס.** ה-fixture כתב `blocks` — התיאור החיצוני ש-Jira *מציג* — בעוד `canon` פולט את שם הטיפוס `blocker`. כלומר הבדיקה שרצה על ה-mini בדקה ערך שהקורפוס האמיתי לא מייצר. ה-fixture תוקן ל-`blocker`, ו-`LINK_TYPE_MAP` מכיר בכוונה גם את `blocks` — כי השכבה הסינתטית מנסחת יחסים כמו שהגרף מנסח אותם, לא כמו ש-Jira שם את טיפוס הקישור.
- **חוזה ה-ledger — הצרכן המציא צורה, והבדיקה שלו אישרה את ההמצאה.** `brain/graph/provenance.py` נכתב מול צורת ledger משוערת, והבדיקה שלו נבנתה על אותה השערה — כך ששניהם היו ירוקים ולא נגעו בקובץ ש-`brain synth merge` באמת כותב (מפתחות = **id קנוני** כמו `xray:XT-10007`, ולא `key`; `merged_at` ולא `extracted_at`). ה-fixture בבדיקות היום מריץ את `synth merge` האמיתי וקורא את הקובץ שהוא השאיר; ה-docstring אומר את זה במפורש: *"A hand-rolled shape would have kept passing while the real ledger moved underneath it — which is exactly what happened once."* ledger שקיים אך אינו בצורה הנכונה מפיל את הפקודה (`ProvenanceError`, exit 1) במקום לטעון שכבה שנכתבה ע"י LLM בלי provenance.
- **`TestPlan`/`TestSet` הם גם container וגם work item.** השכבה הסינתטית מצהירה על תוכנית בדיקות פעמיים: כ-work item (`XP-12`) וכ-container (`xray:testplan:<name>`). טעינת שניהם הייתה שמה **שני צמתים שונים מאחורי `MATCH (p:TestPlan)`**. ההכרעה: ה-work item הוא הצומת (יש לו את המפתח, הקישורים וההיררכיה), והקונטיינר נספר תחת `skipped_container_kinds` ולא נטען בשקט.

## מה היינו משנים

- **`MERGE`-על-מפתח ≠ idempotency של *מצב*.** הריצה החוזרת מדווחת `nodes_created: 0` ו-`relationships_created: 0` — ובאותה נשימה **`properties_set: 262,146`**. כלומר הגרף אינו גדל, אבל כל property נכתב מחדש בכל ריצה. כל עוד הקנוני הוא מקור האמת ל-property הזה זה בסדר; ברגע ששלב מאוחר יותר הוא הבעלים (`Person.resolved` של שלב 08, ובהמשך embeddings וקהילות) — כתיבה מחדש היא מחיקה שקטה. הפתרון שיושם נקודתית (`ON CREATE SET` ב-`node_merge`) צריך להיות **הכלל** לכל שדה שבבעלות downstream, לא חריג ל-`resolved`.
- **`load` לא גוזם — וזה מתועד, לא מתוקן.** אם רשומה קנונית נעלמה, הקשת שלה נשארת בגרף עד שמישהו ימחק אותה. ההכרעה הייתה לא לממש מחיקה בשלב הזה אלא **להפוך אותה לנראית**: `extra_edges` מדווח כל טיפוס קשת שבו הגרף מחזיק יותר ממה שהריצה כתבה, וה-`note` בבדיקה אומר את זה במפורש. בקורפוס שרק גדל זה נכון; ברגע שיהיה מחיקה אמיתית במקור, צריך שלב גיזום עם אותה רמת ספירה.
- **טקסט התגובות לא נכנס לגרף.** `COMMENTED{at}` מחבר Person ל-WorkItem (3,610 קשתות), אבל גוף התגובה נשאר רק ב-`data/canonical/workitems.jsonl`. ההחלטה מודעת — שלב 06 (`brain chunk`) קורא מהקנוני ולא מהגרף — אבל היא אומרת ש**שאילתת Cypher לבדה לא יכולה לקרוא תגובה**, ומי שיצפה למצוא אותה על צומת יגלה זאת רק בשלב 07.
- **קשת ה-`Space` נוספה מאוחר.** `Space` נטען כ-container מהיום הראשון ונשאר **צומת יתום** עד ש-`IN_SPACE` (Document→Space) נוספה בהכרעת מתכנן תוך כדי הסקירה — 1,391 קשתות שנולדו מהערה בדוח היתומים ולא מהתכנון. שווה לבדוק יתומים לפי label לפני שקובעים שהסכמה שלמה: `orphans_by_label` הוא עדיין 119 Persons.

## מה זה מלמד (המתכנן)

**1. רוב הגרף הארגוני נבנה בלי LLM — וזה המספר שצריך לזכור.** 33k צמתים / 97k קשתות (ואחרי הסינתטי: 35.4k / 109.6k) נטענו ב-4 שניות מדאטה מובנה בלבד. כל העקיבות (RESOLVES, REFERENCES, TESTS, HAS_COMMIT), כל האנשים, כל הזמן (StatusChange, ASSIGNED_TO) — דטרמיניסטי. ה-LLM (שלב 07) יוסיף *משמעות* על גבי זה: החלטות, סיבות, סיכונים. בארגון אמיתי זה אומר: לפני שקונים "AI לגרף", בודקים כמה מהגרף כבר יושב במטא-דאטה.

**2. "idempotent" הן שתי תכונות, לא אחת.** יצירה idempotent: ריצה שנייה = 0 צמתים, 0 קשתות. מצב idempotent: ריצה שנייה לא דורסת מה ששלב אחר כתב (`Person.resolved`). הראשון הגיע חינם מ-MERGE-על-מפתח; השני דרש `ON CREATE SET` מפורש — ותפסנו אותו רק כי הסוקר שאל "מה קורה ל-`resolved` אחרי resolve ואז load?". תבנית לכל פייפליין: לכל property, מי הבעלים.

**3. זהות של אירוע היא כל השורה, לא התוצאה.** `sha1(key|field|at|to)` נראה שלם עד ש-Jira הראה שלוש הסרות של Fix Version באותה מילישנייה עם `to=null`. 26 שורות → 10 מזהים, בשקט. הסוכן **מדד** במקום לסמוך על ה-brief שלי — וזה בדיוק מה שהמוסכמות דורשות. לקח: כל מזהה דטרמיניסטי צריך מונה התנגשויות בדוח, תמיד.

**4. `extra_edges` תפס את הבאג של עצמו בריצה הראשונה.** 486 מרווחי ASSIGNED_TO באורך אפס נשארו בגרף אחרי שהכלל השתנה — כי load מוסיף ולא מוחק. בדיקה שמשווה "מה בגרף" ל"מה ה-canonical מבקש" היא הדרך היחידה לראות שאריות. וה-486 האלה הפכו ל-131 `alias_candidates` (למשל `JIRAUSER302322` ↔ `lucasbru`, 69 פריטים) — דלק ל-resolve.

**5. שני סטים סגורים בשני קבצים, שנכתבו ע"י שני סוכנים במקביל, נסחפים.** `synth/schema.json` הבטיח `blocks/duplicates/testplan/testset`; `graph/mapping.py` לא ידע מהם. ה-ledger של provenance — גרוע יותר: הצרכן המציא צורה, כתב בדיקה שמאשרת את ההמצאה, והפלט האמיתי של היצרן קרס עליה. הפתרון היחיד ששורד: contract test שטוען את **הפלט האמיתי של היצרן**. זה חוזר בכל צומת בין שני שלבים.

**6. הרעש הסינתטי עבד כמו שרצינו.** אחרי הטעינה: 660 TESTS, 609 HAS_RUN, 78 IN_PLAN, 2,385 צמתים עם `batch_id`/`model`/`extracted_at`. עכשיו יש בגרף אנשים בכמה זהויות, קישורים רק בטקסט, וסטטוסים מיושנים — עם אמת ידועה. זה מה ש-resolve והערכה יימדדו מולו.

**מה זה קובע ל-resolve (08):** 131 alias candidates + 420 זהויות סינתטיות ל-308 אנשים = ground truth; `Person.resolved` שייך ל-resolve, לא ל-load; מיזוג חייב לשמור `merged_from[]` כדי ש-load חוזר לא יחזיר את הכפילויות.
