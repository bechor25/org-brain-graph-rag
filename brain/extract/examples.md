# `brain extract` — three worked examples

Read this after `brain/extract/schema.json`. The schema says what is *allowed*; this says what is
*good*. All three examples are real chunks from the graph, and the outputs beside them are valid
against the schema with every quote verbatim in the text above it — `tests/test_extract_examples.py`
re-checks that on every run, so an example that drifts is a failing test rather than bad advice.

The `chunk_id`s below are the ids those chunks had when this file was written. **Always copy the
`chunk_id` out of your own batch**; never type one from here.

## The three rules that decide most cases

1. **Only what the text states.** No knowledge about Kafka you brought with you. If the chunk does
   not say it, it is not a fact of this chunk.
2. **The quote is the proof.** Verbatim, at most 300 characters, from the chunk you cite. Merge
   normalises whitespace and rejects the entity if the span is not there. A paraphrase is a rejected
   entity; a stitched-together quote is a rejected entity.
3. **Name things the way the text does.** `brain resolve` unifies "the group coordinator" with
   "GroupCoordinator" later, using evidence you do not have. Normalising early hides the collision
   from the step that is supposed to weigh it.

`MENTIONS` is not something you write: merge mints one `MENTIONS{quote}` edge from the chunk to every
entity you extract. An entity *is* its mention.

---

## 1. A KIP `Motivation` section — `KIP-1198`

### Input — one chunk of `shard-01/007.in.json`

```json
{
  "batch_id": "shard-01/007",
  "shard": "shard-01",
  "index": 7,
  "task": "extract",
  "phase": "A",
  "generated_at": "2026-09-06T00:00:00+00:00",
  "schema_path": "brain/extract/schema.json",
  "schema_sha256": "<sha256 of the schema this batch was built against>",
  "chunk_count": 1,
  "chunks": [
    {
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849",
      "parent_key": "KIP-1198",
      "parent_kind": "Document",
      "parent_title": "KIP-1198: implement a ConfigKey.Builder class",
      "position": 0,
      "kip_keys_referenced": [],
      "text": "# Motivation\n\nThe ConfigDef.define() methods, which create ConfigKey instances,  have increased the number of parameters past the point where a Builder is recommended.  The ConfigKey class is not final but extending it has become difficult.\n\nDeveloping additional functionality for ConfigKey requires changes to the current ConfigKey and ConfigDef classes.  Implementing a ConfigKey.Builder class will make development of new ConfigKey functionality easier because changes can be more localized.\n\n# Public interfaces\n\nAdds a new interface to construct the ConfigKey that may be used in parallel with the existing ConfigDef.define() methods.\n\n# Proposed Changes\n\nThis proposal adds a ConfigKey.Builder class that will construct ConfigKey instances equivalent to the ConfigDef.define() methods while allowing for extensibility.   This proposal does not remove any define() method nor mark them as deprecated.\n\nThe builder must:\n\n* Be extensible since ConfigKey is.\n* Be created by calling a static ConfigKey.builder(name), where name is the name of the config parameter.\n* Provide reasonable defaults as found in the current ConfigDef.define() methods.\n* during its build() method call a new constructor in ConfigKey that takes the Builder as an argument.\n\n# Compatibility, Deprecation, and Migration Plan\n\nThe result of the build() method is equivalent to the ConfigKey.defin() method, as such no compatibility issues are expected.\n\nWhile ConfigDef.define() could be deprecated, there are no plans to do so under this proposal.  If accepted, it is expected that the builder will be used in favor of the ConfigDef.define() method in future development due to ease of use.  Deprecation of ConfigDef.define() is left to a future KIP.\n\nNo migration is proposed.  Developers may utilize either the current ConfigDef.define() methods or the ConfigKey.builder() methods as they see fit.\n\nTest Plan\n\nUnit tests to verify that builder constructed ConfigKeys are equivalent to current ConfigKeys,define() method results.\n\nUnit tests to verify that the builder can be extended."
    }
  ]
}
```

### Expected output

