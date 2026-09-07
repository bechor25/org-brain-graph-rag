# שיעור 06 — `brain extract` Phase A: חילוץ מונחה-סכמה, שמונה סוכנים, ומיזוג דטרמיניסטי

**תאריך:** 2026-09-07 · **תכנית:** Plan 1 (שלב 07) · **מודול בקורס:** 3 ("סכמה קודם: העיקרון החשוב ביותר במודול", "דרך 1: LangChain — LLMGraphTransformer", "דרך 2: neo4j-graphrag — SimpleKGPipeline")

## מה עשינו

שלב 07 הוא השלב הראשון בפייפליין שבו LLM כותב תוכן חדש לגרף — ולכן גם השלב הראשון שבו כל שורה שנכתבת חייבת לשאת ראיה. `brain extract build` בחר מהגרף 2,743 chunks בעלי הערך הגבוה ביותר (1,713 sections של 268 דפי KIP + 1,030 תיאורי issues), עטף כל אחד בהקשר (`parent_key`, `parent_kind`, `parent_title`, `position`, `kip_keys_referenced[]`) וארז אותם ל-**195 batches על 8 shards**, כל אחד JSON מודפס-יפה ≤40,000 בייט — הלקח משלב 04, שם קורא הקבצים של הסוכן חתך שורה בודדת ב-~48KB ו-42% מהקורפוס נוצר מול טקסט שאיש לא ראה.
שמונה סוכני `kg-extractor` (Opus, כלים `Read, Write, Glob` בלבד) קיבלו כל אחד shard שלם — ~24 batches ו-~187k tokens קלט — וכתבו `NNN.out.json` עם `entities[]` ו-`relations[]` מתוך **סט סגור** של שישה kinds ושישה types, כשלכל ישות `quote` מילולי מתוך ה-chunk שהיא מצטטת.
`brain extract merge` הוא הכותב היחיד: הוא מוודא כל batch מול `schema.json` בשני מפלסים (envelope מול רשומה), בודק שכל `quote` באמת נמצא בטקסט אחרי נרמול whitespace, מאחד רשומות זהות ל-`Entity{kind|norm_name}` אחד עם כל ה-`evidence_chunk_ids`, ומטביע `MENTIONS{quote}` מה-chunk לכל ישות.
התוצאה: **195/195 batches תקינים במעבר ראשון**, 42 רשומות נדחו מתוך 14,947 (0.28%), ו-**9,237 צמתי `:Entity`, 5,492 יחסים ו-9,390 קשתות `MENTIONS` — 0 קשתות בלי provenance** (assert חי מול הגרף, לא מול השורות שהתכוונו לשלוח).
מדגם דיוק של 50 `MENTIONS` אקראיים (`--seed 7`) נשפט ידנית: 46 נתמכים + 4 חלקיים = **92%**, מעל סף הקבלה של 85%; ה-kind היה נכון ב-45 מתוך 50. הסקירה סימנה accept.

## למה ככה (הקישור לקורס)

**"סכמה קודם: העיקרון החשוב ביותר במודול" (מודול 3).** הקורס פותח בדיוק בבעיה שהשלב הזה נבנה נגדה: *"חילוץ לא מוגבל ('תחלץ ישויות וקשרים') מייצר גרף רועש: תוויות לא עקביות (`Person` / `person` / `PERSON`), שמות קשרים שרירותיים, וכפילויות. כל המסגרות המרכזיות תומכות היום בחילוץ מונחה-סכמה — **וזו צריכה להיות ברירת המחדל שלכם**."* וממשיך: *"מתחילים מ'שאלות כשירות' (competency questions) … גוזרים מהן את הישויות והקשרים המינימליים, ומרחיבים רק כששאילתות נכשלות."*

אצלנו הסט הסגור לא נגזר בשלב הזה — הוא הוכרע ב-spec §2.4 מזמן (`Feature|Decision|Problem|Alternative|Risk|Technology`, `DECIDES|MOTIVATED_BY|REJECTS|IMPLEMENTS|DEPENDS_ON|INTRODUCES_RISK`), והשלב רק אכף אותו. `brain/extract/schema.json` הוא `enum` ולא הצעה, ו-`validate.py` מתרגם חריגה ממנו לשם סיבה: `unknown_kind`, `unknown_type`. **המספר שמודד את זה: מתוך 14,947 רשומות שהסוכנים כתבו, `schema_violation` אחת ו-אפס `unknown_kind`/`unknown_type`.** אין ב-9,237 הישויות תווית שביעית, ואין ביניהן `Person` לצד `person` — לא כי הסוכן הצטיין, אלא כי לא היה לו איפה לכתוב את זה.

