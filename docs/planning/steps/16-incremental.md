# שלב 16 — עדכון אינקרמנטלי (Plan 3, Task 4)

**תכנית:** Plan 3 · **סוכן מבצע:** `brain-ingest-engineer` (+`kg-extractor` ל-batch אחד, `community-summarizer` לקהילות שהשתנו, `brain-analyst` ל-5 השאלות) · **מודול בקורס:** 8 ("עדכון אינקרמנטלי"), 9 (מדידה בשכבות)

## מטרה
להוכיח שהפייפליין הוא **עדכון**, לא בנייה מחדש: 10 issues אמיתיים של Kafka שנוצרו אחרי סוף הפרוסה נכנסים דרך כל עשרה השלבים, כל שלב מדווח זמן ודלתא, ובסוף אפשר **להוציא אותם בחזרה** בפקודה אחת ולחזור למצב שלפני. ספק §3.9 ו-§5.5; הכרעה 7 של Plan 3.

## קלטים
- גרף מלא אחרי Plan 1 + Plan 2 (`data/reports/index.json` = המפקד שלפני).
- `data/reports/incremental_probe.json` — הפרוב שכבר רץ (קריאה בלבד, 2026-09-17):
  **432 issues** נוצרו ב-`project = KAFKA AND component in (streams, "connect", clients)` החל מ-2026-01-01; העשרה הישנים ביותר, כולם עם changelog:
  `KAFKA-20035` (23 שורות היסטוריה), `20040` (15), `20042` (8), `20043` (3), `20045` (10), `20046` (17), `20053` (5), `20056` (20), `20064` (40), `20065` (4).
- **תנאי פתיחה:** ריצות מצב A של Task 2 כבר נשמרו (`data/eval/runs/fixed/`). הצעד הזה משנה את הגרף, ולכן הוא רץ **אחרי** Task 2 (self-review של Plan 3).

## פלטים
- `data/raw/jira/since-2026-01-01/` (10 issues), רשומות `slice: "incremental"` ב-`data/canonical/*.jsonl`, צמתים וקשתות בגרף עם `slice = 'incremental'`.
- `data/reports/incremental.json` — נכתב **אחרי כל שלב** ע"י `brain.harvest.incremental.IncrementalRun`, לא בסוף.
- שיעור `docs/lessons/16-incremental.md`.

## הכרעות מתכנן (כבר ממומשות בקוד)
1. **`--slice incremental` פותח את חלון התאריכים.** `--since` לבדו ממשיך להיות "מה השתנה בתוך הפרוסה" (`updated >= …`). `--slice incremental` שואל את השאלה השנייה — "מה אין לפרוסה" — ולכן הוא מוריד מה-JQL את גבולות התאריך של הפרוסה (`created >= "2023-01-01"`, `created <= "2025-12-31"`) ומחליף אותם ב-`created >= <since>`. בלי זה הקטלוג היה מחזיר **אפס** (הפרוסה נגמרת ב-2025-12-31). זו בדיוק המוסכמה של קונקטור git מ-Plan 1: `--since` דורס את ההתחלה ומוחק את הסוף.
2. **`--limit N` נכנס ל-signature של ה-checkpoint.** משיכה מוגבלת היא קבוצת תוצאות אחרת, לכן יש לה checkpoint משלה והיא רשאית לומר `done` ביושר. חשוב: כשאין `--limit`, ה-signature **לא משתנה** — signature שמשתנה לא נכשל, הוא שותק ומושך מחדש 19,266 רשומות.
3. **מי מחליט מה "אינקרמנטלי" זה `brain canon`, לא הדגל.** רשומה שייכת לפרוסה האינקרמנטלית אם התיקייה הראשונה שראתה אותה היא `since-<date>/`. רשומה שהמשיכה המלאה כבר החזיקה נשארת `base` גם אם עותק אינקרמנטלי ניצח ב-dedupe — זה **עדכון** לקורפוס, לא תוספת אליו.
4. **`Person`/`Container` הם `base` ברגע שרשומת base אחת מזכירה אותם** (`widest_slice`). לכן ל-`brain reset --slice` אין מעבר "שמות מוגנים" כמו ל-`--synthetic`: בעיית הצומת המשותף נפתרה שכבה אחת קודם, בקובץ, איפה שאפשר לקרוא אותה.
5. **`slice: "base"` לא נכתב ל-JSON.** אותו טריק של `source_id`: הקבצים הקנוניים של הקורפוס הבסיסי נשארים byte-identical ו-`data/reports/modularity.json` ממשיך להיות בדיקה אמיתית. נבדק על הקורפוס הנוכחי: שלושת המקורות מחזירים `by_slice = {base: N, incremental: 0}`, כלומר `brain canon` היום מייצר בדיוק את אותם בתים.
6. **הצומת בגרף כן נושא `slice` תמיד** (`base` או `incremental`), כמו `synthetic` — כי property שלא נכתב נשאר תקוע על ערך ישן אחרי MERGE.
7. **Dry run = בלי `--yes`.** לא נוסף דגל `--dry-run` נפרד: ב-`brain reset` היעדר `--yes` **הוא** ה-dry run, הוא מדפיס את כל הספירות ויוצא ב-1. זה בדיוק מה ש-`--synthetic` עושה.

