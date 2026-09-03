# התקדמות — מוח ארגוני Graph RAG

מקור אמת יחיד ל"איפה אנחנו". מתעדכן ע"י המתכנן אחרי כל שלב. branch עבודה: `plan0-foundations`.

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
| 0 | 10 templates + README | ⬜ | | `.env.example`: להחליף "(see Task 3)" ב-"(see README)" |

## ממצאים (findings)
- `neo4j:2026.06.0` מכיל APOC ו-GDS מקומית (`/var/lib/neo4j/labs`, `/products`) — אין תלות ברשת בזמן עלייה.
- Neo4j Community: `RoutingControl.READ` נאכף בצד השרת — בסיס אמיתי ל-`run_cypher` מוגן.
- ruff ≥0.16 מפרמט Python fences בתוך Markdown — לכן docs מוחרג מהפורמטר (lint עדיין רץ על הכל).

## ל-Plan 1 (מסקירת 7–8)
- regex issue תופס `UTF-8`, `SHA-256`, `COVID-19` → allowlist של project keys ידועים (מה-harvest) לפני טעינה לגרף.
- `Ref(kind=pr, key=N)` ללא repo → להחליט `owner/repo#N` לפני ingest של יותר מריפו אחד.
- `Link.direction` על שני הקצוות → loader חייב לאחד זוגות הדדיים לקשת אחת.
- `Person.id` לא מסמן אם עבר resolution → להוסיף שדה/סמן לפני שלב resolve.
- `write_jsonl` דורס → temp+rename כשהפלט הופך ל-artifact.

## החלטות פתוחות
- Ollama רץ כתהליך ידני (`ollama serve`), לא כ-service — README יסביר; LaunchAgent אופציונלי בהמשך.