**"דרך 1: LangChain — LLMGraphTransformer" (מודול 3).** הקורס מראה את החתימה שמגבילה את החילוץ, ומעיר עליה במפורש: *"טופס הטאפלים מגביל גם את הכיוון והקצוות — עדיף על רשימת מחרוזות"* —

```python
allowed_relationships=[("Person", "WORKS_AT", "Company"), ("Company", "PRODUCES", "Product")]
```

זו בדיוק `brain/extract/shapes.json` שלנו — טבלת (source, type, target) לכל אחד מששת הטיפוסים — **עם הבדל אחד מכוון: אצל LangChain הטופס מסנן, אצלנו הוא סופר.** `shape_warnings()` ב-`merge.py` מסמן קשת שיצאה מהצורה, כותב אותה לדוח (`off_spec_shape`, `off_spec_by_type`, 20 דוגמאות) ו**לא** מוחק אותה. הנימוק כתוב ב-`shapes.json` עצמו: *"the first production merge found 756 relations outside the narrower reading of §2.4 — mostly a `Feature` motivating a `Problem`, which is a real sentence a KIP writes. Losing those would lose the answer to 'why was this built'."*

שתי השורות האחרות בקוד של הקורס הן ההבדל השני: `include_source=True` *"שומר את מסמך המקור ומקשר `(:Document)-[:MENTIONS]->(entity)"*. אצלנו ה-`MENTIONS` יוצא מה-**chunk** ולא מהמסמך, ונושא `quote` מילולי — כלומר הפרובננס הוא לא "המסמך הזה הזכיר את זה" אלא "**הנה הטקסט שבגללו אני מאמין בזה**". זו הסיבה שהבדיקה היקרה ביותר במיזוג היא הבדיקה על ה-quote, ושה-docstring של `validate.py` מנמק אותה כך: *"`MENTIONS{quote}` is what makes the graph auditable … a quote that is not in the chunk is a fabricated answer to that question."* עשר רשומות נדחו כ-`quote_not_verbatim` ועוד עשר כ-`quote_too_long` — עשרים פעמים שבהן הסוכן הפיק "ראיה" שלא הייתה שם.

**"דרך 2: neo4j-graphrag — SimpleKGPipeline" (מודול 3).** הקורס מציג פייפליין שעושה הכול בקריאה אחת — *"פיצול לצ'אנקים, חילוץ, בניית הגרף הלקסיקלי, embedding, ואיחוד ישויות"* — עם `schema={"node_types": …, "relationship_types": …, "patterns": …}` ו-`perform_entity_resolution=True` שהוא *"מופעל כברירת מחדל"*. אנחנו פירקנו את אותו פייפליין לחמישה שלבי `brain` נפרדים, ושני פירוקים הם הכרעה ולא נוחות:

1. **חילוץ ≠ resolution.** הכלל הראשון ב-`examples.md` ובחוזה הסוכן הוא *"Name things the way the text does; do not normalize across chunks (resolution is a later step)"*, ו-`norm_name()` ב-`names.py` **חלש בכוונה** (lowercase, בלי פיסוק, singular נאיבי) — ה-docstring אומר: *"Making it clever here would merge two things the evidence does not say are one, and no later step could tell."* מה שהקורס מקבל בחינם עם `perform_entity_resolution=True` הוא אצלנו שלב 08, עם ledger ועם שיפוט.
2. **הגרף הלקסיקלי כבר קיים.** ה-`(:Chunk)` וה-embedding נכתבו בשלב 06; שלב 07 קורא אותם החוצה ולא בונה אותם מחדש. אצל `SimpleKGPipeline` הקשר הוא `(entity)-[:FROM_CHUNK]->(:Chunk)`; אצלנו הוא `(:Chunk)-[:MENTIONS{quote}]->(:Entity)` — אותו חיבור בין השכבות, בכיוון ההפוך ועם הציטוט על הקשת.

