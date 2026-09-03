# Plan 1 — קורפוס → גרף (Corpus to Graph)

> **For agentic workers:** Execute task-by-task via subagent-driven development. Unlike Plan 0, this plan gives **briefs, contracts and acceptance criteria — not code**. The engineering agent designs and writes the code and tests itself (decision by the user, 2026-09-03: "the agents write the code, the planner plans and reviews").

**Goal:** Turn public Apache Kafka data (Jira, Confluence KIPs, git) plus a synthetic Xray/ADO layer into a Neo4j knowledge graph with full provenance, resolved entities, and community summaries — ready for retrieval (Plan 2).

**Inputs from the probe** (`docs/planning/probe-2026-09-03.md`, JSON alongside): 1,416 issues in `streams/connect/clients` 2023–2025; `/search` returns full changelog + comments (no per-issue fetch); `maxResults` cap 1000; `connect` is a reserved JQL word → quote it; 1,391 KIP pages, 98% carry a `KAFKA-\d+` key, `KAFKA-1` is a template placeholder → blacklist; git clone at `data/raw/git/kafka` (6,107 commits, 4,069 keyed, 99% with `(#PR)`); ADO has no anonymous work items → synthetic only; corpus estimate ~8k chunks scoped.

**Spec sections:** 2, 3 (all), 7. **Conventions:** `docs/agents/conventions.md`. **Progress:** `docs/planning/progress.md`.

**Rules for every task**
- Agent reads its definition in `.claude/agents/<name>.md`, the conventions, the spec sections cited, and the step brief in `docs/planning/steps/NN-*.md`.
- TDD; `make check` before every commit; `make smoke` when Neo4j/Ollama are touched. Network tests get a `network` pytest marker (add it) and are excluded by default like `live`.
- Every step is one idempotent `brain <step>` subcommand and writes `data/reports/<step>.json`.
- Exit report format from the conventions. Numbers must be real.
- Ambiguity → ask (NEEDS_CONTEXT). Never weaken an acceptance criterion.

---

## Task 1 — `brain harvest` (agent: `brain-ingest-engineer`) · lesson 01

**Goal:** Resilient connectors that pull the raw slice to disk once, resumably.