## רצף הפקודות — מה להריץ ומה כל שלב חייב לדווח

> לפני הכול: `cp data/reports/index.json data/reports/index.before-incremental.json` — "נוסף" הוא הפרש, וצריך את הצד השני שלו.
> כל שלב נרשם ב-`data/reports/incremental.json` דרך `IncrementalRun.timed(<step>)`: `duration_s`, הדוח של השלב, והערה אם משהו חרג.

| # | פקודה | מה חייב להופיע בדוח |
|---|---|---|
| 0 | `uv run brain harvest --source jira --probe-since 2026-01-01` | כבר רץ. `total`, 10 המפתחות, `has_changelog` לכל אחד. קריאה בלבד. |
| 1 | `uv run brain harvest --source jira --since 2026-01-01 --limit 10 --slice incremental` | זמן; `records = 10`, `pages = 1`, `checkpoint.done = true`; הנתיב `data/raw/jira/since-2026-01-01/`; `last_run.slice = incremental`, `last_run.limit = 10`. הפרוסה המלאה ב-`report["sources"]` **לא** נגעו בה. |
| 2 | `uv run brain canon --source jira` | זמן; `slices.workitems.incremental = 10`; כמה `Person`/`Container` חדשים נולדו (`slices.persons/containers.incremental`); **ריצה שנייה = אותם בתים** (`shasum` לפני ואחרי). |
| 3 | `uv run brain load` | זמן; `nodes_created` / `relationships_created` (הדלתא האמיתית); `dangling_refs` חדשים; ריצה שנייה = **0 צמתים חדשים**. |
| 4 | `uv run brain chunk --kinds all` | זמן; כמה chunks נוצרו, כמה **הוטמעו מחדש** (רק hash חדש), throughput (chunks/s) ו-batch size שנבחר; `IndexMeta.chunk_count` החי. |
| 5 | `uv run brain extract build --shards 1 --batch-size 25` | זמן; **batch אחד** (`data/batches/extract/0/000.in.json`), כמה chunks נכנסו, גודל ה-batch בבתים ≤ 40KB. |
| 6 | סוכן `kg-extractor` על ה-shard | זמן-סוכן; `status.json` = `done: ["000"]`, `failed: []`. |
| 7 | `uv run brain extract merge` | זמן; entities/relations שנוספו; 100% עם `batch_id`/`model`/`extracted_at`; כמה מהן נדבקו לישות קיימת לעומת ישות חדשה. |
| 8 | `uv run brain resolve --kinds all --tier 1` | זמן; כמה זהויות/ישויות חדשות אוחדו דטרמיניסטית; שורות חדשות ב-`resolution_ledger.json`. tier 2/3 **לא** רצים — אין להם מה לשפוט על 10 פריטים, והם היו משנים את מדידת Plan 1. |
| 9 | `uv run brain communities build --levels 2` | זמן; סה"כ קהילות לפני/אחרי; **כמה `member_hash` השתנו** — זה המספר של השלב. |
| 10 | `uv run brain communities batches --shards 1` | `copied_from_another_level` (כלל ההעתקה) + כמה קהילות באמת נשלחות לסיכום. היעד: מספר חד-ספרתי, לא 148. |
| 11 | סוכן `community-summarizer` ואז `uv run brain communities merge` | זמן-סוכן; דוחות שנכתבו; embeddings שנוצרו. |
| 12 | `uv run brain index` | זמן; כל האינדקסים ONLINE; מפקד אחרי; ההפרש מול `index.before-incremental.json` לפי label ולפי סוג קשת. |

## 5 השאלות (מצב B, `brain-analyst` דרך MCP)
נשאלות **אחרי** שלב 12, על הפריטים החדשים בלבד, ונרשמות ב-`questions[]` עם `citation_valid` מ-`brain eval cite-check`:
1. מה הבעיה שמתוארת ב-KAFKA-20035 ומי דיווח עליה? (lookup — ציטוט של מפתח + chunk)
2. אילו רכיבים (components) נוגעים בעשרת ה-issues שנוספו, ואיזה מהם מופיע הכי הרבה? (aggregation)
3. האם אחד מהעשרה מפנה ל-KIP או ל-issue קיים מהפרוסה הבסיסית? הראה את הקשת. (traversal — בודק שה-refs חצו את הגבול בין הפרוסות)
4. מה היסטוריית הסטטוס של KAFKA-20064 (40 שורות changelog) ומי החזיק אותו ומתי? (temporal — `StatusChange` + `ASSIGNED_TO`)
5. באיזו קהילה נחתו הישויות החדשות, והאם הסיכום שלה עודכן? (global/community — בודק שכלל ההעתקה וה-`member_hash` עבדו)