והמלכודת שהקורס מזהיר ממנה בקופסה — *"הוא יושב תחת namespace `experimental` … ה-API השתנה לאחרונה … הוא async-בלבד"* — לא רלוונטית לנו מסיבה אחרת לגמרי: **ADR-0002**. אין לנו endpoint של LLM. *"כל שלב הדורש LLM … רץ כ-batch לסוכן Claude Code (Opus 5): קלט JSON + סכמה + דוגמאות → פלט JSON → ולידציה pydantic → רק קוד דטרמיניסטי כותב ל-Neo4j"*, ו**כלל הברזל**: *"סוכן לעולם לא כותב ל-DB ישירות."* לכן `LLMGraphTransformer` ו-`SimpleKGPipeline` הם כאן חומר קריאה, לא תלות: מה שהם עושים בקריאת פונקציה אחת אנחנו מממשים ביד — `build.py` (אריזה), `schema.json` + `examples.md` (הפרומפט), `validate.py` (ולידציה), `merge.py` (הכתיבה). המחיר כתוב ב-ADR — *"איטי יותר"* — והוא נמדד: 8 סוכנים × ~שעה ורבע.

**ובסוף, ההערה של הקורס שמסבירה למה השלב הזה קטן.** *"בארגון אמיתי חלק גדול מהגרף כבר קיים כדאטה מובנה … גרף היברידי — ליבה מובנית ואמינה + העשרה מחילוץ — עדיף כמעט תמיד על גרף שחולץ כולו ב-LLM."* בגרף שלנו `brain load` כתב 35,410 צמתים ו-109,623 קשתות בקוד דטרמיניסטי; החילוץ הוסיף 9,237 צמתים ו-14,882 קשתות מעליהם. ה-LLM לא נוגע בעובדות שכבר ידועות — `merge.py` מסרב במפורש ליצור צומת לקצה שהגרף לא מחזיק (*"`KAFKA-99999` in a KIP is outside the harvested slice, not a new work item"*), וזו הסיבה ל-21 היחסים שנדחו כ-`unresolved_endpoint`.

## מספרים

