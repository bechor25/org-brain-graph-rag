# ADR-0004: POC עצמאי בתיקיית graph-rag, MCP כממשק יחיד

**סטטוס:** מאושר (2026-09-03)

**החלטה:** הכל ב-`~/Desktop/code/graph-rag/` כפרויקט מקצה לקצה עם docker-compose. אינטגרציה ל-NessBot מחוץ ל-scope. הממשק החוצה הוא MCP server בלבד — כך שהפורט העתידי ל-NessBot (שכבר עובד MCP-first) הוא רישום שרת, לא שכתוב.
