"""The few-shot bank S4 writes from, and the tooling that refuses to trust it.

Text2Cypher without examples invents `(:Issue)-[:HAS_TEST]->(:TestCase)` — a perfectly
reasonable schema for some other company. The fix is few-shot: three to five *working*
queries per question type, taken from this graph, so the model copies patterns that exist
instead of patterns that sound right.

The important word is *working*. An example bank written by a model and accepted on faith
is a bug generator with citations: every future query inherits its mistakes. So the bank
here is a pipeline, not a file:

    brain cypher-examples build   → data/batches/cypher/001.in.json   (schema + questions)
    (the planner dispatches `cypher-author`, which writes 001.out.json)
    brain cypher-examples merge   → each answer through the guard, then *run* against the
                                    live graph, and accepted only if it returns ≥1 row

The hand-written examples (`SEED_EXAMPLES`, at least one per question type) do double
duty: they are the style guide inside the batch, and they are the bank on a machine where
the batch has never been run — `data/` is not in git, so a bank that lived only there would
make S4 unusable on a fresh clone. They are also the *reference* answer: an author's answer
that repeats a seed's query or re-answers a seed's question is recorded as a duplicate
rather than accepted, because a few-shot bank is a set of patterns and the same pattern
twice buys nothing while a second, subtly different implementation of the same question is
how two tools of one server end up disagreeing.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.common import stamp
from brain.retrieve import cypher_guard as guard
from brain.retrieve.keys import find_keys
from brain.retrieve.schema import compact_schema, get_schema

#: The four types S4 serves. `global` (spec §2.3's fifth group) is S5's job: a community
#: report is not an aggregation, and asking for Cypher that returns "the main themes"
#: invites a query that counts words.
QUESTION_TYPES: tuple[str, ...] = ("traceability", "impact", "rationale", "temporal")

TYPE_DESCRIPTIONS: dict[str, str] = {
    "traceability": "who/what connects two systems — tests to issues, commits to KIPs, "
    "people to components; usually an aggregation over a join",
    "impact": "what a change touches — open issues, failing tests, dependent entities, "
    "usually filtered by component or version",
    "rationale": "why something was decided — DECIDES/REJECTS/MOTIVATED_BY with the "
    "evidence quote and chunk id that support it",
    "temporal": "a point in time or an interval — StatusChange for status at a date, "
    "ASSIGNED_TO.valid_from/valid_to for ownership over time, FIX_VERSION for releases",
}

DEFAULT_BANK = Path("data/eval/cypher_examples.jsonl")
#: The merge's own step report (conventions: `data/reports/<step>.json`). Written by the
#: merge rather than reconstructed later: "why was cq09 refused" is knowable at validation
#: time and unknowable afterwards, because the bank holds only what was accepted.
MERGE_REPORT = Path("data/reports/cypher_examples.json")
BATCH_DIR = Path("data/batches/cypher")
BATCH_NAME = "001"
#: The brief's ceiling for the batch input. Kept as a hard check, not a hope.
MAX_BATCH_BYTES = 40_000
#: Plan: 3–5 examples per type.
MAX_PER_TYPE = 5
#: Rows an example may return while being validated. An example is a *pattern*, not a
#: dataset, and a validation that pulls 10k rows is measuring the wrong thing.
VALIDATION_LIMIT = 25
#: Plan: 3–5 per type. Below three, `brain cypher-examples merge` exits non-zero — and it
#: counts *unique* examples, because five copies of one query are one example.
MIN_PER_TYPE = 3
#: Outcomes decided after an answer has already been validated against the live graph.
#: They are not answers that failed; conflating them inflates the "answers" count.
POST_VALIDATION_REASONS: frozenset[str] = frozenset({"type-full", "duplicate", "duplicate-of-seed"})


def head_sha() -> str | None:
    """The commit a measurement was taken on.

    Plan 1's closing review made this a convention: a number carried forward without the
    sha it was measured on is STALE, not evidence. The git call itself lives in
    `brain/common/stamp.py` — stdlib only, so importing it drags in no pipeline step; this
    is the repo it asks about, which is the checkout the bank was validated against and not
    whatever directory the CLI happened to be run from.
    """
    return stamp.head_sha(Path(__file__).resolve().parents[2], default=None)


#: One hand-written, live-verified example per type. Written against the real graph, with
#: the anchors the competency questions actually use.
SEED_EXAMPLES: tuple[dict[str, Any], ...] = (
    {
        "id": "seed-traceability-01",
        "type": "traceability",
        "lang": "en",
        "question": "Who owns component `streams` (most assignments and commits)?",
        "cypher": """MATCH (c:Component {name: $component})<-[:IN_COMPONENT]-(w:WorkItem)