**קריטריון:** ≥4 מתוך 5 עם ציטוט שנפתר (`citation_valid`).

## חזרה אחורה (rollback) — חלק מהצעד, לא תוכנית חירום
```
uv run brain reset --slice incremental            # dry run: מדפיס ספירות, יוצא 1
uv run brain reset --slice incremental --yes      # מוחק
```
מה נמחק: רשומות עם `slice: "incremental"` מחמשת הקבצים הקנוניים, הצמתים שלהן, ה-chunks שלהן, ישויות שכל chunk-הראיה שלהן בפרוסה הזאת, ושורות ledger שמצביעות על זהות שנמחקה.
מה **נשאר**, והמניפסט אומר את זה: `data/raw/jira/since-2026-01-01/` (ריצת `brain canon` תחזיר את הרשומות — צריך `--data` כדי למחוק גם את הגלם) וצמתי `Community` (החברות בהן השתנתה, ורק `brain communities build` יודע להחזיר אותה).

**איפה זה מוכח:** השורה עם `--yes` **לא רצה על הגרף האמיתי**. היא מוכחת ב-`tests/live/test_reset_live.py` במרחב ה-labels `_ResetTest`, על fixture שיש בו corpus בסיס + פרוסה אינקרמנטלית אמיתית (רשומה קנונית עם `slice: "incremental"` שנטענת דרך `run_load`, ה-chunks שלה וישות שכל ראיותיה בפרוסה). על הגרף האמיתי ה-rollback נמדד ב-**dry run בלבד** (26 ישויות), וזה מה שדווח.

## קריטריוני קבלה
- [ ] 10 פריטים בגרף עם provenance מלא; `keys` בדוח = 10 המפתחות של הפרוב.
- [ ] 0 כפילויות: ריצה שנייה של canon = אותם בתים, ריצה שנייה של load = 0 צמתים חדשים.
- [ ] `data/reports/incremental.json` עובר את `validate_incremental()` — שורה לכל אחד מ-11 השלבים עם זמן, `communities.member_hash_changed`, 5 שאלות.
- [ ] ≥4/5 שאלות עם ציטוט תקף.
- [ ] rollback: **מוכח ב-`_ResetTest` עם fixture של slice, לא בהרצה על הגרף האמיתי** — `test_a_slice_reset_restores_the_exact_base_census` מראה ש-`reset --slice incremental --yes` על גרף בסיס + פרוסה אינקרמנטלית מחזיר את המפקד **המדויק** של הבסיס: צמתים לפי label **וקשתות לפי type** (המדידה: הפרוסה הוסיפה 8 צמתים ו-15 קשתות ב-9 טיפוסים; אחרי ה-reset שני החצאים זהים לבסיס — 44 צמתים ב-16 labels, 76 קשתות ב-21 טיפוסים). `test_the_increment_moves_both_halves_of_the_census` הוא השומר שלו, ו-`test_the_slice_reset_names_what_it_deleted_and_keeps_the_touched_entity` מוודא שההחזרה באה ממחיקת הפרוסה ולא מטעינה מחדש של הבסיס. **לעולם לא להריץ `--yes` על הגרף האמיתי**; שם ה-rollback נמדד ב-dry-run בלבד (26 ישויות), וזה המספר שמדווח. `Community` נשאר מחוץ להשוואה — הוא נבנה מחדש ב-`communities build`.
- [ ] `make check` ירוק; `make smoke` רץ פעם אחת בסוף, ע"י המתכנן, כשאף סוכן לא נוגע ב-DB.

## מה תלמד בשלב הזה
שהעלות האמיתית של עדכון אינקרמנטלי היא לא ה-harvest — זה 10 רשומות ושתי בקשות. היא בשלושה מקומות שקל לשכוח: chunk צריך לדעת מה **לא** להטמיע מחדש, communities צריך לדעת אילו קהילות באמת השתנו (`member_hash`, כלל ההעתקה), ו-resolution צריך להשוות רק את החדש מול הקיים. וששלב שאי אפשר לבטל הוא שלב שאי אפשר למדוד פעמיים — ולכן `slice` הוא חלק מהתכנון, לא ניקיון שאחריו.
