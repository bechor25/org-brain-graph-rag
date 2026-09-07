# שלב 11b — תיקוני מודולריות אחרי סקירה (Plan 1, Task 10 · fix-required)

**סוכן מבצע:** `brain-ingest-engineer` · **נקודת מוצא:** commit `162d451` (עבודה חלקית של סוכן שהופסק: `synthetic` על Chunk + Entity, redaction של credentials, `reset_lock`) — לאמת שכל אחד שלם, מחווט ונבדק. ADR-0005, `docs/guides/adding-a-connector.md`, `data/reports/modularity.json`.

## חובה (blockers מהסקירה)
1. **`synthetic` לא הגיע ל-Chunk/Entity → 1,882 orphans אחרי `brain reset --synthetic`.** ה-diff מוסיף `Chunk.synthetic` (מהאב) ו-`Entity.synthetic` (רק אם **כל** chunks הראייה סינתטיים). חסר: **backfill לגרף החי** — ה-chunks הקיימים נכתבו לפני שהמאפיין היה קיים (`null`). לספק פקודה idempotent (למשל `brain chunk --stamp-synthetic` או צעד ב-`brain reset --synthetic --dry-run`) שמעדכנת `Chunk.synthetic` מהאב עבור כל chunk חי, ו-`Entity.synthetic` לפי chunks הראייה. אימות live: `count(Chunk where synthetic IS NULL) = 0`; `brain reset --synthetic --dry-run` מדווח 0 orphans צפויים.
2. **דליפת token דרך `TimeoutExpired`** (repr של subprocess/HTTP חריגה נושא את ה-header). לאמת שה-redaction מכסה: `TimeoutExpired`, `HTTPError` body/headers, `stats()`, לוגים ודוחות. בדיקה: חריגה מזויפת עם token בטקסט → הדוח/ההודעה לא מכילים אותו.
3. **שני מקורות מאותו סוג (שני Jira) מתנגשים ב-ids.** הכרעת מתכנן: `id` ייחודי חובה לכל מקור ב-`sources.yaml` (registry מסרב לכפילות); raw layout `data/raw/<id>/`; דוחות harvest/canon לפי `id`; רשומה קנונית נושאת `source_id`. **מפתחות קנוניים לא מקבלים prefix** — התנגשות `KAFKA-1` בין שתי instances של Jira היא מחוץ להיקף ה-POC, ומתועדת במדריך כמגבלה ידועה.

## קריטריוני קבלה
- [ ] **אין להריץ `brain reset` על הגרף האמיתי** — אימות reset דרך `tests/live/test_reset_live.py` עם label-prefix (כמו היום). Backfill (סעיף 1) כן רץ על הגרף האמיתי (רק SET של property).
- [ ] canon על `sources.yaml` הנוכחי = byte-identical לקנוני הקיים (`data/reports/modularity.json → matches_baseline: true`).
- [ ] `data/reports/modularity.json` מחודש; המדריך מעודכן (id ייחודי, מגבלת מפתחות).
- [ ] `make check` + `make smoke` ירוקים.

## גבולות קבצים (עבודה במקביל ל-3 סוכנים)
בבעלותך: `brain/canon/**`, `brain/harvest/**`, `brain/common/**`, `brain/chunk/**`, `brain/reset.py`, `brain/modularity.py`, `brain/extract/graph.py` (רק stamping של `synthetic`), `sources.yaml`, `docs/guides/**`, הבדיקות של אלה + `tests/reset_helpers.py`, `tests/live/test_reset_live.py`. `brain/cli.py` — רק פקודות harvest/canon/chunk/reset, לקרוא מחדש לפני כל עריכה. **לא** לגעת ב-`brain/resolve/**`, `brain/extract/merge.py`, `brain/community/**`, `brain/index/**`. git: לשלב רק את הנתיבים שלך, לעולם לא `git add -A` / `git stash`; retry על `index.lock`. prefix: `fix(modularity):`.
