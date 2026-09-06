# שלב 07 — `brain extract` Phase A (Plan 1, Task 6)

**תכנית:** Plan 1 · **סוכנים:** `brain-graph-engineer` (כלים: build/merge, schema, examples) + `kg-extractor` ×4 (תוכן) · **מודול בקורס:** 3 ("סכמה קודם", "LLMGraphTransformer / SimpleKGPipeline" — מה אנחנו מממשים ידנית ולמה)

## מטרה
חילוץ ישויות ויחסים מונחה-סכמה מהטקסט בעל הערך הגבוה ביותר (KIPs + תיאורי issues), מוזג לגרף עם provenance מלא. **הסוכן הוא ה-LLM; קוד דטרמיניסטי הוא היחיד שכותב.**

## קלטים
- גרף עם `:Chunk` (שלב 06) — `Chunk{id, parent_key, parent_kind, kind, text, position}`
- spec §2.4 (סט סגור: kinds `Feature|Decision|Problem|Alternative|Risk|Technology`; types `DECIDES|MOTIVATED_BY|REJECTS|IMPLEMENTS|DEPENDS_ON|INTRODUCES_RISK|MENTIONS`), Task 6 בתכנית
- `.claude/agents/kg-extractor.md` (חוזה הסוכן)

## פלטים
- `brain/extract/schema.json` (סכמת פלט), `brain/extract/examples.md` (3 דוגמאות מלאות: motivation של KIP, rejected alternative, תיאור issue) — **טיוטה ע"י הסוכן, אישור המתכנן לפני הרצת המחלצים**
- `brain extract build [--shards 4] [--batch-size 20]` → `data/batches/extract/shard-NN/NNN.in.json` + `status.json` + `MANIFEST.json`
- `brain extract merge` → צמתי `:Entity`, קשתות `MENTIONS{quote}` + יחסים עם provenance, `data/reports/extract.json`

## הכרעות מתכנן
1. **פורמט batch: JSON מודפס-יפה (indent=2), chunk אחד לשורה-קבוצה; ≤40KB לקובץ.** לקח משלב 04: כלי Read של הסוכן חותך שורה בודדת ב-~48KB. 20 chunks ל-batch, ואם עוברים 40KB — מפצלים. הסוכן קורא עם offset/limit לפי שורות.
2. **בחירת chunks (Phase A):** כל chunks של Documents `kind=KIP` שמוזכרים מהפרוסה (+ `ambiguous-kip`); chunks מסוג `description` של WorkItems אמיתיים באורך ≥300 תווים שהם Bug/Improvement/NewFeature **או** מפנים ל-KIP. **לא** comments, **לא** commits, **לא** סינתטי (Phase B).
3. **הקשר לכל chunk בקלט:** `parent_key`, `parent_kind`, `parent_title`, `position`, `kip_keys_referenced[]`, `text`. אין טקסט של chunks שכנים (חיסכון; overlap כבר קיים).
4. **סכמת פלט:** `entities[]{kind, name, description, quote, chunk_id}`, `relations[]{type, source, target, evidence_chunk_id, note}`; `source/target` = שם ישות מאותו batch **או** מפתח קיים (`KAFKA-…`, `KIP-…`, שם component). ולידציה: kinds/types בסט הסגור; `chunk_id` קיים ב-batch; `quote` ≤300 תווים ו-**מופיע מילולית** בטקסט ה-chunk (whitespace-normalized) — אחרת הישות נדחית (לא ה-batch כולו); Decision בלי MOTIVATED_BY/REJECTS → אזהרה + נשמר עם `weak=true`.
5. **מיזוג:** `Entity.id = kind|norm_name` (lowercase, ללא פיסוק, singular פשוט); `MENTIONS{quote, chunk_id}` Chunk→Entity; יחסים כקשתות Entity↔Entity/WorkItem/Document/Component עם `evidence_chunk_ids[]`, `batch_id`, `shard`, `model:"opus:kg-extractor"`, `extracted_at`. שם ישות שמתאים למפתח קיים (`KIP-848`, `KAFKA-…`) → קישור לצומת הקיים במקום Entity חדש. **0 קשתות LLM בלי provenance** (assert).
6. **כשל batch:** JSON לא תקין / סכמה → `retry/` (עד 2) → `quarantine/`; ישות בודדת פסולה → נדחית ונספרת. הדוח: תקינות ראשונה של batches, ישויות לפי kind, יחסים לפי type, נדחו לפי סיבה, Decision חלשים.
7. **בדיקת דיוק (מדגם):** `brain extract sample --n 50` מדפיס 50 `MENTIONS` אקראיים עם quote + טקסט הסביבה → המתכנן והמשתמש שופטים; ≥85% נתמכים = קבלה.
8. **מקביליות:** 4 shards; כל סוכן מקבל shard שלם ומעבד batch אחר batch; `status.json` אחרי כל batch.

## קריטריוני קבלה
- [ ] `schema.json` + `examples.md` אושרו ע"י המתכנן לפני הרצה.
- [ ] ≥90% מה-batches תקינים במעבר ראשון; 0 יחסים בלי provenance (בדיקה live).
- [ ] מדגם 50 → ≥85% נתמכים.
- [ ] ריצת merge חוזרת = 0 ישויות/קשתות חדשות.
- [ ] דוח מלא; `make check` + `make smoke` ירוקים (smoke: build+merge על 2 batches מהקורפוס-מיני עם פלט סוכן מדומה ב-fixtures).

## מה תלמד בשלב הזה
"סכמה קודם" הלכה למעשה: מה קורה כשהסט סגור (ולמה זה מוריד רעש), מה חילוץ טוב מפספס בכוונה, ואיך provenance ברמת quote הופך את הגרף לניתן-לביקורת — "למה המוח מאמין בזה".

## הערות למבצע
- הסוכן-מהנדס לא מריץ את המחלצים — המתכנן מפזר אותם אחרי אישור `examples.md`.
- אומדן: ~2.5k chunks / 20 = ~125 batches / 4 shards ≈ 31 לכל סוכן. אם `brain chunk` דיווח יותר — לדווח ולהמתין להכרעה על היקף לפני build.
