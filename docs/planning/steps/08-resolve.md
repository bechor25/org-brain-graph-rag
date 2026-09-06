# שלב 08 — `brain resolve` (Plan 1, Task 7)

**תכנית:** Plan 1 · **סוכנים:** `brain-graph-engineer` (3 שכבות + מדידה) + `entity-adjudicator` ×2 (התחום האפור) · **מודול בקורס:** 3 ("איחוד ישויות — רוצח האיכות השקט")

## מטרה
לאחד כפילויות של אנשים וישויות — ולמדוד את זה מול אמת ידועה.

## קלטים
- גרף: `Person` (2,187 זהויות; `resolved=false`), `Entity` (משלב 07), `alias_candidates[]` ב-`data/reports/load.json` (131 זוגות `JIRAUSER…`↔username), `data/canonical/synthetic_truth.json` → `identity_map` (420 זהויות סינתטיות → 308 אנשים אמיתיים) — **לשימוש בהערכה בלבד, אסור בפייפליין**
- `OllamaEmbedder`, spec §3.6, Task 7 בתכנית

## פלטים
- `brain/resolve/`, פקודות `brain resolve [--tier 1|2|3|all] [--dry-run]`, `brain resolve build-batches`, `brain resolve merge-decisions`, `brain resolve gold`
- `data/batches/resolve/shard-NN/NNN.in.json` (JSON מודפס-יפה, ≤40KB) → `.out.json`
- `data/eval/resolution_gold.jsonl`, `data/reports/resolve.json`

## הכרעות מתכנן
1. **Person — שכבה 1 (דטרמיניסטי):** (א) email זהה (lowercase); (ב) `jira.name == git.name` מנורמל, או display זהה + חפיפת פעילות (אותו issue/commit); (ג) `alias_candidates` של load (`JIRAUSER…`↔username על אותו issue) — **כן, זה דטרמיניסטי**: Jira עצמו כתב את שניהם על אותו פריט; (ד) confluence `userKey` == jira `name`. **אסור:** `identity_map` מה-truth.
2. **Entity — שכבה 1:** `norm_name` זהה בתוך אותו kind (כבר מפתח); טבלת aliases: `KIP-N` ↔ כותרת ה-KIP (Feature בשם הכותרת = ה-Document). כלל: Feature/Technology שהשם שלו שווה לכותרת KIP (normalized) → `SAME_AS` ל-Document, לא ל-Entity.
3. **שכבה 2 (embedding):** `name + " — " + description` דרך `OllamaEmbedder`; מועמדים באותו kind בלבד (Person: לפי display+source hints); cosine ≥0.92 → auto-merge; 0.80–0.92 → batch לשופט; <0.80 → לא. עבור Person: embedding על `display` + רשימת פריטים שנגע בהם (עד 5 כותרות) — לא על שם בלבד.
4. **שכבה 3 (adjudicator):** batches של ≤25 זוגות, כל זוג עם quotes/הקשר (3 chunks או כותרות פריטים לכל צד), פלט `same|different|unsure + reason`. `unsure` = לא ממזגים. 2 shards.
5. **ביצוע המיזוג:** `SAME_AS{tier, score, reason}` קודם (דוח + `--dry-run`), ואז `apoc.refactor.mergeNodes` עם `properties: combine` ל-`aliases[]`, `merged_from[]`, `identities[]`; הצומת השורד = הזהות **הקנונית**: לאנשים — jira אם קיים, אחרת git, אחרת confluence, אחרת סינתטי; `resolved=true`, `resolved_at`, `resolution_tier`. **idempotent:** ריצה חוזרת = 0 מיזוגים.
6. **סט זהב** (`brain resolve gold`): (א) אנשים — כל זוגות `identity_map` (חיובי) + זוגות שליליים קשים: זהויות סינתטיות של אנשים *שונים* עם display דומה (מ-truth); (ב) ישויות — 100 זוגות מהתחום האפור שהשופט סימן + 10 שהמשתמש דוגם (המתכנן מכין רשימה). P/R/F1 לכל kind ולכל tier.
7. **מדידה בדוח:** כפילויות לפני/אחרי (אנשים: זהויות/אדם; ישויות: שמות דומים באותו kind), מיזוגים לפי tier, P/R/F1 מול זהב, `unsure` count, זמן.
8. **לא מאחדים:** בין kinds; Person↔Entity; Document↔Document (KIP variants כבר מטופלים ב-`VARIANT_OF`).

## קריטריוני קבלה
- [ ] P/R ≥0.85 על זהב האנשים (יעד הקורס). אם לא — לדווח ולעצור למתכנן, לא להוריד סף.
- [ ] 0 מיזוגים חוצי-kind; כל צומת ממוזג עם `merged_from[]` ו-`aliases[]`; ריצה חוזרת 0 מיזוגים; `brain load` אחרי resolve **לא** מחזיר את הכפילויות (בדיקה live: load → resolve → load → ספירת Person לא משתנה).
- [ ] דוח מלא; batches ≤40KB מודפסים-יפה; `make check` + `make smoke` (על הקורפוס-מיני: jrao ב-3 זהויות → 1 צומת).

## מה תלמד בשלב הזה
כמה מהאיחוד הוא דטרמיניסטי (הרוב, אם שומרים מזהים גולמיים), מה embedding מוסיף ומה הוא מקלקל (false positives בשמות דומים), ולמה `unsure` הוא תשובה טובה. והכי חשוב — P/R במספרים במקום "נראה בסדר".

## הערות למבצע
- הסוכן-מהנדס לא מריץ את השופטים; המתכנן מפזר.
- להריץ **אחרי** extract (07) כדי שיהיו ישויות; אם extract מתעכב — להריץ tier 1–3 על Persons קודם עם דגל `--kinds person`.
