# Agent conventions — org-brain POC

Applies to every agent under `.claude/agents/`. Read before acting. The spec
(`docs/superpowers/specs/2026-09-03-org-brain-graph-rag-design.md`) is the source of truth;
the current step brief is in `docs/planning/steps/`.

## Roles
- Planner (Claude Fable session): writes briefs, reviews, decides, teaches the user.
- Engineering agents: write code + tests, run pipeline steps, write reports.
- LLM-role agents: are the pipeline's "language model". They read batch inputs and write
  batch outputs as JSON. They have NO shell and NO database access by construction.
- Quality agents: review and document.

## Iron rules
1. No agent writes to Neo4j except through `brain/graph/client.py` from engineering code.
   LLM-role agents never touch the DB (they cannot: no Bash tool).
2. No external LLM or embedding API. Embeddings only via `brain/embed/client.py` (Ollama).
3. Every LLM-derived node/edge carries provenance: `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at`.
   LLM-role agents emit only chunk ids; the deterministic merge code stamps `batch_id`, `model`, `extracted_at`.
4. Schema first: entity kinds and relation types are closed sets defined in the spec §2.4 and
   in `brain/<task>/schema.json`. Never invent new kinds; put the unexpected in `notes`.
5. Pins: dependencies are locked in `uv.lock`; do not upgrade without a brief that asks for it.
6. Language: code/comments/prompts/commits English; `README.md`, `docs/lessons/`, `docs/planning/` Hebrew.
7. Secrets only in `.env`. Never commit `.env` or `data/` (except `data/fixtures/`).

## Batch protocol (LLM-role agents)
- Inputs: `data/batches/<task>/<shard>/<NNN>.in.json`. Outputs: `<NNN>.out.json` next to them.
- Per-shard `status.json`: `{"done": [...], "failed": [{"batch": "...", "reason": "..."}]}` — update after every batch.
- Validate your own output against `brain/<task>/schema.json` before writing. If you cannot
  produce a valid output, write `failed` with a reason; never write a partial or invalid file.
- Never modify inputs. Never skip silently. Process batches in order; resume from `status.json`.
- Quote evidence verbatim from the input text; never paraphrase a quote.

## Reports
- Engineering step report: `data/reports/<step>.json` — counts, durations, warnings, errors.
- Exit report (in the final message, caveman-terse):
  ```
  DONE: <what>
  NUMBERS: <key counts/durations>
  DEVIATIONS: <anything not per brief, with why>
  OPEN: <questions/risks for the planner>
  FILES: <created/modified>
  ```

## Engineering workflow
- TDD: failing test → minimal code → pass → commit. Small commits, conventional messages.
- Run `make check` before every commit; `make smoke` before finishing a step that touches Neo4j/Ollama.
- Idempotent steps: rerunning a step must not duplicate data (`MERGE`, hashes, checkpoints).
- Never "fix" a failing acceptance criterion by weakening the test; report it instead.
- Parallel agents share one working tree: stage only your own paths (`git add <paths>`), never `git add -A`, never `git stash`, never `ruff format .` outside your paths, and retry on `index.lock`. If a file you must edit carries another agent's uncommitted hunk, keep it and say so in your commit message.
