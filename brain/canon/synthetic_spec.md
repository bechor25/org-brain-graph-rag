# Synthetic Xray / ADO layer — noise contract (`brain/canon/synthetic_spec.md`)

Purpose: add the two org layers public data lacks (test management, delivery tracking) on top of REAL
Kafka entities, with noise whose exact shape is known, so entity resolution and traceability can be
scored against truth. Every synthetic record has `synthetic: true`. Truth is written to
`data/canonical/synthetic_truth.json` and is used ONLY by evaluation — never by the pipeline.

## Keys and sources
- Xray: `source: "xray"`. Keys `XT-<n>` (type `Test`), `XP-<n>` (`TestPlan`), `XS-<n>` (`TestSet`), `XE-<n>` (`TestExecution`). Runs are expressed as `links[]{type:"executes", target: XT-n}` on the execution plus a `comments[]` line `"<XT-n>: PASS|FAIL (<reason>)"` and, for FAIL, a `links[]{type:"defect", target: <KAFKA-key>}` when a real bug in the same fix version exists.
- ADO: `source: "ado"`. Keys `ADO-<n>`; `type` ∈ Epic | Feature | User Story | Task | Bug. Containers: `kind: sprint` (`Sprint 2023-01` … monthly), `kind: area` (`Kafka\Streams`, `Kafka\Connect`, `Kafka\Clients`).
- Numbering is global and monotonic across batches (agent reads the last id from `status.json`).

## Coverage targets (per whole slice, ±5%)
| what | target |
|---|---|
| real stories/bugs with ≥1 Test | 45% |
| Tests per covered story | 1–3 (mean ~1.8) |
| TestPlans | one per real `fix_version` that has ≥5 covered issues |
| TestExecutions | one per (TestPlan, month with activity) |
| FAIL runs that name a real bug (`defect` link) | 70% of FAILs |
| ADO Epics | one per KIP referenced by the slice (title = KIP title, description paraphrased) |
| ADO Features | one per group of 3–8 real issues sharing component + fix version |
| ADO User Stories | one per real Improvement/New Feature issue (60% of them) |
| ADO Bugs | one per real Bug issue (40% of them) |
| ADO Tasks | 0–2 per Story |

## Noise (this is the point)
| noise | ratio | truth record |
|---|---|---|
| ADO Story ↔ Jira key given ONLY in description text (no `links[]`) | 35% of Stories | `text_only_links[] {ado_key, jira_key}` |
| ADO Story ↔ Jira key as formal link (`links[]{type:"related", target}`) | 65% | — |
| Test ↔ story: 20% of Tests reference the story only in `description` ("Covers KAFKA-…") without `links[]{type:"tests"}` | 20% | `text_only_links[] {test_key, jira_key}` |
| Same real person, different display form in ADO (`"Rao, Jun"`, `"jrao"`, `"J. Rao"`) | 100% of people used in ADO/Xray get a new identity `{source: ado|xray, key: <slug>, display: <variant>}`; 30% use the "Last, First" form, 30% username-like, 40% initial form | `identity_map {ado_or_xray_key: jira_username}` |
| Stale state: ADO item still `Active` although the linked Jira issue is `Resolved`/`Closed` | 15% of linked Stories/Bugs | `stale_states[] {ado_key, jira_key, ado_status, jira_status}` |
| Slightly different feature naming between Xray Test titles and Jira summaries (synonym/abbrev, e.g. "GroupCoordinator" vs "group coordinator", "KIP-848 protocol" vs "new consumer protocol") | 30% of Tests | `renames[] {test_key, jira_key, test_phrase, jira_phrase}` |
| Duplicate Test (same steps, different key, created 2 months apart) | 5% of Tests | `duplicate_tests[] {a, b}` |

## Content rules
- Test `description`: numbered steps derived from the real issue text; expected result explicit. 60–200 words.
- Execution dates fall within the fix version's release window (use real `created`/`updated` of linked issues).
- Never invent emails. Never create people that do not exist in the real identities list.
- `reporter`/`assignee` on synthetic items use the synthetic identity keys (so resolution has work to do).
- Every record must validate against `brain/canon/models.py`.