```json
{
  "batch_id": "shard-01/007",
  "entities": [
    {
      "kind": "Problem",
      "name": "ConfigDef.define() has too many parameters",
      "description": "The define() methods have grown past the number of parameters a builder is meant to replace.",
      "quote": "have increased the number of parameters past the point where a Builder is recommended",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Problem",
      "name": "extending ConfigKey has become difficult",
      "description": "ConfigKey is not final, but subclassing it is hard in practice.",
      "quote": "The ConfigKey class is not final but extending it has become difficult.",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Problem",
      "name": "new ConfigKey functionality requires changing ConfigDef",
      "description": "Adding functionality to ConfigKey forces edits to both ConfigKey and ConfigDef.",
      "quote": "Developing additional functionality for ConfigKey requires changes to the current ConfigKey and ConfigDef classes.",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Decision",
      "name": "add a ConfigKey.Builder class",
      "description": "A builder is added that constructs ConfigKey instances equivalently to define().",
      "quote": "This proposal adds a ConfigKey.Builder class that will construct ConfigKey instances equivalent to the ConfigDef.define() methods while allowing for extensibility.",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Decision",
      "name": "do not deprecate ConfigDef.define()",
      "description": "define() stays as it is; deprecating it is left to a future KIP.",
      "quote": "While ConfigDef.define() could be deprecated, there are no plans to do so under this proposal.",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Feature",
      "name": "ConfigKey.Builder",
      "description": "A builder, created by ConfigKey.builder(name), that localises changes to ConfigKey.",
      "quote": "Be created by calling a static ConfigKey.builder(name), where name is the name of the config parameter.",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "kind": "Technology",
      "name": "ConfigDef.define()",
      "description": "The existing factory methods that create ConfigKey instances.",
      "quote": "The ConfigDef.define() methods, which create ConfigKey instances,",
      "chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    }
  ],
  "relations": [
    {
      "type": "DECIDES",
      "source": "KIP-1198",
      "target": "add a ConfigKey.Builder class",
      "evidence_chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "type": "DECIDES",
      "source": "KIP-1198",
      "target": "do not deprecate ConfigDef.define()",
      "evidence_chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "type": "MOTIVATED_BY",
      "source": "add a ConfigKey.Builder class",
      "target": "ConfigDef.define() has too many parameters",
      "evidence_chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    },
    {
      "type": "MOTIVATED_BY",
      "source": "add a ConfigKey.Builder class",
      "target": "new ConfigKey functionality requires changing ConfigDef",
      "evidence_chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849",
      "note": "the builder is proposed so changes stay localised"
    },
    {
      "type": "MOTIVATED_BY",
      "source": "add a ConfigKey.Builder class",
      "target": "extending ConfigKey has become difficult",
      "evidence_chunk_id": "4a770ef034fa1ea473bb5a94369de24bada2a849"
    }
  ],
  "notes": [
    "The Test Plan sentences ('Unit tests to verify that builder constructed ConfigKeys are equivalent') state no fact about the design and were not extracted."
  ]
}
```

### Why this output

This is the shape most of Phase A has: a section chunk that runs from `# Motivation` into the
next two headings. The Motivation states **Problems**; the Proposed Changes state a **Decision** and
the **Feature** it introduces; the Compatibility section states a second, smaller decision.

Four things to copy from this example:

* **One Decision, several MOTIVATED_BY.** The text gives three separate reasons; each is its own
  `Problem` with its own quote, and each gets its own edge. Merging them into "define() is awkward"
  would lose the answer to "why was the builder added".
* **`DECIDES` starts at the KIP.** `KIP-1198` is a key the graph already holds, so writing it as the
  `source` links the existing `Document`. You never mint a node for a key.
* **A Decision with no stated reason is still a Decision.** "do not deprecate ConfigDef.define()" has
  no `MOTIVATED_BY` — the text says what was decided, not why. Extract it anyway: merge marks it
  `weak = true` and the report counts it. Dropping it would lose a real decision; inventing a reason
  would be worse.