| מדד | ערך |
|---|---|
| commits בשלב (`829b837..HEAD`, נוגעים ב-`brain/extract`, `brain/common` או `tests`) | `0e221fb`, `d7be168`, `c3f2397`, `d552262`, `913168c` |
| **בחירת Phase A** | **2,743 chunks** — 1,713 KIP sections + 1,030 issue descriptions · 1,087 parents · 5,966,202 תווים · `token_est` 1,492,586 |
| מסמכים בהיקף | **268** (267 נגישים + **1 variant `ambiguous-kip`**; 0 מסמכים בהיקף בלי chunks) — ההיקף נלקח מ-`brain.chunk.scope.select()`, לא מ-predicate משלו |
| issue descriptions לפי type | Bug **688** · Improvement **228** · Sub-task **48** · New Feature **23** · Test **23** · Task **20** (Sub-task/Test/Task נכנסו **רק** דרך הפניה ל-KIP) |
| מה נשאר בחוץ | comments, commit messages, כל הרשומות הסינתטיות (Phase B) · תיאורים קצרים מ-**300** תווים |
| **batches** | **195** על **8 shards** · `batch_size` 20 · תקציב 40,000B על ה-JSON המסודר (`indent=2`) |
| גדלים בפועל | max **39,997B** · min 8,597B · mean 36,456B · סה"כ 7,108,881B · **`over_budget: []`** |
| השורה הארוכה ביותר | **23,602B** (הרצפה שהוגדרה: `MAX_LINE_BYTES` 45,000; מדידת שלב 04: ~48KB) |
| עומס לסוכן | ~**24.4** batches · ~**186,573** tokens קלט (טווח בפועל 186,543–186,634 לכל shard) |
| זמן `build` | **732ms** |
| ריצת הסוכנים | 8 סוכני `kg-extractor` במקביל, **~1:10–1:20 שעות** כל אחד, **~0.82–0.91M tokens** כל אחד |
| **`merge`: תקינות** | **195/195 valid** (`first_pass_rate` **1.0**) · 0 invalid · 0 `reported_failed` · 0 `missing_outputs` · 0 `done_without_output` |
| **רשומות שנדחו** | **42 / 14,947 = 0.28%** — `relation:unresolved_endpoint` **21** · `entity:quote_not_verbatim` **10** · `entity:quote_too_long` **10** · `entity:schema_violation` **1** |
| **ישויות** | **9,237** — Decision **3,837** · Problem **2,277** · Feature **1,118** · Technology **739** · Alternative **670** · Risk **596** |
| **יחסים** | **5,492** — DECIDES **2,794** · MOTIVATED_BY **1,342** · REJECTS **642** · INTRODUCES_RISK **402** · DEPENDS_ON **254** · IMPLEMENTS **58** |
| `MENTIONS` | **9,390** — `Entity` 9,390 · WorkItem **0** · Document **0** · Component **0** · סה"כ קשתות LLM **14,882** |
| **provenance** | `edges_without_provenance` **0** (assert חי מול הגרף) · שדות חובה: `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at` · `entities_without_mention` **0** |
| Decision חלשים | **3,094 / 3,837 = 81%** (`weak=true`) · 743 נתמכים ב-MOTIVATED_BY/REJECTS |
| **ריצה חוזרת** | `nodes_created` **0** · `relationships_created` **0** · `properties_set` 205,225 · schema: 1 constraint, 2 indexes |
| זמן `merge` | **2,035ms** על 195 batches |
| **מדגם דיוק** (`--n 50 --targets entity --seed 7`) | **46 נתמכים + 4 חלקיים = 92%** (סף: 85%) · kind נכון **45/50** |
| קשתות מחוץ לצורה | **756** — MOTIVATED_BY 406 · DEPENDS_ON 235 · DECIDES 55 · IMPLEMENTS 26 · REJECTS 23 · INTRODUCES_RISK 11 → **396** אחרי הרחבת הטבלה ב-`shapes.json` (נספרות, לא נמחקות) |
| קצוות לא-פתירים | 21 שמות · **0 מהם הוכרזו ב-batch אחר** (`unresolved_endpoint_matches_other_batch`) — מעבר resolution חוצה-batch לא היה קונה כלום |
| **קונסולידציה** | **98.6%** מהישויות עם chunk ראייתי **אחד** · 9,390 mentions / 9,237 ישויות = **1.017** |
| קישוריות | **35%** מהישויות בלי אף קשת סמנטית (Technology: **83%**) · **777** Decisions מבודדים |
| התנגשויות בין kinds | **19** קבוצות `norm_name` שנחלקו לשני kinds (בעיקר מפתחות קונפיגורציה שחולצו פעם כ-`Technology` ופעם כ-`Feature`) |
| NFKC | **לא נדרש** — כל 10 כשלי ה-verbatim היו ASCII, כלומר שגיאות סוכן ולא הבדלי יוניקוד; `normalise_quote()` נשאר "whitespace בלבד" |

## מה הפתיע

