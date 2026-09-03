# שלב 04 — שכבה סינתטית Xray/ADO (Plan 1, Task 3)

**תכנית:** Plan 1 · **סוכנים:** `brain-ingest-engineer` (כלים: build/merge) + `synthetic-org-generator` ×3 (תוכן) · **מודול בקורס:** 9 ("בניית סט הערכה משלכם" — אמת לפי בנייה)

## מטרה
להוסיף את שכבות ניהול-הבדיקות (Xray) והמסירה (ADO) שאין בדאטה ציבורי, עם רעש **ידוע** — כך ש-resolution ועקיבות יימדדו מול אמת.

## קלטים
- `brain/canon/synthetic_spec.md` — חוזה הרעש (יחסים, מפתחות, כללי תוכן). **מחייב.**
- `data/canonical/*.jsonl` משלב 03 (stories/bugs אמיתיים, KIPs, זהויות)
- Task 3 בתכנית

## פלטים
- `brain synth build` → `data/batches/synthetic/<shard>/NNN.in.json` (3 shards, ~40 issues אמיתיים ל-batch + KIPs מוזכרים + רשימת זהויות + מונה מפתחות אחרון)
- סוכנים → `NNN.out.json` + `status.json`
- `brain synth merge` → append ל-`data/canonical/*.jsonl` (רשומות `synthetic=true`), `data/canonical/synthetic_truth.json`, `data/reports/synth.json`

## קריטריוני קבלה
- [ ] ולידציה pydantic לכל רשומה; מפתחות ייחודיים גלובלית; `synthetic=true` בכולן; allowlist מפתחות מורחב ל-`XT/XE/XP/XS/ADO`.
- [ ] יחסים בתוך ±5% מה-spec: כיסוי tests ≥40% מה-stories/bugs; Epic לכל KIP מוזכר; ≥300 ADO items; 35% text-only links; 15% stale; 100% זהויות חדשות לאנשים בשימוש.
- [ ] truth מלא: `identity_map`, `text_only_links`, `stale_states`, `renames`, `duplicate_tests`.
- [ ] batch פגום → `retry/` (עד 2) → `quarantine/` + דוח. אין השמטה שקטה.
- [ ] ריצת merge חוזרת = אין כפילויות.

## מה תלמד בשלב הזה
איך בונים ground truth כשאין — ולמה רעש "מתוכנן" (שמות שונים לאותו אדם, קישור רק בטקסט, סטטוס מיושן) הוא מה שהופך הערכה למדידה ולא לתחושה.

## הערות למבצע
- הסוכנים-מייצרים הם LLM-role: `Read, Write, Glob` בלבד. ה-merge (קוד דטרמיניסטי) הוא היחיד שכותב ל-canonical.
- ה-truth משמש **רק** להערכה (Plan 3) ול-gold של resolution — לא לפייפליין.
