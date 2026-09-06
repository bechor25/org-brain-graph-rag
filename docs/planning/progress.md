# התקדמות — מוח ארגוני Graph RAG

מקור אמת יחיד ל"איפה אנחנו". מתעדכן ע"י המתכנן אחרי כל שלב.

**Plan 0 מוזג ל-`main` (2026-09-03).** ריפו ציבורי: https://github.com/bechor25/org-brain-graph-rag · הבא: Plan 1 (קורפוס → גרף) — נכתב לפרטים אחרי probe של Jira/Confluence.

| Plan | שלב | סטטוס | commit | הערות |
|---|---|---|---|---|
| 0 | 1 scaffold + CLI | ✅ | 4cfc272 | ruff 0.16 מפרמט code-blocks ב-Markdown → `extend-exclude=["docs"]` |
| 0 | 2 settings | ✅ | 14c92e8 | גרסאות שנפתרו: neo4j 6.3, typer 0.27, pydantic 2.13 — כולן תואמות |
| 0 | 3 compose neo4j | ✅ | f40c44c | `neo4j:2026.06.0` — APOC+GDS 2026.06.0 מובנים ב-image, healthy ב-11s |
| 0 | 4 GraphClient | ✅ | af24345 | read-mode guard אכוף בשרת: `Neo.ClientError.Statement.AccessMode` (driver 6.3, GQL status 08N03) |
| 0 | 5 embeddings | ✅ | 4dcb037 | Ollama 0.32 היה מותקן (לא brew) → `ollama serve` ידני; bge-m3 1.08GiB, pull 16s; 64 משפטים/0.94s |
| 0 | 6 doctor | ✅ | fab6d96, 0444366, f53793a | 7/7 OK; fix-ups מסקירות 1–4 (try/finally, loopback ports, cache_clear, docstring) |
| 0 | 7 canonical model | ✅ | 8b5d943 | 5 טיפוסים + regex mentions; עברית = `\w` ב-re → keys נמצאים גם בטקסט עברי |
| 0 | 8 mini fixtures | ✅ | 6eceb0b, 3fbc900 | 6 issues/1 KIP/3 persons/4 changes/4 containers; regex מוצא רק refs בטקסט — ה-PR חי ב-comment |
| 0 | 9 agents | ✅ | 35235e2 | 15 סוכנים: 5 הנדסה (כל הכלים), 7 סוכני-LLM (`Read, Write, Glob` בלבד), analyst (`Read`), reviewer, lesson-writer |
| 0 | 10 templates + README | ✅ | 8149f11 | README בעברית עם 5 עקרונות; `.env.example` תוקן |
| 1 | 01 probe | ✅ | — | 1,416 issues, 1,391 KIPs, 6,107 commits; search מחזיר changelog מלא; ADO לא נגיש; `"connect"` מילה שמורה |
| 1 | 02 harvest | ✅ | 79099a0…2c19ca2 | 1,416/1,391/6,107 ב-2m14s; ריצה שנייה 0; `--no-renames` הוריד git log מ-6+ דק' ל-0.44s; צפיפות links מלאה 36% (probe אמר 38%) |
| 1 | 03 canon | ✅ | 82db3e3…8d60a0c | 1,416/1,391(1,334 KIP+57 Page)/12,133/1,597/117 ב-6.9s; text-only refs 15% (any-mention 23.4%) — קריטריון תוקן; `[~user]` 1,972 אזכורים נוספים; resolution נוסף למודל |
| 1 | 04 synthetic | 🟡 כלים ✅ (d8b1a11…d939176, בסקירה); 3 סוכני-תוכן רצים | | 1,021 items נבחרו → 27 batches / 3 shards, 143 Epics מוקצים מראש; מרחבי מפתחות נפרדים לכל shard |
| 1 | 05 load | 🟡 fix-required (2 blockers: ledger shape/keys; majors: blocks/duplicates, ASSIGNED_TO zero-length ×486, TestPlan/TestSet) | cc2ad87…fba0ae9 | 32,855 צמתים / 95,695 קשתות ב-3.6s; ריצה שנייה 0/0; LINKS_TO 298 זוגות; dangling issue refs 8,217 (גבול הפרוסה); StatusChange 7,607 (18.9k רעש הוסר); 725 מרווחי assignee בלי Person → canon ימנטס מ-changelog |
| 1 | 06 chunk+embed | ⬜ | | |
| 1 | 07 extract Phase A | ⬜ | | |
| 1 | 08 resolve | ⬜ | | |
| 1 | 09 communities | ⬜ | | |
| 1 | 10 index + gate | ⬜ | | |

