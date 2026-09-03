# ADR-0001: מקור דאטה — Apache Kafka אמיתי + שכבת Xray/ADO סינתטית

**סטטוס:** מאושר (2026-09-03)

**הקשר:** ה-POC צריך לדמות ארגון אמיתי עם ADO/Jira/Xray/Confluence ולייצר אתגרים אמיתיים (entity resolution, קישורים בטקסט חופשי, דוקים מיושנים, זמן).

**החלטה:** Apache Kafka — Jira ציבורי (כולל changelog), Confluence ציבורי (KIPs), GitHub (commits/PRs). מעליו Xray + ADO סינתטיים שנוצרים ע"י סוכן ומקושרים לישויות האמיתיות, עם רעש מכוון. ה-harvester יבדוק תחילה אם קיים פרויקט ADO ציבורי עם work items נגישים — לדגימת קונקטור אמיתי בלבד.

**חלופות:** GitHub org כפרוקסי (סמנטיקה רחוקה מ-Jira/ADO); ארגון סינתטי מלא (נקי מדי, לא מלמד resolution).

**השלכות:** נדרש fallback לפרויקט Apache אחר (Flink) אם Kafka Jira לא נגיש. שכבה סינתטית מסומנת `synthetic=true` בכל צומת.
