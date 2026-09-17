# Judge rubric — layer 3 of the evaluation (spec §5.2, §5.4)

You are scoring **answers**, not retrievals and not agents. Every case was produced by a
retrieval strategy whose name has been replaced by a random label, and the map back to that
name is deliberately kept out of your reach. If you find yourself reasoning about *which*
strategy wrote something, you have left the task.

Read this file, then `brain/eval/judge_schema.json`, then your batch.

## What a case gives you

| field | what it is |
|---|---|
| `case_id` | an opaque label. It carries no meaning. |
| `question` | the question that was asked, in its own language |
| `lang` | `en` or `he` — the language the answer was required to be in |
| `gold_answer` | the known-correct answer, derived from the graph or from the synthetic truth file |
| `gold_evidence` | the keys / chunk ids that hold that answer |
| `context_items` | **exactly** what the answering agent was given. It had no tools and no memory beyond this. |
| `answer` | what it wrote |
| `cited_keys` | the keys it says it cited |
| `context_available` | when `false`, no packed context was recorded (an agentic run). Faithfulness is then `null`. |

The answering agent could not look anything up. So "the answer is right but the context does
not say it" is a real and important verdict: it means the model knew Kafka, not that the
retrieval worked. Score it as **correct and unfaithful**, and the report will say so.

## The four metrics, 0–2

### faithfulness — is every claim supported by `context_items`?

| score | when |
|---|---|
| 2 | every factual claim is stated by, or follows directly from, an item in `context_items`. |
| 1 | the main claim is supported, but at least one secondary claim is not in the context (a date, a name, a count, a causal link the context does not make). |
| 0 | the central claim is not in the context at all — including a claim that happens to be *true* about Kafka. |

`unsupported_claims[]` is the evidence for this score: quote each unsupported claim verbatim
from the answer. A faithfulness below 2 with an empty list is reported as unevidenced.

A refusal ("not in context", "לא נמצא בהקשר") is **faithful**: it claims nothing. Score it 2
here, and let `correctness` and `relevancy` say what it cost.

`context_available: false` → faithfulness is `null`. Do not guess a context you were not given.

### correctness — does it agree with `gold_answer` on the facts?

| score | when |
|---|---|
| 2 | every fact the gold answer states that the question asked for is in the answer, and nothing in the answer contradicts the gold. |
| 1 | partial: some of the asked-for facts, or the right facts with one wrong detail, or the right answer hedged into uselessness. |
| 0 | it contradicts the gold, or it answers a different question, or it refuses. |

Compare **facts, not wording**, and not order, and not language. The gold answer is often a
generated sentence listing keys ("3 test(s) cover KAFKA-14649: XT-10007, XT-10008,
XT-10009"); an answer that names the same three tests in prose is a 2. An answer that names
two of the three is a 1. An answer that names a fourth test the gold does not is a 1, not a
2 — and quote that fourth key in your justification.

The gold answer sometimes ends with "N further … are not listed". The answer is not required
to list them; it is required not to contradict the count.

### citation_validity — do the brackets hold up?

| score | when |
|---|---|
| 2 | every citation names an item that is in `context_items`, and each one supports the sentence it follows. |
| 1 | the citations exist in the context but at least one is attached to a sentence it does not support, or a factual sentence carries no citation at all. |
| 0 | a cited key is not in `context_items` at all, or the answer makes factual claims with no citations anywhere. |

A refusal with no citations is not a 0 — it makes no claim to cite. Score it 2.

This metric is also computed deterministically in code, against the context and against the
graph. Your score and the code's are compared in the report, and a disagreement is a finding
about the rubric, not about you — so score what you see and do not try to predict the code.

### relevancy — does it answer *this* question?

| score | when |
|---|---|
| 2 | it answers what was asked, at the asked-for scope, with nothing padded in. |
| 1 | it answers around the question: the right subject, the wrong aspect; or the answer is buried in material nobody asked for. |
| 0 | it answers a different question, or it says nothing usable — including a refusal. |

## Rules that apply to every score

- **Never reward length.** A three-line answer that names the two right keys beats a page
  that names them among ten. If you are tempted to reward thoroughness, check whether the
  extra material is in `gold_answer`.
- **Never reward confidence.** "Clearly, X" and "X" are the same claim.
- **Judge Hebrew and English by the same rubric.** Identifiers (`KAFKA-…`, `KIP-…`, `XT-…`,
  commit shas) stay Latin in a Hebrew answer; that is correct, not a language error. An
  answer written in the wrong language is still scored on all four metrics — set
  `answer_language_ok: false` and say so in the justification.
- **A quoted justification is required.** Every `justification` contains at least one
  verbatim quotation in double quotes: from the answer when you mark it down, from the
  context or the gold when you say what is missing. Copy the words exactly — never
  paraphrase inside quotation marks. Merge checks for the quotation and reports its absence.
- **Score the case in front of you.** Do not compare it with a case you scored earlier, do
  not average across a batch, and do not let a batch's first weak answer set your bar.

## Pairwise cases

A pairwise case gives you one question, its gold answer, and two answers labelled `a` and
`b`. **The order was randomised** — it is not baseline-then-graph, or best-then-worst, and
it changes from case to case.

Pick the one that serves the asker better, judged against `gold_answer` and the question:

1. facts first — the answer that carries more of the gold facts with fewer wrong ones wins;
2. then citations — when the facts tie, the answer whose claims are cited wins;
3. then directness — when both tie, the answer that answers without padding wins;
4. `tie` when none of the three separates them.

Set `both_wrong: true` when neither answer contains the gold fact. A tie between two right
answers and a tie between two wrong ones are opposite results, and the report keeps them
apart only if you say which one this is.

Never pick a winner on length, tone, formatting, or on how many items it mentions.

## What to do when you cannot score

Write the batch's `status.json` entry as `failed` with a reason, and write no `.out.json`
for it. Never write a partial file, and never invent a score to fill a slot — an invented
score becomes a number in a report that the planner will read as a measurement.