OPTIONAL MATCH (w)-[:ASSIGNED_TO]->(p:Person)
WITH p, count(DISTINCT w) AS assigned_items
WHERE p IS NOT NULL
OPTIONAL MATCH (p)-[:AUTHORED]->(commit:Commit)-[:RESOLVES]->(:WorkItem)
      -[:IN_COMPONENT]->(:Component {name: $component})
RETURN p.display AS person, p.id AS person_id, assigned_items,
       count(DISTINCT commit) AS commits,
       assigned_items + count(DISTINCT commit) AS score
ORDER BY score DESC, person
LIMIT 10""",
        "params": {"component": "streams"},
        "explanation": "Ownership is two facts, not one: who is assigned the component's "
        "work and who authored the commits that resolved it. Both are aggregated per "
        "person and summed, so a reviewer can see which half carries the answer.",
        "uses_labels": ["Component", "WorkItem", "Person", "Commit"],
        "source": "hand",
    },
    {
        "id": "seed-traceability-02",
        "type": "traceability",
        "lang": "en",
        "question": "Which ADO story delivers KIP-848?",
        "cypher": """MATCH (d:Document {key: $key})<-[:REFERENCES|IMPLEMENTS]-(w:WorkItem)
WHERE w.source = $source
OPTIONAL MATCH (w)<-[:PARENT_OF]-(parent:WorkItem)
RETURN w.key AS key, w.title AS title, w.type AS type, w.status AS status,
       collect(DISTINCT parent.key) AS parents
ORDER BY key
LIMIT 10""",
        "params": {"key": "KIP-848", "source": "ado"},
        "explanation": "Two identifier spaces meet on one document, so the side you were "
        "asked about has to be named: `w.source` is `jira`/`xray`/`ado` (schema "
        "`sample_values`), and without that filter the query answers with whatever 25 rows "
        "it reaches first — the Jira half included. `PARENT_OF` is optional because an "
        "epic has no parent and dropping it would drop the epic.",
        "uses_labels": ["Document", "WorkItem"],
        "source": "hand",
    },
    {
        "id": "seed-impact-01",
        "type": "impact",
        "lang": "en",
        "question": "Which components have failing tests tied to open bugs in 3.8?",
        "cypher": """MATCH (v:Version)<-[:FIX_VERSION|AFFECTS_VERSION]-(b:Bug)
      -[:IN_COMPONENT]->(c:Component)
WHERE v.name STARTS WITH $version
  AND NOT b.status IN ['Resolved', 'Closed', 'Done', 'Completed', 'Removed']
OPTIONAL MATCH (t:Test)-[:TESTS]->(b)
OPTIONAL MATCH (t)<-[run:HAS_RUN]-()
WITH c, b,
     CASE WHEN t IS NOT NULL AND run.status = 'FAIL' THEN t END AS failing_test,
     CASE WHEN t IS NOT NULL THEN t END AS test
RETURN c.name AS component, count(DISTINCT b) AS open_bugs,
       count(DISTINCT test) AS tests, count(DISTINCT failing_test) AS failing_tests,
       collect(DISTINCT b.key)[0..5] AS bug_keys
ORDER BY failing_tests DESC, open_bugs DESC, component
LIMIT 10""",
        "params": {"version": "3.8"},
        "explanation": "`Version.name` is a full semver (`3.8.0`, `3.8.1`) while questions "
        "name the minor series, so the filter is `STARTS WITH`. Open is defined by "
        "exclusion because the corpus has nine status values across Jira and ADO. A test "
        "is failing only when a run says so — `t IS NOT NULL AND run.status = 'FAIL'`, and "
        "`HAS_RUN.status` is `PASS`/`FAIL` per the schema's `sample_values`. Counting "
        "`run IS NULL` as failing (the previous shape) called every never-executed test a "
        "failure: it reported 4 failing tests for `clients` where the honest answer is 0. "
        "The condition sits in the aggregation rather than in a `WHERE`, so a component "
        "with open bugs and no failing test still appears — with a zero, which is the "
        "answer, instead of vanishing from it.",
        "uses_labels": ["Version", "Bug", "Component", "Test"],
        "source": "hand",
    },
    {
        "id": "seed-rationale-01",
        "type": "rationale",
        "lang": "en",
        "question": "What alternatives were rejected in KIP-796 and why?",
        "cypher": """MATCH (d:Document {key: $key})-[r:REJECTS]->(e:Entity)