- **הסוכנים השתמשו במפתחות כקצוות של יחסים — ומעולם לא כשמות של ישויות.** ל-`facts.key_ref()` יש שני מסלולים: אחד שממיר שם ישות שהוא מפתח קיים (`KIP-848`, `KAFKA-16046`) לקישור לצומת הקיים במקום ל-`Entity` חדש, ואחד שפותר קצה של יחס. המסלול הראשון **לא נורה אף פעם**: `mentions_by_target_label` הוא `Entity: 9390, WorkItem: 0, Document: 0, Component: 0`. המסלול השני נשא 2,794 קשתות `DECIDES` שהמקור שלהן הוא Document או WorkItem. במילים אחרות, הסוכנים הבינו את הכלל בדיוק כפי שהוא נוסח — "מי מחליט" הוא ה-KIP, אבל "מה החלטנו" הוא תמיד ישות חדשה — ואף אחד לא כתב ישות ששמה `KIP-848`. חצי מהקוד שנכתב לטיפול במקרה הזה הוא ביטוח שלא נדרש; החצי השני נשא שליש מהגרף.
- **ה-Feature נושא את הסיבות, וה-Decision נשאר חלש.** 81% מ-3,837 ההחלטות (3,094) חסרות MOTIVATED_BY או REJECTS, בזמן ש-406 מתוך 756 הקשתות שיצאו מהצורה הן `MOTIVATED_BY` שמקורה `Entity:Feature` ולא `Entity:Decision`. זו לא שגיאה של הסוכנים — כך KIP כתוב: הפסקה אומרת "היכולת X נחוצה כי Y", לא "החלטנו X כי Y". הכלל ב-spec היה צר מהטקסט, והכרעת המתכנן הייתה להרחיב את הטבלה ולא לזרוק את הקשתות. ההשלכה, שנרשמה ל-Plan 2: **`Decision{weak=true}` היא עובדה מצוטטת ולא רשומת החלטה** — לנתב אותה דרך MENTIONS ("מה KIP-N אומר"), לא דרך מסלול "למה הוחלט".
- **כמעט אפס איחוד בין chunks — למרות שזה מה שהכלל ביקש.** הכלל "תן שם לדברים כמו שהטקסט נותן" נועד למנוע נרמול מוקדם, וההנחה הייתה שאותה ישות תופיע בכמה chunks ותתאחד ב-merge. בפועל **98.6% מהישויות נשענות על chunk ראייתי אחד בלבד**, והיחס mentions/entity הוא 1.017. הסיבה: הסוכן מנסח מחדש את אותו רעיון בכל chunk לפי המשפט שלפניו, ו-`norm_name` החלש (בכוונה) לא מגשר על ניסוחים שונים. כלומר שלב 07 לא בנה גרף מאוחד — הוא בנה **אינדקס טענות עם ראיות**, וכל עבודת האיחוד נדחתה לשלב 08.
- **החלטות boilerplate שנראות זהות ואסור למזג.** "This KIP does not change any public interfaces" ו-"No migration is proposed" מופיעות בעשרות KIPs שונים, ומתחלצות כ-Decision עם `norm_name` כמעט זהה. מיזוג שלהן היה יוצר צומת אחד שמחובר ל-40 מסמכים ומשקר על כולם. זה הצד השני של אותו מטבע: אותו `norm_name` חלש שמפספס איחוד אמיתי הוא גם מה שמונע איחוד הרסני — והכרעת "מה כן למזג" חייבת ראיות, לא מחרוזות.
- **טבלת הצורות התפצלה לשני מקורות, ואף אחד לא שם לב עד הסקירה.** היא הייתה dict בתוך `merge.py` **ומשפט ב-spec §2.4** — שני מקורות לכלל אחד. התוצאה: ל-`INTRODUCES_RISK` הייתה רשימת `source` ריקה, מה שפטר את הטיפוס כולו מהבדיקה שנכתבה בשבילו, **בשקט**. הסקירה תפסה את זה, הטבלה עברה ל-`brain/extract/shapes.json` (עם הפניה ל-spec שהיא אחראית מולו ובדיקה שהיא מכסה את הסט הסגור), ומספר הקשתות מחוץ לצורה ירד מ-756 ל-396 — לא כי הגרף השתנה, אלא כי הכלל סוף-סוף כתוב פעם אחת.
- **סוכנים דיווחו שוב ושוב על "chunk ענק" שאינו קיים.** שני `notes` נפרדים מדווחים על chunk של "roughly 2.8 million characters" ו-"roughly 7.15 million characters", אחד מהם עם התראה: *"looks like a chunker bug worth checking before the merge"*. אבל `max_chars` של כל הבחירה הוא **23,284**. מה שקרה: הטקסט עצמו מכיל את הסימון `...[truncated 7156182 chars]...` משלב מוקדם יותר, והסוכן קרא את המספר שבסימון כגודל ה-chunk. ה-chunk ב-`shard-07/004` הוא ~2.7KB (השורה הארוכה ביותר בכל ה-batch היא 3,120B), וזה ב-`shard-05/006` הוא 23,284 תווים. אף ישות לא נוצרה מהם — אבל דוח "בעיית דאטה" מהסוכן הוא **טענה על הקלט, לא מדידה שלו**, וצריך לאמת אותו מול המנפסט לפני שפותחים באג.
- **הריצה נקטעה באמצע, ו-`status.json` היה ההבדל בין המשך לבין התחלה מחדש.** הממצא הרשום ב-`progress.md` (2026-09-06 21:11): *"שינה של המק + מכסת session של Opus הפילו 4 סוכנים באמצע; `status.json` לכל shard אפשר להמשיך מהנקודה. מעכשיו `caffeinate -dims` רץ בזמן batches."* הכלל בחוזה הסוכן — *"Write `status.json` after every batch … Never skip a batch silently"* — נכתב כדי שהמיזוג ידע מה נכשל; מה שהוא באמת קנה היה **חסינות בפני נפילה של המכונה**. הסוכנים שהמשיכו קראו את `done[]` והתחילו מה-batch הבא; בסוף כל שמונת ה-`status.json` מראים `failed: []`.

