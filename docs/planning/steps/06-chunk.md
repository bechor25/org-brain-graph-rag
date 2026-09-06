# שלב 06 — `brain chunk` (Plan 1, Task 5)

**תכנית:** Plan 1 · **סוכן מבצע:** `brain-ingest-engineer` · **מודול בקורס:** 2 ("אינדקסים וקטוריים ו-full-text — הגשר אל RAG"), 3 ("שלב 0: איכות הקלט" — איכות chunks)

## מטרה
יחידות טקסט + embeddings מקומיים + vector index, עם throughput **נמדד** על chunks אמיתיים — לא על משפטים קצרים.

## קלטים
- גרף טעון (שלב 05): צמתי `WorkItem`, `Document`, `Commit` עם הטקסטים; `data/canonical/*.jsonl` (comments עם author/at)
- `brain/embed/client.py` (`OllamaEmbedder`), `Settings.embed_model/embed_dim`
- Task 5 בתכנית; spec §3.4

## פלטים
- `brain/chunk/` (chunker + runner), פקודת `brain chunk [--kinds doc,issue,comment,commit] [--limit N] [--measure]`, צמתי `:Chunk` + `HAS_CHUNK`, vector index `chunk_embedding`, צומת `IndexMeta`, `data/reports/chunk.json`

## הכרעות מתכנן
1. **היקף Phase A:** כל ה-Documents מסוג `KIP` **שמוזכרים** מהפרוסה (ref `kip` מ-WorkItem/Commit) **וגם** ה-`ambiguous-kip` variants שלהם; כל `description` של WorkItem (אמיתי + סינתטי); כל comment; כל הודעת Commit עם ref issue/kip. Documents שאינם מוזכרים — **לא** בשלב זה (דגל `--all-docs` להרחבה עתידית).
2. **חיתוך:** Document לפי כותרות Markdown (`#`/`##`), יעד 500–800 tokens, overlap ~50 tokens, בלי לשבור code fences/טבלאות באמצע; אומדן tokens = `len(text)/4` (בלי tokenizer כבד — לתעד את הסטייה על 20 chunks מול tokenizer אמיתי אם זמין ב-Ollama `/api/tokenize`, אחרת להשאיר אומדן). description > 800 → פיצול לפי פסקאות. comment = chunk אחד (עם `author`, `at`). Commit message = chunk אחד.
3. **זהות:** `Chunk.id = sha1(parent_key|kind|position|text)`. Properties: `parent_key, parent_kind (Document|WorkItem|Commit), kind (section|description|comment|message), position, text, char_len, token_est, lang (he|en|other — heuristic), author?, at?, hash`.
4. **Embedding:** דרך `OllamaEmbedder` בלבד; **קודם `--measure`**: 200 chunks אמיתיים → tokens/s, s/batch לגדלי batch 8/16/32/64 → בוחרים batch + timeout מהמדידה, לתעד בדוח. `Chunk.embedding` (1024). `IndexMeta{name:"chunk_embedding", model, dim, created_at, chunk_count}`; vector index cosine.
5. **אינקרמנטלי:** chunk שכבר קיים עם אותו `hash` → לא מוטמע מחדש; chunk של parent שנעלם/השתנה → `orphaned=true` (לא נמחק בשלב זה). ריצה שנייה = 0 embeddings.
6. **Chunk טקסט ריק/קצר (<20 תווים)** — לא נוצר; נספר.
7. **בדיקת שפיות cross-lingual (live test):** שאילתה בעברית "פרוטוקול איזון מחדש של הצרכן" → top-5 chunks מכילים לפחות אחד עם `rebalance`. שאילתה באנגלית "consumer group rebalance protocol" → top-1 מ-KIP-848/848-related.

## קריטריוני קבלה
- [ ] דוח: מספר chunks לפי `kind`/`parent_kind`, התפלגות `token_est` (p50/p95/max), chunks שנדחו, זמן chunking, **טבלת throughput** (batch → tokens/s, s/batch), batch/timeout שנבחרו, זמן embedding כולל.
- [ ] כל Chunk מוטמע (0 ללא `embedding`); `IndexMeta` קיים; vector index ONLINE (`SHOW INDEXES`).
- [ ] ריצה שנייה: 0 chunks חדשים, 0 embeddings.
- [ ] בדיקת cross-lingual (7) עוברת ב-`make smoke`.
- [ ] בדיקות יחידה: chunker על Markdown עם כותרות/קוד/טבלה (לא שובר fence), overlap, id דטרמיניסטי, שפה. `make check` ירוק.

## מה תלמד בשלב הזה
למה chunking הוא החלטת איכות (הקורס: "צוואר הבקבוק האמיתי"), כמה עולה embedding מקומי על טקסט אמיתי (לא על משפטים), ואיך בונים אינדקס שאפשר לעדכן בלי לבנות מחדש.

## הערות למבצע
- אין LLM. embeddings רק דרך `brain/embed/client.py`. אי-התאמת מימד = שגיאה קשה.
- לכתוב לגרף רק דרך `GraphClient`; `UNWIND $rows MERGE` על `Chunk.id`; projection מפורש.
- אם Ollama איטי (צפוי ~5–10 chunks/s על 600 tokens): להריץ עם `--limit` קודם, לדווח אומדן זמן מלא, ורק אז להריץ מלא. לא לשנות `EMBED_MODEL`.
