# שלב 01 — probe של מקורות הדאטה (Plan 1)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-ingest-engineer` (דרך general-purpose) · **מודול בקורס:** 3 (שלב 0: איכות הקלט — צוואר הבקבוק האמיתי)

## מטרה
לגלות, לפני כתיבת Plan 1 המפורט, מה באמת נגיש ובאיזה נפח: Jira של Kafka (כולל changelog), KIPs ב-Confluence, commits ב-GitHub, ופרויקט ADO ציבורי. לייצר דוח צפיפות קישורים לרכיבים `streams/connect/clients`.

## קלטים
- אינטרנט (קריאה בלבד). אין credentials.

## פלטים
- `docs/planning/probe-2026-09-03.md` (עברית) + `docs/planning/probe-2026-09-03.json` (מספרים גולמיים)
- clone רדוד של `apache/kafka` ב-`data/raw/git/kafka` (gitignored) — ישמש את ה-harvest

## קריטריוני קבלה
- [ ] לכל מקור: נגיש/לא, endpoint שעבד, נפח (ספירות), מגבלות (maxResults, rate limit), שדות זמינים
- [ ] Jira: `expand=changelog` ו-comments עובדים ב-search? ספירת issues לכל רכיב בחלון 2023-01→2025-12
- [ ] Confluence: מספר דפי KIP, גודל body ממוצע, anonymous OK
- [ ] git: מספר commits 2023→2025 עם `KAFKA-\d+`, אחוז עם `(#PR)`
- [ ] צפיפות: מתוך 100 issues לרכיב — % שמזכירים KIP, % עם links פורמליים, % עם commit תואם
- [ ] ADO: האם קיים פרויקט ציבורי עם work items נגישים אנונימית
- [ ] המלצה: להישאר עם Kafka או fallback ל-Flink; רכיבים לשמור/להחליף

## מה תלמד בשלב הזה
כמה מהעקיבות הארגונית קיימת כבר במטא-דאטה (links, changelog) לעומת בטקסט חופשי; מה API ציבורי מגביל; ולמה מודדים את הקלט לפני שבונים.

## הערות למבצע
לא לכתוב קוד production — סקריפטים חד-פעמיים בלבד (לא ב-`brain/`). לכבד rate limits (sleep בין קריאות). לא להוריד יותר מ-~500 issues בסך הכל בשלב זה.
