# יומן תכנון — מוח ארגוני Graph RAG (POC)

תאריך התחלה: 2026-09-03. מתכנן: Claude Fable 5.1. מבצע: סוכני Opus 5 (`.claude/agents/`).

## החלטות שהתקבלו בדיאלוג

| # | שאלה | תשובה | השלכה |
|---|---|---|---|
| 1 | מקור הדאטה | "הכי דומה לארגון אמיתי, אתגרים אמיתיים" | היברידי: Apache Kafka אמיתי (Jira+changelog, Confluence KIPs, GitHub) + שכבת Xray/ADO סינתטית מקושרת לישויות אמיתיות |
| 2 | סוגי שאלות | כל הארבעה: עקיבות חוצת-מערכות, ניתוח השפעה, רציונל החלטות, גלובלי/טמפורלי | נדרש הארסנל המלא: Local, Text2Cypher, Global (קהילות), טמפורלי |
| 3 | LLM ותקציב | "אם נדרש LLM — סוכנים. embedding — מקומי, עברית+אנגלית" | אפס API. חילוץ/סיכום/שיפוט = batches לסוכני Opus. embedding = bge-m3 מקומי |
| 4 | מיקום | "הכל בתיקייה הזאת, מקצה לקצה עם deploy" | פרויקט עצמאי ב-`graph-rag/`. אינטגרציה ל-NessBot מחוץ ל-scope; MCP כממשק נקי לפורט עתידי |
| 5 | תיעוד | "כל התכנון והשלבים נשמרים בתיקייה שלי" | `docs/superpowers/specs/` (spec), `docs/decisions/` (ADR), `docs/lessons/` (שיעור לכל שלב), `docs/planning/` (יומן זה) |

## גישות שנשקלו

- **A. Neo4j + סוכנים כ-LLM + פייפליין ידני** — נבחר. למידה מקסימלית, אפס עלות, שליטה בכל שכבה.
- **B. מסגרות (MS GraphRAG / SimpleKGPipeline) עם proxy ל-`claude -p`** — נדחה: hack, איטי, מסתיר פנימיות.
- **C. A + מודול השוואה עם API אמיתי בעתיד** — נשמר כהרחבה אופציונלית.

## סטטוס סעיפי העיצוב

| סעיף | סטטוס |
|---|---|
| 1. ארכיטקטורה ופריסה | מאושר |
| 2. קורפוס, מודל קנוני, סכמת גרף | מאושר (רכיבים: streams/connect/clients — הכרעת המתכנן) |
| 3. פייפליין בנייה (load / extract / resolve / communities) | מאושר |
| 4. אחזור, router, MCP | מאושר |
| 5. הערכה | מאושר |
| 6. סוכנים ותהליך ביצוע | מאושר |
| 7. בדיקות וטיפול בשגיאות | מאושר |

## Self-review של ה-spec (2026-09-03)

תוקן inline: אחידות נתיבי batches בין סעיף 1 ל-3; `:Claim` שלא הוגדר → `relations`; מפתחות בדוגמאות השאלות סומנו כהמחשה; `synthetic_truth.json` נוסף כ-ground truth מכונה-קריא; הובהר מי כותב Cypher במצב A/B; כלי `lesson-writer`; אימות GDS ב-`brain doctor`; פסקת Scope בראש המסמך.

**תיקון (writing-plans):** Neo4j Community ללא RBAC → `brain_ro` הוחלף ב-`RoutingControl.READ` (אכיפת השרת) + deny-list + EXPLAIN.