## החלטת תהליך (2026-09-03, המשתמש)
- מ-Plan 1 והלאה: התכנית = brief + חוזים + קריטריונים. **הסוכנים מתכננים וכותבים את הקוד**; המתכנן סוקר ומכריע. (ב-Plan 0 הקוד היה בתכנית והסוכנים הקלידו.)

## שיעורים
- Plan 0: `docs/lessons/00-foundations.md`
- Plan 1 שלבים 01–02: `docs/lessons/01-harvest.md`
- Plan 1 שלב 03: `docs/lessons/02-canon.md`

## ממצאים (findings)
- **(synth) כלי Read של הסוכנים חותך קובץ ~49KB בשורה אחת** — `.in.json` של 80–98KB בשורה אחת נקרא חלקית (הסוכן ראה 21–27 מ-40 פריטים, לא ראה `documents/persons`). חובה לכל batch builder עתידי (extract!): JSON מודפס-יפה רב-שורות (Read עם offset/limit עובד לפי שורות) ו/או batches ≤40KB. shard-02 פיצה מ-`persons.jsonl` ומיצג יחסים לפי 40.
- (synth) blocker בסקירה: ה-spec אמר "מונה גלובלי" בעוד ה-build חילק בלוקים לכל shard — תוקן ב-`98d98c1` והודעה נשלחה ל-3 המחוללים תוך כדי ריצה. לקח: כשסוטים מחוזה מחייב, תיקון החוזה הוא חלק מהסטייה.
- סוכני `.claude/agents/` נטענים רק בתחילת סשן — בסשן הזה dispatch נעשה דרך general-purpose עם ההגדרה מודבקת; מסשן חדש הם זמינים ישירות.
- `neo4j:2026.06.0` מכיל APOC ו-GDS מקומית (`/var/lib/neo4j/labs`, `/products`) — אין תלות ברשת בזמן עלייה.
- Neo4j Community: `RoutingControl.READ` נאכף בצד השרת — בסיס אמיתי ל-`run_cypher` מוגן.
- ruff ≥0.16 מפרמט Python fences בתוך Markdown — לכן docs מוחרג מהפורמטר (lint עדיין רץ על הכל).

## ל-Plan 1 (מסקירת 7–8)
- (canon) 74% מ-refs ל-issues מצביעים מחוץ לפרוסה (pre-2023) → load מקשר רק ליעדים קיימים, סופר dangling. Jira `Test` type (123) ≠ Xray Test. 9 KIPs אמיתיים חולקים מספר עם KIP אחר (`ambiguous`).
- (harvest) `KAFKA-1` ב-69 עמודי KIP; 21 מספרי KIP על 45 עמודים; `KIP-1001` = שני KIPs שונים; `KIP-929` body ריק; `raw_layout` = חוזה dedupe בין base ל-`since-*/`.
- regex issue תופס `UTF-8`, `SHA-256`, `COVID-19` → allowlist של project keys ידועים (מה-harvest) לפני טעינה לגרף.
- `Ref(kind=pr, key=N)` ללא repo → להחליט `owner/repo#N` לפני ingest של יותר מריפו אחד.
- `Link.direction` על שני הקצוות → loader חייב לאחד זוגות הדדיים לקשת אחת.
- `Person.id` לא מסמן אם עבר resolution → להוסיף שדה/סמן לפני שלב resolve.
- `write_jsonl` דורס → temp+rename כשהפלט הופך ל-artifact.

## מסקירה סופית של Plan 0
- `read_mode_guard` הוא `required=False` בכוונה — Plan 2 מוסיף guard שני (deny-list + EXPLAIN). לא לסמוך על doctor לבד.
- `make check` דורש `uv sync --extra dev`; אופציה עתידית: `[dependency-groups] dev`.

## הכרעות מתכנן מ-load
- `StatusChange.id` כולל `from` (אחרת 26→10 התנגשויות) — מאושר.
- `IN_PLAN`/`HAS_RUN{status}`: load יפרסר דטרמיניסטית את שורות `"XT-n: PASS|FAIL (…)"` ב-comments של TestExecution → `HAS_RUN{status,reason}`; `IN_PLAN` מ-`parent`=TestPlan או links `tests` מה-plan. מיושם אחרי המיזוג הסינתטי.
- `IN_SPACE` Document→Space (Space היה orphan).

## החלטות פתוחות
- Ollama רץ כתהליך ידני (`ollama serve`), לא כ-service — README יסביר; LaunchAgent אופציונלי בהמשך.
