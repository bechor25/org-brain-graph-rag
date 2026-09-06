# `tests/fixtures/extract` — a canned agent run over the mini corpus

`shard-01/001.in.json` and `002.in.json` are exactly what `brain extract build` writes for
`data/fixtures/mini` at `--shards 1 --batch-size 2 --min-chars 90` (the mini corpus has no
description over the real 300-character floor). The `.out.json` files beside them are what a
`kg-extractor` agent would answer — hand-written, valid against `brain/extract/schema.json`,
and quoting the input verbatim.

They exist so `make smoke` can run build + merge end to end without an agent. Between them
they cover: an entity whose name is an existing key (`KIP-5` → the Document), an entity whose
name is an existing component (`streams`), relations anchored on existing work items
(`KAFKA-100`, `KAFKA-101`), a Decision with `MOTIVATED_BY` + `REJECTS` (not weak) and a
Decision with neither (`weak = true`).

The `chunk_id`s are `sha1(parent_key|kind|position|text)` of the mini corpus, so they are
stable as long as the mini fixture and the chunker's splitting agree. If `brain chunk`
changes how it cuts `KIP-5`, `test_the_fixture_chunk_ids_are_still_in_the_mini_corpus` fails
and says so — regenerate the pair, do not edit the ids by hand.