OPTIONAL MATCH (chunk:Chunk {parent_key: $key})-[m:MENTIONS]->(e)
RETURN e.name AS alternative, e.kind AS kind, r.note AS note,
       head(collect(m.quote)) AS quote, head(collect(chunk.id)) AS chunk_id
ORDER BY alternative
LIMIT 20""",
        "params": {"key": "KIP-796"},
        "explanation": "A rationale answer without a quote is an assertion. The optional "
        "`MENTIONS` hop from a chunk of the same document is what turns the rejected "
        "alternative into a citation the reader can check.",
        "uses_labels": ["Document", "Entity", "Chunk"],
        "source": "hand",
    },
    {
        "id": "seed-temporal-01",
        "type": "temporal",
        "lang": "en",
        "question": "What was the status of KAFKA-15538 on 2024-01-16?",
        "cypher": """MATCH (w:WorkItem {key: $key})-[:HAS_CHANGE]->(s:StatusChange)
WHERE s.field = 'status' AND s.at < datetime($date) + duration('P1D')
RETURN w.key AS key, s.to AS status, toString(s.at) AS changed_at, s.by AS changed_by
ORDER BY s.at DESC
LIMIT 1""",
        "params": {"key": "KAFKA-15538", "date": "2024-01-16"},
        "explanation": "Status at a date is the last change up to the *end of that day*, "
        "not the current status and not the state at its midnight: filter `s.at < "
        "datetime($date) + duration('P1D')`, order descending, take one. `datetime($date)` "
        "alone is midnight, which silently answers 'what was it when the day began' — on "
        "KAFKA-15538 / 2024-01-16 that returns `Resolved` while `status_at` (S6, which uses "
        "the end of the day) returns `Reopened`. Two tools of the same server disagreeing "
        "about the same fact is worse than either answer.",
        "uses_labels": ["WorkItem", "StatusChange"],
        "source": "hand",
    },
)


# --------------------------------------------------------------------------- the bank


def read_bank(path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else DEFAULT_BANK
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_bank(examples: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else DEFAULT_BANK
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in examples)
    target.write_text(body, encoding="utf-8")
    return target


def cypher_examples(
    question_type: str | None = None,
    *,
    path: Path | None = None,
    limit: int = MAX_PER_TYPE,
) -> list[dict[str, Any]]:
    """The few-shot examples for one question type (all of them when `question_type` is None).

    Falls back to the hand-written seeds for a type the bank has nothing for — an empty
    example list is the failure mode that makes a model invent a schema, so the tool never
    returns one while a working example exists in the source tree.
    """
    if question_type is not None and question_type not in QUESTION_TYPES:
        raise ValueError(f"question_type must be one of {QUESTION_TYPES}, got {question_type!r}")
    bank = read_bank(path) or list(SEED_EXAMPLES)
    if question_type is None:
        return bank[: limit * len(QUESTION_TYPES)]
    chosen = [e for e in bank if e.get("type") == question_type]
    if not chosen:
        chosen = [e for e in SEED_EXAMPLES if e["type"] == question_type]
    return chosen[:limit]


# --------------------------------------------------------------------------- parameters


def _components(ctx: Any) -> dict[str, str]:
    rows = ctx.read(f"MATCH (c:{ctx.label('Component')}) RETURN c.name AS name")
    return {r["name"].casefold(): r["name"] for r in rows if r.get("name")}


def bind_params(ctx: Any, question: str, example: dict[str, Any]) -> dict[str, Any]:
    """Re-point an example's parameters at what *this* question names.

    An example is a query plus the anchors that proved it works. Reusing it for a new
    question means keeping the query and swapping the anchors — a component name the graph
    actually holds, the keys and versions and dates the sentence contains — and keeping the
    example's own value for anything the question does not mention, so the query still runs.
    """
    defaults = dict(example.get("params") or {})
    keys = find_keys(question)
    bound = dict(defaults)
    known: dict[str, str] | None = None

    for name in defaults:
        lowered = name.lower()
        if lowered in ("key", "issue", "issue_key", "workitem", "item_key") and (
            keys.workitems or keys.documents
        ):
            bound[name] = (keys.workitems or keys.documents)[0]
        elif lowered in ("kip", "document", "doc_key") and keys.documents:
            bound[name] = keys.documents[0]
        elif lowered in ("component", "component_name"):
            if known is None:
                known = _components(ctx)
            match = _component_in(question, known)
            if match:
                bound[name] = match
        elif lowered in ("version", "version_high", "v2") and keys.versions:
            bound[name] = keys.versions[-1]
        elif lowered in ("version_low", "v1") and keys.versions:
            bound[name] = keys.versions[0]
        elif lowered in ("date", "at", "as_of") and keys.dates:
            bound[name] = keys.dates[0]
        elif lowered in ("entity", "name", "term") and keys.quoted:
            bound[name] = keys.quoted[0]
    return bound


def _component_in(question: str, known: dict[str, str]) -> str | None:
    keys = find_keys(question)
    for candidate in keys.quoted:
        if candidate.casefold() in known:
            return known[candidate.casefold()]
    for token in question.replace("?", " ").replace(",", " ").split():
        cleaned = token.strip("`'\"“”‘’.,;:()[]").casefold()
        if cleaned in known:
            return known[cleaned]
    return None


# --------------------------------------------------------------------------- the batch

INSTRUCTIONS: tuple[str, ...] = (
    "Write one read-only Cypher query per question, using ONLY the labels, relationship "
    "types and properties in `schema`.",
    "Project explicitly (`RETURN p.display AS person`), never `RETURN n`: the driver "
    "returns rows, not nodes, and an unprojected node arrives as an unlabelled dict.",
    "End every query with LIMIT (<= 50, or 1 for a point-in-time answer).",
    "Parameterise the anchors ($key, $component, $version, $date) and put a working "
    "binding in `params` — the merge step RUNS your query and rejects it unless it "
    "returns at least one row.",
    "No write clause and no procedure outside `schema.allowed_procedures`: the guard "
    "refuses CREATE/MERGE/DELETE/SET/REMOVE/DROP/FOREACH/LOAD CSV, every CALL {} "
    "subquery, and every apoc/gds call that is not in the allowlist.",
    "Rationale answers must return an evidence quote and a chunk id; an answer without "
    "provenance cannot be cited.",
    "Hebrew questions get the same Cypher as their English twin — translate the question, "
    "never the identifiers.",
    "If a question cannot be answered with this schema, return `cypher: null` and say "
    "what is missing in `explanation`.",
)

OUTPUT_CONTRACT: dict[str, Any] = {
    "path": "data/batches/cypher/001.out.json",
    "shape": {
        "batch_id": "cypher-001",
        "answers": [
            {
                "question_id": "cq03",
                "type": "one of: " + ", ".join(QUESTION_TYPES),
                "question": "the question this answers (copy it)",
                "cypher": "the read-only query, or null",
                "params": {"component": "streams"},
                "explanation": "why this shape answers the question, in one or two sentences",
                "uses_labels": ["Component", "Person"],
                "confidence": 0.0,
            }
        ],
    },
}


def batch_payload(
    schema: dict[str, Any],
    questions: list[dict[str, Any]],
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything `cypher-author` needs and nothing it does not — schema, types, questions."""
    return {
        "batch_id": f"cypher-{BATCH_NAME}",
        "task": "cypher-examples",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "instructions": list(INSTRUCTIONS),
        "question_types": [
            {"name": name, "description": TYPE_DESCRIPTIONS[name], "wanted_examples": "3-5"}
            for name in QUESTION_TYPES
        ],
        "schema": schema,
        "style_examples": [
            {k: v for k, v in example.items() if k != "source"}
            for example in (examples if examples is not None else SEED_EXAMPLES)
        ],
        "questions": questions,
        "output_contract": OUTPUT_CONTRACT,
    }