* **What is deliberately missing.** The Test Plan sentences ("Unit tests to verify that builder
  constructed ConfigKeys are equivalent…") describe how the KIP will be tested, not what it decides.
  They are noise in an entity graph, and the `notes` array is where you say you left them out.

---

## 2. A KIP `Rejected Alternatives` section — `KIP-1106`

### Input — one chunk of `shard-02/012.in.json`

```json
{
  "batch_id": "shard-02/012",
  "shard": "shard-02",
  "index": 12,
  "task": "extract",
  "phase": "A",
  "generated_at": "2026-09-06T00:00:00+00:00",
  "schema_path": "brain/extract/schema.json",
  "schema_sha256": "<sha256 of the schema this batch was built against>",
  "chunk_count": 1,
  "chunks": [
    {
      "chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194",
      "parent_key": "KIP-1106",
      "parent_kind": "Document",
      "parent_title": "KIP-1106: Add duration based offset reset option for consumer clients",
      "position": 6,
      "kip_keys_referenced": [
        "KIP-405",
        "KIP-932"
      ],
      "text": "# Rejected Alternatives\n\n* Add a separate config option to configure the duration like **[auto.offset.reset.by](http://auto.offset.reset.by).duration**\n* Add **earliest_local** reset strategyto automatically resets the offset to the earliest message stored in the local log on the broker. Initially we felt earliest_local can be useful in certain use cases. I think we can ignore it for now and add back when we have a clear use case.\n* Use config values of the format **minus-n-hours** , **minus-n-days**, **minus-n-months** and **minus-n-years.** As per the suggestions, It will be good to follow the standard like ISO-8601 format for defining duration."
    }
  ]
}
```

### Expected output

```json
{
  "batch_id": "shard-02/012",
  "entities": [
    {
      "kind": "Alternative",
      "name": "a separate auto.offset.reset.by.duration config option",
      "description": "Configuring the duration through its own config key instead of the existing reset option.",
      "quote": "Add a separate config option to configure the duration like **[auto.offset.reset.by](http://auto.offset.reset.by).duration**",
      "chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194"
    },
    {
      "kind": "Alternative",
      "name": "earliest_local reset strategy",
      "description": "A reset strategy that moves the offset to the earliest message still in the broker's local log.",
      "quote": "Add **earliest_local** reset strategyto automatically resets the offset to the earliest message stored in the local log on the broker.",
      "chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194"
    },
    {
      "kind": "Alternative",
      "name": "minus-n-hours style config values",
      "description": "Spelling the duration as minus-n-hours / minus-n-days rather than as ISO-8601.",
      "quote": "Use config values of the format **minus-n-hours** , **minus-n-days**, **minus-n-months** and **minus-n-years.**",
      "chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194"
    },
    {
      "kind": "Technology",
      "name": "ISO-8601",
      "description": "The duration format the KIP says it will follow instead.",
      "quote": "It will be good to follow the standard like ISO-8601 format for defining duration.",
      "chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194"
    }
  ],
  "relations": [
    {
      "type": "REJECTS",
      "source": "KIP-1106",
      "target": "a separate auto.offset.reset.by.duration config option",
      "evidence_chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194"
    },
    {
      "type": "REJECTS",
      "source": "KIP-1106",
      "target": "earliest_local reset strategy",
      "evidence_chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194",
      "note": "ignored for now; may come back when there is a clear use case"
    },
    {
      "type": "REJECTS",
      "source": "KIP-1106",
      "target": "minus-n-hours style config values",
      "evidence_chunk_id": "7e146484f15e68068c68f75d214adc6d506b7194",
      "note": "a standard format like ISO-8601 was preferred"
    }
  ],
  "notes": [
    "The chunk lists what was rejected without restating the decision, so the KIP itself is the source of REJECTS."
  ]
}
```

### Why this output

`Rejected Alternatives` is the highest-value section in the corpus: it is the only place that says
what was *not* done, and it answers the spec's question "what alternatives were rejected in KIP-932
and why".

* **Each bullet is one `Alternative`.** Three bullets, three entities, three quotes. A single
  "rejected alternatives" entity would be unusable.
* **The KIP is the source of `REJECTS`.** This chunk lists what was turned down without restating the
  decision, so `KIP-1106` — an existing `Document` — is the `source`. Spec 2.4 writes
  `REJECTS (Decision -> Alternative)`; that is the common case, not the only legal one, and merge
  accepts a Document there and counts the shape in the report.
* **`note` carries the "why".** "ignored for now; may come back when there is a clear use case" is the
  reason the text gives, and it lands on the edge. It is your own words, unlike `quote`.
* **`ISO-8601` is a `Technology`, not an `Alternative`.** It is the format the KIP says it will follow
  — the thing that won, mentioned inside a section about the things that lost. Kind is decided by
  what the sentence says the thing *is*, never by which heading it sits under.

---

## 3. A Bug description — `KAFKA-16046`

### Input — one chunk of `shard-03/004.in.json`

```json
{
  "batch_id": "shard-03/004",
  "shard": "shard-03",
  "index": 4,
  "task": "extract",
  "phase": "A",
  "generated_at": "2026-09-06T00:00:00+00:00",
  "schema_path": "brain/extract/schema.json",
  "schema_sha256": "<sha256 of the schema this batch was built against>",
  "chunk_count": 1,
  "chunks": [
    {
      "chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f",
      "parent_key": "KAFKA-16046",
      "parent_kind": "WorkItem",
      "parent_title": "Stream Stream Joins fail after restoration with deserialization exceptions",
      "position": 0,
      "kip_keys_referenced": [
        "KIP-954"
      ],
      "text": "Before KIP-954, the `KStreamImplJoin` class would always create non-timestamped persistent windowed stores. After that KIP, the default was changed to create timestamped stores. This wasn't compatible because, during restoration, timestamped stores have their changelog values transformed to prepend the timestamp to the value. This caused serialization errors when trying to read from the store because the deserializers did not expect the timestamp to be prepended."
    }
  ]
}
```

### Expected output

```json
{
  "batch_id": "shard-03/004",
  "entities": [
    {
      "kind": "Decision",
      "name": "default to timestamped windowed stores",
      "description": "KIP-954 changed the default store created for stream-stream joins to a timestamped one.",
      "quote": "After that KIP, the default was changed to create timestamped stores.",
      "chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    },
    {
      "kind": "Problem",
      "name": "serialization errors reading from the store after restoration",
      "description": "Deserializers do not expect the prepended timestamp, so reads fail after a restore.",
      "quote": "This caused serialization errors when trying to read from the store because the deserializers did not expect the timestamp to be prepended.",
      "chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    },
    {
      "kind": "Risk",
      "name": "timestamped stores are not restore-compatible with existing changelogs",
      "description": "During restoration a timestamped store prepends the timestamp to the changelog value.",
      "quote": "during restoration, timestamped stores have their changelog values transformed to prepend the timestamp to the value",
      "chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    },
    {
      "kind": "Technology",
      "name": "KStreamImplJoin",
      "description": "The class that creates the windowed stores behind a stream-stream join.",
      "quote": "the `KStreamImplJoin` class would always create non-timestamped persistent windowed stores",
      "chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    }
  ],
  "relations": [
    {
      "type": "DECIDES",
      "source": "KIP-954",
      "target": "default to timestamped windowed stores",
      "evidence_chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    },
    {
      "type": "INTRODUCES_RISK",
      "source": "default to timestamped windowed stores",
      "target": "timestamped stores are not restore-compatible with existing changelogs",
      "evidence_chunk_id": "a62c95f68242428fa61616f7c2a5715e06da062f"
    }
  ],
  "notes": []
}
```

### Why this output

Issue descriptions are shorter, denser and much more causal than KIP prose. This one is four
sentences and contains a decision, its risk and the failure it produced.

* **A work item can carry someone else's decision.** The decision here is KIP-954's; the bug is where
  it is described. `DECIDES` starts at `KIP-954` — again, an existing key, linked rather than minted.
  Note that `KIP-954` is also in this chunk's `kip_keys_referenced`, which is how you know the graph
  has it.
* **`Risk` is the mechanism, `Problem` is what went wrong.** The changelog transformation is the risk
  the change introduced; the serialization errors are the problem that resulted. Two entities, two
  quotes, and the causal chain is readable.
* **`weak = true` is the right outcome here.** The decision has an `INTRODUCES_RISK` but no
  `MOTIVATED_BY` and no `REJECTS`, because this chunk says what changed and not why. That is honest,
  and the report will show it.
* **No `IMPLEMENTS`.** `KAFKA-16046` is a bug report about a change, not an implementation of a
  feature. Precision over recall: an edge the text does not state is a wrong answer with provenance
  attached to it, which is worse than no answer.

---

## The rejections you will hit if you are careless

Merge rejects the single record, counts the reason, and merges the rest of the batch:

| reason | what you did |
|---|---|
| `quote_not_verbatim` | paraphrased, fixed a typo in the source, stitched two spans, or added an ellipsis |
| `quote_too_long` | over 300 characters — quote the sentence, not the section |
| `chunk_not_in_batch` | copied a `chunk_id` from another batch, or from this file |
| `unresolved_endpoint` | a relation naming something that is neither an entity of this batch nor a key the graph holds |
| `name_normalises_to_nothing` | a name that is only punctuation |

And it rejects the whole batch — `retry/`, then `quarantine/` after two more tries — for a broken
`batch_id`, a kind or type outside the closed set, an unknown field, or malformed JSON. Validate
against `brain/extract/schema.json` before you write, and write `status.json` after every batch.