## מה היינו משנים

- **דחייה ברמת הרשומה — לתכנן אותה לפני הכתיבה, לא כתיקון.** החלוקה לשני מפלסים (envelope פוסל batch, רשומה בודדת נפסלת ונספרת) היא מה שהפך 42 טעויות ל-0.28% במקום לאסון: הממוצע הוא **77 רשומות ל-batch** (14,947/195), כך שכל רשומה פגומה הייתה שולחת ~77 רשומות טובות ל-`retry/`, וב-2 ניסיונות — ל-`quarantine/`. עם 20 ציטוטים שגויים מפוזרים על batches שונים, "ה-batch הוא יחידת הדחייה" היה מוריד את שיעור המעבר הראשון מ-100% למשהו שדורש סבב שני של סוכנים. הכלל שכדאי לקחת קדימה: **יחידת הכתיבה של המודל ויחידת הדחייה של הוולידציה הן לא אותה יחידה.**
- **כלל אחד — קובץ אחד.** `shapes.json` נולד מבאג שקט (`INTRODUCES_RISK` עם `source` ריק). אותה תבנית קיימת עדיין במקומות שלא תוקנו: `validate.records_seen()` ו-`validate.soft_reasons()` כתובים ב-`validate.py`, אבל `merge.build_report()` **לא קורא לאף אחד מהם** — ולכן המכנה של שיעור הדחייה (14,947) ותוצאת ה-soft check אינם ב-`data/reports/extract.json`, והם נשענים על חישוב חיצוני. פונקציה שמחשבת מספר לדוח ולא נקראת מהדוח היא בדיוק אותה בעיה, מפלס אחד מטה.
- **`consolidation rate` היה צריך להיות מדד בדוח מהיום הראשון.** "98.6% מהישויות עם chunk אחד" הוא הממצא החשוב ביותר של השלב, והוא לא הופיע בדוח — הוא נמצא בשאילתה בסקירה. שתי המנות הן זולות ומיידיות: `mentions/entity` ו-`entities/chunk`. בלעדיהן "9,237 ישויות" נשמע כמו הצלחה; איתן ברור שזה 9,237 **טענות**, ושהשאלה האמיתית פתוחה לשלב 08. נרשם ל-Plan 2 כמדד חובה לכל שלב חילוץ.
- **כלל הגבול של ה-quote היה צריך להיות בחוזה, לא אחריו.** `quote_found()` מסתפק בכל substring, ולכן סוכן שספר 300 תווים ועצר מייצר `"...the listeners configuratio"` — מילולי לחלוטין וראיה לכלום. `quote_boundary_ok()` נכתב ב-2026-09-07 ונמצא כרגע ב-`SOFT_CHECKS`: נספר, לא דוחה, *"until the planner has seen the number against 9,390 accepted quotes"*. זה הסדר הנכון לכלל חדש — אבל הכלל עצמו היה צריך להיות בסכמה מלכתחילה, ליד `maxLength: 300` שיצר אותו.
- **`batch` ≤40KB רב-שורתי — מיום ראשון, בכל builder.** הכלל הזה כבר היה ממצא כתוב אחרי שלב 04 (*"כלי Read של הסוכנים חותך קובץ ~49KB בשורה אחת"*), ושלב 07 יישם אותו נכון: אריזה מול הגודל **המסודר בפועל** (`json.dumps(..., indent=2)`) ולא מול אומדן, `max_bytes` 40,000 שנשמר (max 39,997), ו-`MAX_LINE_BYTES` 45,000 שנכשל בקול אם chunk בודד לא נכנס. מה שהיה חסר זה שהכלל יהיה **מנגנון ולא הערה**: `build.py` של שלב 07 מכיל את הבדיקה, אבל אין שכבה משותפת שכל batch builder עתידי יורש ממנה, וההערה ב-`progress.md` ("חובה לכל batch builder עתידי") היא עדיין הערה.

