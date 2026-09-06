# ADR-0005: מודולריות, איפוס, וחיבור קל של מערכות

**סטטוס:** מאושר (2026-09-03, דרישת המשתמש)

**הקשר:** ה-POC רץ על Kafka + שכבה סינתטית. בסוף המשתמש רוצה למחוק את דאטה הבדיקות ולחבר מערכות אמיתיות (Jira/ADO/Xray/Confluence של הארגון) בקלות.

**החלטה:**
1. **גבול מודול = המודל הקנוני.** קונקטור חדש = `Connector` (probe/fetch/checkpoint) + mapper ל-5 הטיפוסים + בדיקת golden. שום קוד אחרי `canon` לא יודע מאיפה הדאטה.
2. **registry מונחה-קונפיג** — `sources.yaml` בשורש: לכל מקור `type` (jira/confluence/git/ado/xray/…), `base_url`, שאילתה (JQL/CQL), `project_keys` (allowlist), `auth_env` (שם משתנה סביבה). אין מפתחות פרויקט או URL בקוד.
3. **`brain reset [--graph] [--data] [--all]`** — מוחק את תוכן הגרף (constraints/indexes נשארים) ואת `data/raw|canonical|batches|reports|eval`; דורש אישור מפורש (`--yes`). fixtures לא נמחקים.
4. **auth דרך env בלבד**; הקונקטורים תומכים באנונימי (POC) ובטוקן (ארגון).
5. **השכבה הסינתטית = plugin**; לא רצה אלא אם `brain synth` הופעל במפורש; `synthetic=true` על כל רשומה מאפשר מחיקה סלקטיבית (`brain reset --synthetic`).
6. **מדריך:** `docs/guides/adding-a-connector.md` (עברית) — 3 קבצים + `sources.yaml` + פקודה אחת.

**השלכות:** משימה חדשה ב-Plan 1 (Task 10, אחרי chunk); Plan 2 (MCP) לא מושפע — הכלים עובדים על הגרף בלבד.