def _questions_for_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The competency questions, with the anchors the graph itself chose (plan decision 7)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        question = {
            "question_id": row["id"],
            "type": row.get("type", ""),
            "lang": row.get("lang", "en"),
            "question": row.get("question", ""),
            "anchors": row.get("anchors", []),
            "expected_strategy": row.get("expected_strategy", ""),
        }
        # Only the questions S4 cannot serve carry a note; an empty key on every row is
        # 19 lines of the size budget spent saying nothing.
        if row.get("type") not in QUESTION_TYPES:
            question["note"] = (
                "answered by S5 (community reports); write Cypher only if it genuinely "
                "aggregates — otherwise return cypher: null"
            )
        out.append(question)
    return out


def build_batch(
    ctx: Any,
    *,
    questions_path: Path | None = None,
    path: Path | None = None,
    max_bytes: int = MAX_BATCH_BYTES,
) -> tuple[dict[str, Any], Path]:
    """Write `data/batches/cypher/001.in.json`. Raises if it would exceed the size cap."""
    from brain.retrieve import competency

    rows = competency.read(questions_path)
    if not rows:
        raise ValueError(
            "no competency questions — run `brain competency` first "
            f"({questions_path or competency.DEFAULT_PATH} is empty)"
        )
    payload = batch_payload(compact_schema(get_schema(ctx)), _questions_for_batch(rows))
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    size = len(body.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"batch is {size} bytes, over the {max_bytes} cap — trim the schema")
    target = Path(path) if path is not None else BATCH_DIR / f"{BATCH_NAME}.in.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return payload, target


