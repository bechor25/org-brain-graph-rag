# ADR-0003: embeddings מקומיים, עברית + אנגלית

**סטטוס:** מאושר (2026-09-03)

**החלטה:** `BAAI/bge-m3` דרך Ollama נייטיב על Mac (Metal), endpoint תואם-OpenAI `/v1/embeddings`. fallback: sentence-transformers בתהליך. אין API חיצוני ל-embedding לעולם.

**למה:** דאטה ארגוני ב-Ness הוא עברית+אנגלית; bge-m3 רב-לשוני, 1024-dim, 8k tokens; תואם לחוק NessBot "embeddings stay local". Docker על Mac ללא GPU → נייטיב.

**שימושים בגרף:** vector index על Chunk, entity linking לשאלה, entity resolution (דמיון שמות/תיאורים), שאלות בעברית מול קורפוס באנגלית (cross-lingual).