**Contract**
- `brain/harvest/base.py`: `Connector` protocol — `name`, `probe() -> ProbeResult`, `fetch(since: date | None, checkpoint: Checkpoint) -> Iterator[RawRecord]`; checkpoint persisted as `data/raw/<source>/checkpoint.json` after every page.
- `brain/harvest/jira.py`: JQL `project = KAFKA AND component in (streams, "connect", clients) AND created >= "2023-01-01" AND created <= "2025-12-31" ORDER BY created ASC`, `fields=*all` (or the explicit list from the probe) + `expand=changelog`, page 500, pacing ≥1s, retry with backoff on 429/5xx. Output `data/raw/jira/issues-<page>.json`.
- `brain/harvest/confluence.py`: CQL `space=KAFKA and type=page and title ~ "KIP-"` with `expand=body.storage,version,history,ancestors,metadata.labels`, limit 25 → **all** KIP pages to `data/raw/confluence/pages-<page>.json`. Convert nothing here (raw HTML stays).
- `brain/harvest/git.py`: reads the local clone (`data/raw/git/kafka`; clone it with the probe's command if missing), `git log --since=2023-01-01 --name-only --format=...` → `data/raw/git/commits.jsonl` with sha, author name/email, date, subject, body, files.
- `brain harvest [--source jira|confluence|git|all] [--since YYYY-MM-DD]`; `--since` narrows Jira/Confluence by `updated >=` for incremental runs.
- Report `data/reports/harvest.json`: per source counts, pages, duration, errors, and the **link-density table** per component (formal links %, KIP mention %, changelog present %) recomputed from the full pull.

**Acceptance**
- [ ] Jira: ≥1,400 issues, 100% with `changelog` present in the raw record, ≥95% with `fields.comment`.
- [ ] Confluence: ≥1,350 KIP pages with `body.storage`.
- [ ] git: ≥4,000 commits with an issue key; ≥98% of them with `(#N)`.
- [ ] Second run with existing checkpoints fetches 0 new pages (idempotent), and `--since` works.
- [ ] Unit tests with recorded fixtures (respx / small JSON): pagination, checkpoint resume, backoff on 429, reserved-word quoting. Network test marked `network`.
- [ ] Lesson draft 01 by `lesson-writer` after review.

**Course link:** module 3 "שלב 0: איכות הקלט", module 5 "עדכונים אינקרמנטליים".

---

## Task 2 — `brain canon` (agent: `brain-ingest-engineer`) · lesson 02

**Goal:** Map every raw source into the five canonical types with deterministic refs, and measure what the mapping loses.

**Contract**
- `brain/canon/mappers/{jira,confluence,git}.py` → `data/canonical/{workitems,documents,persons,changes,containers}.jsonl` via `brain/canon/io.py`.
- Confluence HTML → Markdown (pick a maintained library; pin it). `Document.key` = `KIP-N` parsed from title; `kind=KIP`.
- Persons: one `Person` per identity (`id = "<source>:<key>"`), identities from Jira (`name`, `displayName`), Confluence (`userKey`/`username`, `displayName`), git (`email`, `name`). Resolution is Task 7 — do not merge here.
- Containers: components, versions (fix/affects), Confluence space.
- Refs: `extract_refs` over title+description+comments (WorkItem), title+body (Document), message (Change) — then **filter issue refs by project-key allowlist** `{KAFKA}` (+ synthetic prefixes later) and **blacklist `KAFKA-1`**. Report how many refs the filter removed and the top removed "keys" (expect `UTF-8`, `SHA-256`…).
- Report `data/reports/canon.json`: counts per type, `unmapped_fields` per source (raw field names that were dropped), refs stats: via formal link vs via text, per source.

**Acceptance**
- [ ] Counts equal harvest counts (issues → WorkItems; pages → Documents; commits → Changes).
- [ ] Every WorkItem has ≥1 component; every Document has non-empty `body_md`; every Change has `at`.
- [ ] ≥20% of WorkItems carry a text-only issue ref (probe: ~27%); the filter report lists removed pseudo-keys.
- [ ] Golden-file tests: one raw sample per source → expected canonical record (`tests/fixtures/canon/`).
- [ ] Rerun overwrites identically (byte-equal outputs, use temp+rename in `write_jsonl` — carryover from Plan 0).

**Course link:** module 3 "ומה עם דאטה מובנה?".

---

## Task 3 — synthetic Xray/ADO layer (agents: `brain-ingest-engineer` for tooling; `synthetic-org-generator` ×3 for content) · lesson 03

**Goal:** Add the test-management and delivery layers a real org has, with **known** noise, so resolution and traceability can be measured against truth.

**Contract**
- Planner writes `brain/canon/synthetic_spec.md` (noise contract, ratios, key ranges) before this task starts.
- `brain synth build`: batches of ~40 real stories/bugs (+ referenced KIPs + person identities) → `data/batches/synthetic/<shard>/NNN.in.json`; 3 shards.
- Agents produce `NNN.out.json` per the generator definition.
- `brain synth merge`: validate (pydantic, key uniqueness, `synthetic=true`, ratios within ±5% of spec) → append to canonical JSONL; write `data/canonical/synthetic_truth.json` (identity map, text-only links, stale states).
- Report `data/reports/synth.json`: counts, ratio compliance, rejected batches.

**Acceptance**
- [ ] Tests cover ≥40% of stories/bugs; every KIP referenced by the slice has an ADO Epic; ≥300 ADO work items.
- [ ] All synthetic records validate; keys unique; ratios within tolerance; truth file complete.
- [ ] Batch failures go to `retry/` then `quarantine/` — never silently dropped.

**Course link:** module 9 "בניית סט הערכה משלכם" (truth by construction).

---

## Task 4 — `brain load` (agent: `brain-graph-engineer`) · lesson 04

**Goal:** The structured graph, built without any LLM, idempotently.

**Contract**
- `brain/graph/schema.py`: constraints + indexes from spec §2.4 (unique keys: `WorkItem.key`, `Document.key`, `Person.id`, `Commit.sha`, `Component.name`, `Version.name`, `Chunk.id`, `Community.id`); `brain load --schema-only` applies them.
- Loaders per canonical type with `UNWIND $rows MERGE …` in batches of 1000 via `GraphClient.write_batched`.
- Edges per spec §2.4: `REPORTED_BY`, `ASSIGNED_TO{valid_from,valid_to}` (derived from assignee changelog; open-ended = null), `IN_COMPONENT`, `FIX_VERSION`, `AFFECTS_VERSION`, `PARENT_OF`, `LINKS_TO{type}` (reciprocal Jira links → **one** edge), `REFERENCES{via: link|text}`, `TESTS`, `IN_PLAN`, `EXECUTED_IN`, `HAS_RUN{status}`, `AUTHORED`, `TOUCHES` (Commit→File), `RESOLVES` (Commit→WorkItem from refs), `HAS_CHANGE` → `StatusChange{field,from,to,at,by}` nodes.
- `Person` nodes per identity now (`id = source:key`, `resolved=false`).
- Report `data/reports/load.json`: nodes/edges by label/type, orphans (nodes with degree 0) by label, duplicates check, duration.

**Acceptance**
- [ ] `make smoke` includes loading `data/fixtures/mini/` into Neo4j and asserting expected counts + one traversal (Test→TESTS→WorkItem←RESOLVES←Commit).
- [ ] Full load: counts match canonical; **second run creates 0 nodes and 0 relationships**.
- [ ] Reciprocal links produce exactly one `LINKS_TO` per pair; `REFERENCES{via:text}` count ≥ probe expectation.
- [ ] Every Cypher projects explicitly (no bare `RETURN n`).

**Course link:** module 2 "Cypher מזורז" (MERGE pitfalls), module 3 "דאטה מובנה".

---

## Task 5 — `brain chunk` (agent: `brain-ingest-engineer`) · lesson 05

**Goal:** Text units + local embeddings + vector index, with measured throughput.

**Contract**
- Chunker: Documents by Markdown headings, target 500–800 tokens (approximate tokens = chars/4 unless a light tokenizer is justified), overlap ~50; WorkItem description = 1 chunk (split if >800); each comment = 1 chunk (author, at kept); commit message = 1 chunk. `Chunk.id = sha1(parent_key|position|text)`; properties: `parent_key, parent_kind, position, text, lang, char_len, hash`.
- Embedding via `OllamaEmbedder`; batch size chosen from a **measured** run on 200 real chunks (report tokens/s and s/batch); persist `Chunk.embedding`; `IndexMeta{name:"chunk_embedding", model, dim}` node; vector index `chunk_embedding` (cosine, dim from settings) + `HAS_CHUNK` edges.
- Re-run embeds only chunks whose hash is new.

**Acceptance**
- [ ] ~7–9k chunks (Phase A scope: all KIPs referenced by the slice, all issue descriptions, all comments, all keyed commits); report exact numbers.
- [ ] Vector index ONLINE; a Hebrew query ("פרוטוקול איזון מחדש של הצרכן") returns a rebalance-related English chunk in top-5 (sanity, in a live test).
- [ ] Second run embeds 0 chunks.
- [ ] Throughput table in the report; timeout chosen accordingly.

**Course link:** module 2 "אינדקסים וקטוריים ו-full-text", module 3 "שלב 0" (chunk quality).

---

## Task 6 — `brain extract` Phase A (agent: `brain-graph-engineer` builds tooling; `kg-extractor` ×4 run shards) · lesson 06

**Goal:** Schema-guided extraction from the highest-value text, merged with full provenance.

**Contract**
- `brain/extract/schema.json` (closed kinds/types from spec §2.4, output shape) and `brain/extract/examples.md` (3 worked examples: a KIP motivation chunk, a rejected-alternative chunk, an issue description) — drafted by the graph engineer, reviewed by the planner before agents run.
- `brain extract build`: select chunks — all KIP chunks + issue descriptions ≥300 chars that reference a KIP or are Bug/Improvement; 20–25 chunks per batch with parent context; 4 shards under `data/batches/extract/<shard>/`.
- Agents write `NNN.out.json` + `status.json`.
- `brain extract merge`: pydantic validation, unknown kinds/types rejected, chunk ids verified; `Entity` MERGE on `(kind, norm_name)`; `MENTIONS{quote}`; relation edges with `evidence_chunk_ids[]`, `batch_id`, `model`, `extracted_at`; retry/quarantine flow; report entities by kind, relations by type, invalid batch rate, quarantine list.

**Acceptance**
- [ ] ≥90% of batches valid on first pass; 0 LLM edges without provenance (assert in a live test).
- [ ] Sample check: 50 random `MENTIONS` printed with quotes for the planner/user; ≥85% judged supported.
- [ ] Every Decision has ≥1 MOTIVATED_BY/REJECTS (merge rejects others into the report as warnings).

**Course link:** module 3 "סכמה קודם", "LLMGraphTransformer/SimpleKGPipeline" (what we reimplement by hand and why).

---

## Task 7 — `brain resolve` (agent: `brain-graph-engineer`; `entity-adjudicator` ×2) · lesson 07

**Goal:** Merge duplicate people and entities, and measure it.

**Contract**
- Tier 1 deterministic: Person by email match, by `jira.name == git.name` normalized, by synthetic identity map hints only if they are *public* (no — truth is for evaluation only; do not use truth in resolution); Entity by `norm_name` within kind + alias table (`KIP-N` ↔ feature name from KIP title).
- Tier 2 embedding: `name + description` via `OllamaEmbedder`; cosine ≥0.92 same kind → auto; 0.80–0.92 → batches for adjudication.
- Tier 3 adjudicator batches → `same/different/unsure`.
- Apply: `SAME_AS` then `apoc.refactor.mergeNodes` keeping `aliases[]`, `merged_from[]`, set `resolved=true`.
- Gold: `data/eval/resolution_gold.jsonl` = synthetic identity truth (persons) + 100 entity pairs labeled by the adjudicator and spot-checked by the user (10).
- Report: duplicate rate before/after per kind, P/R/F1 on gold, counts per tier.

**Acceptance**
- [ ] P/R on gold ≥0.85 each (course target); if not met, report honestly and stop for the planner.
- [ ] No merge across kinds; every merged node keeps aliases; rerun is a no-op.

**Course link:** module 3 "איחוד ישויות — רוצח האיכות השקט".

---

## Task 8 — `brain communities` (agent: `brain-graph-engineer`; `community-summarizer` ×2) · lesson 08

**Goal:** Global-search substrate: hierarchical communities with cited reports.

**Contract**
- GDS projection: `Entity, WorkItem, Document, Component` with `MENTIONS, REFERENCES, LINKS_TO, IMPLEMENTS, DEPENDS_ON, IN_COMPONENT`; Leiden with 2 hierarchy levels; `Community{id, level, size, member_hash}` + `IN_COMMUNITY`.
- `brain communities build` → batches (members top-N by degree + evidence chunks) → agents → `brain communities merge` (reports with findings citing chunk ids; `Community.embedding` from summary via Ollama; re-summarize only when `member_hash` changed).
- Report: communities per level, size distribution, reports merged, findings without evidence (must be 0).

**Acceptance**
- [ ] 100–200 communities total; every report has ≥1 finding with ≥1 valid `evidence_chunk_id`.
- [ ] Vector index on `Community.embedding` ONLINE.

**Course link:** module 5 "פייפליין האינדוקס" (community reports), appendix LightRAG.

---

## Task 9 — `brain index` + Plan 1 gate (agent: `brain-graph-engineer`) · lesson 09

**Contract:** vector index on `Entity.embedding` (name+description), fulltext indexes on `WorkItem(title,description)`, `Document(title,body_md)`, `Entity(name,description)`; `data/reports/index.json` with the full graph census: counts per label/type, provenance coverage %, orphans %, resolution metrics, community stats, embedding meta.

**Plan 1 exit gate:** ≥1,400 issues, ≥190 KIPs embedded (all referenced), ≥4,000 commits, resolution P/R ≥0.85, 0 LLM edges without provenance, all indexes ONLINE, `make smoke` green, lessons 01–09 present, `progress.md` complete.

---

## Self-review (planner)
- Spec coverage: §3.1→T1, §3.2→T2+T3, §3.3→T4, §3.4→T5, §3.5→T6, §3.6→T7, §3.7→T8, §3.8→T9, §3.9 (incremental) → T1 `--since`, T5 hash-based re-embed, T8 `member_hash` (full incremental test is Plan 3).
- Plan 0 carryovers placed: allowlist + `KAFKA-1` (T2), reciprocal dedupe (T4), Person marker (T4/T7), `write_jsonl` temp+rename (T2), PR key with repo — single repo in this POC; `Change.id` stays sha / `pr:N`, documented as a known limitation in T2's report.
- No code in this plan by design; contracts name every file, command, property and report.