# --------------------------------------------------------------------------- the merge


def _batch_questions(batch_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(batch_dir.glob("*.in.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for question in payload.get("questions", []):
            out[question.get("question_id", "")] = question
    return out


def validate_example(
    ctx: Any,
    answer: dict[str, Any],
    *,
    question: dict[str, Any] | None = None,
    limit: int = VALIDATION_LIMIT,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Guard it, run it, demand a row. Returns (accepted example, rejection) — one is None.

    Running the example is the point. A query that parses, passes the guard and returns
    nothing is worse than no example: the model will copy its shape and every answer built
    from it will be empty for a reason nobody can see.
    """
    qid = str(answer.get("question_id") or answer.get("id") or "?")
    cypher = answer.get("cypher")
    kind = answer.get("type") or (question or {}).get("type") or ""

    def reject(reason: str, detail: str = "") -> tuple[None, dict[str, Any]]:
        return None, {"question_id": qid, "reason": reason, "detail": detail, "type": kind}

    if not cypher:
        return reject("no-cypher", str(answer.get("explanation", ""))[:200])
    if kind not in QUESTION_TYPES:
        return reject("unknown-type", kind)
    try:
        guard.check(cypher)
    except guard.GuardError as err:
        return reject(err.reason, err.detail)

    text = str(answer.get("question") or (question or {}).get("question") or "")
    params = answer.get("params")
    if not isinstance(params, dict) or not params:
        params = bind_params(ctx, text, {"params": _params_hint(cypher)})
    try:
        result = guard.run_cypher(ctx, cypher, params, limit=limit, question=text, log=False)
    except guard.GuardError as err:
        return reject(err.reason, err.detail)
    if not result.items:
        return reject("no-rows", "the query ran and returned nothing")

    return {
        "id": f"{kind}-{qid}",
        "type": kind,
        "lang": (question or {}).get("lang", answer.get("lang", "en")),
        "question": text,
        "cypher": cypher,
        "params": params,
        "explanation": str(answer.get("explanation", "")),
        "uses_labels": list(answer.get("uses_labels", [])),
        "source": "cypher-author",
        "rows": len(result.items),
        "latency_ms": result.latency_ms,
        "validated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }, None


def _params_hint(cypher: str) -> dict[str, Any]:
    """`$key`, `$component` … with empty defaults, so `bind_params` has names to fill."""
    return {name: "" for name in sorted(set(re.findall(r"\$(\w+)", cypher)))}


def _norm(text: Any) -> str:
    """Whitespace-collapsed, lower-cased. Two queries that differ only in indentation are one."""
    return " ".join(str(text or "").split()).lower()


def dedupe_keys(example: dict[str, Any]) -> list[tuple[str, str]]:
    """The two ways an example can already be in the bank: same query, or same question.

    Both matter and they catch different things. The same *query* under a different
    question is a pattern the bank already teaches — the Hebrew twin of an English answer
    is character-for-character the same Cypher, and keeping it would spend one of the five
    slots on a second copy. The same *question* under a different query is worse: two
    answers to one question, and a model picking between them by embedding distance gets
    whichever sorted first. That is exactly how the midnight temporal example and the
    end-of-day `status_at` tool came to disagree about KAFKA-15538.
    """
    return [("cypher", _norm(example.get("cypher"))), ("question", _norm(example.get("question")))]


def write_merge_report(report: dict[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else MERGE_REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def read_merge_report(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else MERGE_REPORT
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def merge_batch(
    ctx: Any,
    *,
    batch_dir: Path | None = None,
    bank_path: Path | None = None,
    keep_seeds: bool = True,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Validate every answer in every `*.out.json` and rewrite the bank. Reports counts."""
    directory = Path(batch_dir) if batch_dir is not None else BATCH_DIR
    questions = _batch_questions(directory)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    files: list[str] = []

    for path in sorted(directory.glob("*.out.json")):
        files.append(str(path))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rejected.append({"question_id": path.name, "reason": "unreadable", "detail": str(exc)})
            continue
        for answer in payload.get("answers", []):
            question = questions.get(str(answer.get("question_id", "")))
            example, rejection = validate_example(ctx, answer, question=question)
            (accepted if example else rejected).append(example or rejection)  # type: ignore[arg-type]

    bank: list[dict[str, Any]] = list(SEED_EXAMPLES) if keep_seeds else []
    per_type: dict[str, int] = dict.fromkeys(QUESTION_TYPES, 0)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for example in bank:
        per_type[example["type"]] += 1
        for key in dedupe_keys(example):
            seen.setdefault(key, example)
    validated = list(accepted)
    for example in validated:
        duplicate = next(((k, seen[k]) for k in dedupe_keys(example) if k in seen), None)
        if duplicate is not None:
            ((kind, _value), original) = duplicate
            is_seed = original.get("source") == "hand"
            rejected.append(
                {
                    "question_id": example["id"],
                    "reason": "duplicate-of-seed" if is_seed else "duplicate",
                    "detail": f"same {kind} as {original['id']}",
                    "type": example["type"],
                }
            )
            continue
        if per_type[example["type"]] >= MAX_PER_TYPE:
            rejected.append(
                {
                    "question_id": example["id"],
                    "reason": "type-full",
                    "detail": f"{example['type']} already has {MAX_PER_TYPE} examples",
                    "type": example["type"],
                }
            )
            continue
        bank.append(example)
        per_type[example["type"]] += 1
        for key in dedupe_keys(example):
            seen.setdefault(key, example)

    target = write_bank(bank, bank_path)
    # Three different numbers, and conflating them is how a bank report lies: how many
    # answers arrived, how many survived the guard and the graph, and how many the 3–5
    # per type cap actually let in.
    thin = sorted(t for t, n in per_type.items() if n < MIN_PER_TYPE)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": head_sha(),
        "batches": files,
        "answers": len(validated)
        + len([r for r in rejected if r.get("reason") not in POST_VALIDATION_REASONS]),
        "validated": len(validated),
        "accepted": len(bank) - (len(SEED_EXAMPLES) if keep_seeds else 0),
        "duplicates": len(
            [r for r in rejected if r.get("reason") in ("duplicate", "duplicate-of-seed")]
        ),
        "rejected": rejected,
        "bank_size": len(bank),
        # Every example in the bank is unique by query *and* by question, so this is the
        # per-type count of distinct examples — the number the 3–5 rule is about.
        "per_type": per_type,
        "unique_per_type": dict(per_type),
        "min_per_type": MIN_PER_TYPE,
        "thin_types": thin,
        "ok": not thin,
        "bank_path": str(target),
        "seeds": len(SEED_EXAMPLES) if keep_seeds else 0,
        "per_question": sorted(
            [
                {
                    "question_id": e["id"].split("-", 1)[-1],
                    "id": e["id"],
                    "type": e["type"],
                    "outcome": "accepted",
                    "rows": e.get("rows"),
                    "latency_ms": e.get("latency_ms"),
                }
                for e in bank
                if e.get("source") == "cypher-author"
            ]
            + [
                {
                    "question_id": r.get("question_id"),
                    "type": r.get("type"),
                    "outcome": "rejected",
                    "reason": r.get("reason"),
                    "detail": str(r.get("detail", ""))[:300],
                }
                for r in rejected
            ],
            key=lambda row: str(row.get("question_id")),
        ),
    }
    report["report_path"] = str(write_merge_report(report, report_path))
    return report