## מה זה מלמד (המתכנן)

**1. "סכמה קודם" עובד — והמחיר שלו גלוי במספרים.** 6 kinds, 6 types, quote מילולי חובה, ולידציה דטרמיניסטית: 195/195 batches תקינים, 0.28% רשומות נדחו, 92% מהמדגם נתמך, 0 קשתות בלי provenance. זה מה שהקורס מבטיח מ-`allowed_nodes/relationships` — רק שכאן הסט הסגור לא *מסנן* אלא *נספר* (`shapes.json`): כשהמחלצים הגיעו שוב ושוב ל-`MOTIVATED_BY` מ-Feature (406 פעמים), זו הייתה החלטה שלי להרחיב את הספק, לא שגיאה שנעלמה בשקט. סכמה סגורה + ספירה של מה שלא נכנס = הדרך לגלות מה הסכמה שלך מפספסת.

**2. דיוק גבוה ≠ שכבת ידע.** 98.6% מהישויות עם chunk ראייתי אחד, mentions/entity = 1.017. החילוץ ייצר restatement נאמן לכל chunk — לא מושגים מאוחדים. זה לא כשל של הסוכנים: הכלל "קרא לדברים כמו שהטקסט קורא להם" הוא נכון (איחוד מוקדם מסתיר את ההתנגשות מהשלב שאמור לשקול אותה), אבל הוא אומר ש-**resolve הוא השלב שבונה את שכבת הידע**, ו-extract הוא רק הספק שלו. המדד שחסר היה ואמור להיות בכל שלב חילוץ מעכשיו: **consolidation rate**.

**3. 81% "החלטות חלשות" הן ממצא על הטקסט, לא על המחלץ.** KIPs אומרים "this KIP proposes X" (Decision) ו"X fixes Y" (Feature→Problem). הסיבות נתלות על ה-Feature; ה-Decision נשארת בלי MOTIVATED_BY. ל-Plan 2 זה קובע ניתוב: `weak=true` = עובדה מצוטטת ("מה KIP-N אומר", דרך MENTIONS), לא רשומת החלטה ("למה הוחלט", דרך MOTIVATED_BY/REJECTS). ובארגון אמיתי: מסמכי design של הצוות שלך כנראה כתובים אותו דבר.

**4. שני סטים סגורים בשני מקומות = שקר במספרים.** `off_spec_shape: 756` מדד dict בפייתון שסטה מהספק לשני הכיוונים; `INTRODUCES_RISK` בכלל לא נבדק (tuple ריק). אחרי טבלה אחת (`shapes.json` עם הפניה לשורת הספק): 193 (3.5%). כל מספר שנגזר מכלל צריך את מקור הכלל לידו — אחרת הוא הופך לפולקלור.

**5. סוכן = LLM, במספרים.** 8 סוכנים × ~1h15 × ~0.85M tokens קלט; ~2.7 ישויות לדקת-סוכן; שינה של המק + מכסת session הפילו 4 באמצע — ו-`status.json` לכל shard החזיר אותם מהנקודה המדויקת. עלות ה-API המקבילה הייתה ~$20–40; הרווח: replay, diff, ו-0 חוסר-ודאות על מה הופק ממה. הארכיטקטורה (batches → ולידציה → merge דטרמיניסטי) לא משתנה כשמחליפים סוכן ב-API.

**6. שני דפוסי "כמעט-כפילות" שחייבים להישאר נפרדים.** boilerplate של KIPs ("no compatibility concerns because the APIs are new" ×3 מ-KIPs שונים) נראה זהה ואסור למזג; config key שהוא Technology למחלץ אחד ו-Feature לאחר (19 קבוצות) *כן* צריך כלל. זה ההבדל בין דמיון טקסטואלי לזהות — ובדיוק מה ש-resolve צריך להכריע עם evidence, לא עם cosine.

**מה זה קובע ל-resolve (08) ולקהילות (09):** ישויות — ANN לפי embedding של `name — description` + חובה של token משותף או parent משותף ל-auto-merge; Technology (83% מבודדות) מחוץ לפרויקציה הסמנטית או דרך MENTIONS בלבד; ה-777 החלטות מבודדות הן חומר recall, לא רעש.
