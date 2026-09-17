"""Reading one analyst answer file, and the template that tells the analyst how to write it.

The file layout is a contract between three parties that never meet: the `brain-analyst`
agent writing the answer, this parser, and the planner reading the report. `README_TEMPLATE`
is that contract in the analyst's own directory, and it lives next to the parser so the two
cannot drift.

The parser is forgiving in exactly one direction. It accepts `strategy: route, lookup` and
`**strategy:** called route() then local_search`, because an agent writing prose is normal
and a gate that fails on a bold marker measures markdown. It is not forgiving about what a
tool *is*: only the fifteen names the MCP server actually serves are counted, so a `strategy:`
line naming a tool that does not exist shows up as "no known tool" instead of as a number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from brain.retrieve.types import STRATEGIES

#: The fifteen MCP tools (spec §4.3). Deliberately a copy rather than an import: reading a
#: report must not require the `mcp` package to be importable. `tests/test_eval_answers.py`
#: asserts this equals `brain.mcp.server.TOOL_NAMES`, which is the seam that keeps it true.
KNOWN_TOOLS: tuple[str, ...] = (
    "search_chunks",
    "search_with_context",
    "lookup",
    "local_search",
    "get_schema",
    "cypher_examples",
    "run_cypher",
    "global_search",
    "status_at",
    "timeline",
    "changes_between",
    "assignees_over_time",
    "impact",
    "explain_edge",
    "route",
)

FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
STRATEGY_LINE_RE = re.compile(r"^\s*[*_#>\s]*strategy[*_\s]*[:：]\s*(.*)$", re.IGNORECASE)
LATENCY_LINE_RE = re.compile(r"^\s*[*_#>\s]*latency[*_\s]*[:：]\s*(.*)$", re.IGNORECASE)
LATENCY_VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)?", re.I)
HEBREW_RE = re.compile(r"[֐-׿]")
LATIN_RE = re.compile(r"[A-Za-z]")
#: A Hebrew answer always carries Latin keys (`KAFKA-…`, `chunk:…`, tool names), so the
#: question is never "is there Latin" but "is there real Hebrew". Both a floor and a ratio,
#: so one transliterated word cannot flip an English answer and a Hebrew answer full of
#: keys cannot be read as English.
HEBREW_FLOOR, HEBREW_RATIO = 10, 0.2

README_NAME = "README.md"
README_TEMPLATE = """\
# Plan 2 Task 4 — how to write an answer file

One file per question, named `<question_id>.md` (`cq01.md` … `cq19.md`), in this directory.
`brain eval cite-check` reads exactly this layout; anything else it cannot read is reported
as a problem against your question, so keep to it.

```markdown
---
question_id: cq01
lang: en
started_at: 2026-09-17T10:00:00+00:00
finished_at: 2026-09-17T10:00:42+00:00
---

<the answer, in the language of the question>

strategy: route, lookup, search_with_context
```

## Front matter (between the two `---` lines)

| field | required | what it is |
|---|---|---|
| `question_id` | yes | must equal the filename, e.g. `cq01` |
| `lang` | yes | `en` or `he` — the question's language, which you must answer in |
| `started_at` | yes | ISO-8601 with an offset, when you began this question |
| `finished_at` | yes | ISO-8601 with an offset, when you finished it |

`finished_at - started_at` is what the report prints as latency. Write `latency: 12400ms`
(or `latency: 12.4s`) on its own line if you measured the tool time itself and want that
number reported instead.

## The body

Every factual sentence ends with citations in square brackets. These forms are checked
against the graph, and nothing else counts as a citation:

| form | example | checked by |
|---|---|---|
| work item key | `[KAFKA-14649]` | `WorkItem.key`, exact |
| KIP / document key | `[KIP-848]` | `Document.key`, exact |
| chunk id | `[chunk:ab12cd34ef56]` | `Chunk.id` starts with it — at least 8 hex characters |
| commit sha | `[a1b2c3d4]` | `Commit.sha` starts with it — 7 to 40 hex characters |
| person | `[person:jira:mjsax]` | `Person.id`, exact — the full id, with its source prefix |
| community | `[community:L1-7]` | `Community.id`, exact |

Rules that the checker enforces or reports:

