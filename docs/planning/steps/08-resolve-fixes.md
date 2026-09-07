# שלב 08b — תיקוני resolve אחרי סקירה (Plan 1, Task 7 · fix-required)

**סוכן מבצע:** `brain-graph-engineer` · **נקודת מוצא:** commit `e19179c` (עבודה חלקית של סוכן שהופסק: `dedupe_relationships` אחרי extract merge, `closure_overrides`, `write_index_meta`) — לאמת שכל אחד שלם, מחווט ונבדק, לא להניח.

## חובה (blockers)
1. **כלל `username_stem` (tier 1, Person) מעולם לא רץ על הגרף החי.** להריץ `brain resolve --kinds person --tier 1` (idempotent — רק הזוגות החדשים אמורים להתמזג, צפי ≈ +16), אחר-כך closure + עדכון `data/canonical/resolution_ledger.json`, ואז `brain resolve gold` / evaluate מחדש → `data/reports/resolve.json` עם P/R/F1 מעודכנים (צפי R ≈ 0.775). לדווח לפני/אחרי.
2. **113 קשתות כפולות** (extract merge אחרי resolve ניתב שני קצוות לשורד אחד → קשתות מקבילות זהות). להריץ את ה-dedupe על הגרף החי; אימות live: מספר הקשתות המקבילות (אותו type, אותם קצוות, אותן properties) בין Entity = 0; ריצה חוזרת מוחקת 0.

## חשוב (majors)
3. provenance (`batch_id, model, extracted_at`) על מיזוגי tier 3 (על `SAME_AS` ועל השורד).
4. `brain/resolve/reset.py`: הודעה לפי kind (person/entity) — מה נמחק ומה נשאר. **לא לגעת ב-`brain/reset.py`** (בבעלות סוכן אחר במקביל).
5. `closure_overrides` בדוח — לאמת שהמספר מחושב ומופיע.
6. הערת eval מעגלי לישויות בדוח (`entity_eval.circular: true` + משפט הסבר: ה-gold = פסקי השופט).
7. `IndexMeta` ל-`person_embedding` ו-`entity_embedding` (מודל, מימד, ספירה **חיה**) — לאמת שנכתב בריצה.

## קריטריוני קבלה
- [ ] live: 0 קשתות מקבילות זהות; person count ירד ב-≈16; `brain load` אחרי = 0/0; `brain extract merge` חוזר = 0 ישויות/קשתות חדשות, 0 נמחקו.
- [ ] `data/reports/resolve.json` מעודכן (לפני/אחרי לכל מספר שהשתנה).
- [ ] `make check` + `make smoke` ירוקים.

## גבולות קבצים (עבודה במקביל ל-3 סוכנים)
בבעלותך: `brain/resolve/**`, `brain/extract/merge.py`, `tests/test_resolve_*`. `brain/cli.py` — רק אם הכרחי, עריכה מינימלית, לקרוא מחדש לפני כל עריכה. **לא** לגעת ב-`brain/reset.py`, `brain/community/**`, `brain/index/**`, `brain/canon/**`, `brain/harvest/**`, `brain/extract/graph.py`. git: לשלב רק את הנתיבים שלך (`git add <paths>`), לעולם לא `git add -A` / `git stash`; retry על `index.lock`. prefix: `fix(resolve):`.
