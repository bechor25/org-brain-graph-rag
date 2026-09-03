# שלב 02 — `brain harvest` (Plan 1, Task 1)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-ingest-engineer` · **מודול בקורס:** 3 (שלב 0: איכות הקלט), 5 (עדכונים אינקרמנטליים)

## מטרה
קונקטורים עמידים שמורידים את הפרוסה פעם אחת לדיסק, עם checkpoint, ומפיקים דוח צפיפות קישורים מהמשיכה המלאה.

## קלטים
- `docs/planning/probe-2026-09-03.md` + `.json` — endpoints שעבדו, מגבלות, מלכודות (`"connect"` מצוטט, cap 1000, `KAFKA-1`)
- clone קיים ב-`data/raw/git/kafka`
- `docs/superpowers/plans/2026-09-03-plan1-corpus-to-graph.md` — Task 1 (חוזה + קריטריונים)

## פלטים
- `brain/harvest/{base,jira,confluence,git}.py`, פקודת `brain harvest`, `data/raw/<source>/…`, `data/reports/harvest.json`
- בדיקות יחידה עם fixtures מוקלטים; בדיקת רשת מסומנת `network`

## קריטריוני קבלה
ראה Task 1 בתכנית (Jira ≥1,400 עם changelog מלא; Confluence ≥1,350; git ≥4,000 keyed; ריצה שנייה = 0 דפים חדשים; `--since` עובד; בדיקות pagination/checkpoint/backoff/quoting). `make check` ירוק.

## מה תלמד בשלב הזה
איך נראה קונקטור ארגוני שאפשר לסמוך עליו (checkpoint לכל דף, backoff, דוח שגיאות במקום "הצלחה בשקט"), ולמה ההחלטה הכי חשובה ב-harvest היא לשמור raw לדיסק ולא לנרמל תוך כדי.

## הערות למבצע
- הסוכן מתכנן וכותב את הקוד בעצמו — התכנית נותנת חוזה וקריטריונים, לא קוד.
- Jira: `component in (streams, "connect", clients)` — `connect` מילה שמורה. `fields=*all&expand=changelog`, `maxResults=500`, pacing ≥1s.
- Confluence: להוריד את **כל** 1,391 דפי KIP כ-raw (זול); הגבלת הטמעה למצוטטים נעשית ב-chunk.
- git: לא להשתמש ב-GitHub API (60/h). הכל מה-clone המקומי.
- אין הרשאות/credentials. אין כתיבה ל-Neo4j בשלב זה.