- A key in running prose is not a citation. Only the brackets count.
- Citations inside a ``` fenced block are ignored (that is where you paste the Cypher you
  ran, and `[:RESOLVES]` is not evidence).
- `[KAFKA-100](https://…)` is a markdown link, not a citation.
- A citation that does not resolve in the graph is counted as invalid and listed by name in
  the report. If the tools returned nothing, write "not found" — never fill the gap from
  prior knowledge about Kafka.

## The last line

End the file with a `strategy:` line naming the tools you used, in the order you used them:

```
strategy: route, lookup, local_search, run_cypher
```

Only these names are recognised: {tools}. Strategy ids (`s1`…`s6`) may be mentioned too and
are reported separately. A file with no `strategy:` line is reported as a problem.
""".replace("{tools}", ", ".join(f"`{t}`" for t in KNOWN_TOOLS))


@dataclass
class Answer:
    """One answer file, already split into the parts the report asks about."""

    qid: str
    path: Path
    raw: str
    front: dict[str, str] = field(default_factory=dict)
    body: str = ""
    strategy_line: str | None = None
    tools: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    latency_ms: int | None = None
    latency_source: str | None = None
    detected_lang: str | None = None
    modified_at: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def lang(self) -> str | None:
        """What language this answer is in — measured, with the declared value as fallback."""
        return self.detected_lang or (self.front.get("lang") or None)


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    front: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        field_match = FIELD_RE.match(line)
        if field_match:
            front[field_match.group(1)] = field_match.group(2).strip().strip("\"'")
    return front, text[match.end() :]


def _pull_line(body: str, pattern: re.Pattern[str]) -> tuple[str | None, str]:
    """The last line matching `pattern`, and the body without it."""
    lines = body.split("\n")
    for index in range(len(lines) - 1, -1, -1):
        match = pattern.match(lines[index])
        if match:
            value = match.group(1).strip().strip("*_ ").strip()
            return value, "\n".join(lines[:index] + lines[index + 1 :])
    return None, body


def parse_tools(line: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Tool names and strategy ids named in a `strategy:` line, in order, de-duplicated."""
    lowered = line.lower()
    found = [
        (match.start(), match.group(0))
        for tool in KNOWN_TOOLS
        for match in re.finditer(rf"\b{re.escape(tool)}\b", lowered)
    ]
    tools: list[str] = []
    for _, name in sorted(found):
        if name not in tools:
            tools.append(name)
    strategies = [
        s for s in STRATEGIES if s != "lookup" and re.search(rf"\b{s}\b", lowered) is not None
    ]
    return tuple(tools), tuple(strategies)


def parse_latency(line: str) -> int | None:
    match = LATENCY_VALUE_RE.search(line)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "ms").lower()
    return round(number * 1000) if unit.startswith("s") else round(number)


def _timestamp_latency(front: dict[str, str]) -> int | None:
    started, finished = front.get("started_at"), front.get("finished_at")
    if not started or not finished:
        return None
    try:
        delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    except ValueError:
        return None
    ms = round(delta.total_seconds() * 1000)
    return ms if ms >= 0 else None


def detect_lang(body: str) -> str | None:
    hebrew = len(HEBREW_RE.findall(body))
    latin = len(LATIN_RE.findall(body))
    if not hebrew and not latin:
        return None
    if hebrew >= HEBREW_FLOOR and hebrew / (hebrew + latin) >= HEBREW_RATIO:
        return "he"
    return "he" if hebrew and not latin else "en"


def parse_answer(path: Path) -> Answer:
    """Read one `<qid>.md`. Never raises on bad content — it reports it."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    front, body = _front_matter(raw)
    strategy_line, body = _pull_line(body, STRATEGY_LINE_RE)
    latency_line, body = _pull_line(body, LATENCY_LINE_RE)

    problems: list[str] = []
    qid = path.stem
    if not front:
        problems.append("no front matter (the `---` block with question_id/lang/started_at)")
    for required in ("question_id", "lang", "started_at", "finished_at"):
        if not front.get(required):
            problems.append(f"front matter is missing `{required}`")
    declared = front.get("question_id")
    if declared and declared != qid:
        problems.append(f"front matter `question_id` is {declared!r} but the file is {qid}.md")

    tools: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    if strategy_line is None:
        problems.append("no `strategy:` line — the trace cannot say which tools were used")
    else:
        tools, strategies = parse_tools(strategy_line)
        if not tools:
            problems.append(f"the `strategy:` line names no known tool: {strategy_line!r}")

    latency_ms = parse_latency(latency_line) if latency_line else None
    latency_source = "line" if latency_ms is not None else None
    if latency_ms is None:
        latency_ms = _timestamp_latency(front)
        latency_source = "timestamps" if latency_ms is not None else None

    body = body.strip("\n")
    if not body.strip():
        problems.append("the answer body is empty")

    return Answer(
        qid=qid,
        path=path,
        raw=raw,
        front=front,
        body=body,
        strategy_line=strategy_line,
        tools=tools,
        strategies=strategies,
        latency_ms=latency_ms,
        latency_source=latency_source,
        detected_lang=detect_lang(body),
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
        problems=problems,
    )


def read_answers(directory: Path) -> dict[str, Answer]:
    """Every `<qid>.md` in the answers directory, keyed by qid. A missing directory is `{}`."""
    directory = Path(directory)
    if not directory.is_dir():
        return {}
    out: dict[str, Answer] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == README_NAME.lower() or path.name.startswith("_"):
            continue
        out[path.stem] = parse_answer(path)
    return out


def write_template(directory: Path) -> Path:
    """Put (or refresh) the layout contract in the analyst's directory. Idempotent."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / README_NAME
    if not path.exists() or path.read_text(encoding="utf-8") != README_TEMPLATE:
        path.write_text(README_TEMPLATE, encoding="utf-8")
    return path
