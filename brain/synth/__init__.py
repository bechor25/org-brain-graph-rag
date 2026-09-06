"""The synthetic Xray/ADO layer (Plan 1 Task 3) — tooling, not content.

Public data has no test-management system and no delivery tracker. This package builds
the *batches* an LLM-role agent (`synthetic-org-generator`) turns into those two layers on
top of the real Kafka entities, and merges what comes back into `data/canonical/`.

Three commands' worth of responsibility, and one line that says why each exists:

* :mod:`brain.synth.build` — pick the real work items worth covering, attach everything a
  writer needs (the KIPs they cite, the people who touched them, their components and
  versions), and hand out **disjoint key ranges** so three agents can write in parallel
  without minting the same `XT-7` twice.
* :mod:`brain.synth.merge` — the only writer into `data/canonical/`. Validates, enforces
  global key uniqueness, rebuilds `synthetic_truth.json`, and is idempotent by
  construction: it rewrites the synthetic slice rather than appending to it.
* :mod:`brain.synth.ratios` — measures the merged layer against the noise contract. The
  ratios are *reported*, never enforced: a merge that refused content for being 6% off
  target would cost more than it saves, and the planner is the one who decides.

The noise contract itself is `brain/canon/synthetic_spec.md`. It is binding, it is copied
into every shard directory beside the batches, and its sha256 is recorded in the manifest
so a layer built against an older contract is visible rather than assumed.
"""
