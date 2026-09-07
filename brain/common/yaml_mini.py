"""A YAML loader for exactly the subset `sources.yaml` uses.

One caller: :mod:`brain.harvest.registry`.

Why not `PyYAML`: the conventions pin dependencies in `uv.lock` and ask for a brief
before adding one (`brain/common/jsonschema_mini.py` made the same call for the same
reason). `sources.yaml` is a config file a human edits — block mappings, block
sequences, quoted strings — and that subset is small enough to implement honestly.

The danger with a hand-rolled parser is not failing on a construct it does not know; it
is **succeeding** on one and returning something subtly different from what the author
wrote. So every construct below is rejected with a line number rather than guessed at:

    anchors/aliases (`&a`, `*a`), merge keys (`<<`), tags (`!!str`), block scalars
    (`|`, `>`), multiple documents (`---`, `...`), explicit keys (`?`), tab indentation,
    nested flow collections, and any line the grammar below does not match.

What it does support:

* block mappings ``key: value`` at any nesting depth, indented with spaces;
* block sequences ``- item``, where an item is a scalar or a mapping;
* flow sequences ``[a, b]`` and flow mappings ``{a: b}`` of scalars, one level deep;
* scalars: plain, ``'single'``, ``"double"`` (with ``\\n``/``\\t``/``\\"``/``\\\\`` escapes),
  ``true``/``false``, ``null``/``~``/empty, integers and floats;
* ``#`` comments — a whole line, or after a value when preceded by whitespace and not
  inside quotes (so ``base_url: https://host/x#y`` keeps its fragment).

Plain scalars keep their inner ``#`` and ``:`` — a JQL string with a colon does not need
quoting to survive, and quoting it anyway is always allowed.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["YamlError", "parse_scalar", "safe_load"]


class YamlError(ValueError):
    """`sources.yaml` uses something this loader does not implement, or is malformed.

    Loud with a line number on purpose: the alternative to an error here is a config
    that loads as the wrong thing, and the first symptom of that is a harvest against
    the wrong query.
    """

    def __init__(self, line_no: int, message: str, line: str = "") -> None:
        where = f"line {line_no}"
        detail = f"{where}: {message}"
        if line:
            detail += f"\n  {line.rstrip()}"
        super().__init__(detail)
        self.line_no = line_no


#: Constructs that mean something in YAML which this loader does not implement.
#: Matched on the *value* side, after the key, or on a bare sequence item.
_REJECT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^[&*]\S"), "anchors and aliases are not supported"),
    (re.compile(r"^!"), "tags are not supported"),
    (re.compile(r"^[|>][-+0-9]*$"), "block scalars (| and >) are not supported"),
)

_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?:\s+(?P<value>.*))?$")
_QUOTED_KEY = re.compile(r"^(?P<q>[\"'])(?P<key>.*?)(?P=q)\s*:(?:\s+(?P<value>.*))?$")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")

_NULLS = frozenset({"", "null", "Null", "NULL", "~"})
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})


# --------------------------------------------------------------------------- scalars


def _unescape_double(text: str, line_no: int, line: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(text):
            raise YamlError(line_no, "string ends with a dangling backslash", line)
        nxt = text[i]
        mapped = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(nxt)
        if mapped is None:
            raise YamlError(line_no, rf"unsupported escape \{nxt}", line)
        out.append(mapped)
        i += 1
    return "".join(out)


def _strip_comment(text: str) -> str:
    """Drop a trailing `# comment`, but only outside quotes and after whitespace.

    `https://host/page#frag` keeps its fragment; `KAFKA # the real one` loses the note.
    """
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i]
    return text


def parse_scalar(raw: str, line_no: int = 0, line: str = "") -> Any:
    """One YAML scalar (or a one-level flow collection) as a Python value."""
    text = raw.strip()
    if text.startswith(("'", '"')):
        quote = text[0]
        end = text.rfind(quote)
        if end <= 0:
            raise YamlError(line_no, "unterminated quoted string", line or raw)
        body, rest = text[1:end], text[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise YamlError(line_no, f"trailing content after a quoted string: {rest!r}", line)
        return body.replace("''", "'") if quote == "'" else _unescape_double(body, line_no, line)

    text = _strip_comment(text).strip()
    if text.startswith("["):
        return _flow_sequence(text, line_no, line)
    if text.startswith("{"):
        return _flow_mapping(text, line_no, line)
    for pattern, message in _REJECT:
        if pattern.match(text):
            raise YamlError(line_no, message, line or raw)
    if text in _NULLS:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


def _split_flow(body: str, line_no: int, line: str) -> list[str]:
    """Split a flow collection's body on top-level commas, respecting quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote is not None:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
            continue
        if ch in "[]{}":
            raise YamlError(line_no, "nested flow collections are not supported", line)
        if ch == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if quote is not None:
        raise YamlError(line_no, "unterminated quoted string in a flow collection", line)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _flow_sequence(text: str, line_no: int, line: str) -> list[Any]:
    if not text.endswith("]"):
        raise YamlError(line_no, "flow sequence is not closed on the same line", line)
    return [parse_scalar(part, line_no, line) for part in _split_flow(text[1:-1], line_no, line)]


def _flow_mapping(text: str, line_no: int, line: str) -> dict[str, Any]:
    if not text.endswith("}"):
        raise YamlError(line_no, "flow mapping is not closed on the same line", line)
    out: dict[str, Any] = {}
    for part in _split_flow(text[1:-1], line_no, line):
        key, sep, value = part.partition(":")
        if not sep:
            raise YamlError(line_no, f"flow mapping entry {part!r} has no value", line)
        out[str(parse_scalar(key, line_no, line))] = parse_scalar(value, line_no, line)
    return out


# --------------------------------------------------------------------------- documents


class _Line:
    __slots__ = ("indent", "no", "raw", "text")

    def __init__(self, no: int, raw: str) -> None:
        self.no = no
        self.raw = raw
        stripped = raw.lstrip(" ")
        self.indent = len(raw) - len(stripped)
        self.text = stripped.rstrip()


def _scan(source: str) -> list[_Line]:
    lines: list[_Line] = []
    for no, raw in enumerate(source.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(no, "tab used for indentation; YAML requires spaces", raw)
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in {"---", "..."} or stripped.startswith("--- "):
            raise YamlError(no, "multi-document streams are not supported", raw)
        if stripped.startswith("<<"):
            raise YamlError(no, "merge keys (<<) are not supported", raw)
        if stripped.startswith("? "):
            raise YamlError(no, "explicit keys (?) are not supported", raw)
        lines.append(_Line(no, raw))
    return lines


class _Parser:
    def __init__(self, lines: list[_Line]) -> None:
        self.lines = lines
        self.pos = 0

    def peek(self) -> _Line | None:
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def block(self, indent: int) -> Any:
        line = self.peek()
        if line is None or line.indent < indent:
            return None
        return (
            self.sequence(line.indent)
            if line.text.startswith("- ") or line.text == "-"
            else (self.mapping(line.indent))
        )

    def sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while (line := self.peek()) is not None and line.indent == indent:
            if not (line.text.startswith("- ") or line.text == "-"):
                break
            self.pos += 1
            rest = line.text[2:].strip() if line.text.startswith("- ") else ""
            items.append(self._item(line, rest, indent))
        return items

    def _item(self, line: _Line, rest: str, indent: int) -> Any:
        if not rest:
            nested = self.peek()
            if nested is None or nested.indent <= indent:
                return None
            return self.block(nested.indent)
        # `- key: value` opens a mapping whose keys are indented to the text after "- ".
        match = _KEY.match(rest) or _QUOTED_KEY.match(rest)
        if match:
            inner_indent = indent + (len(line.text) - len(line.text[2:].lstrip()))
            value = self._value(line, match, inner_indent)
            item: dict[str, Any] = {match.group("key"): value}
            while (nxt := self.peek()) is not None and nxt.indent == inner_indent:
                inner = _KEY.match(nxt.text) or _QUOTED_KEY.match(nxt.text)
                if inner is None:
                    raise YamlError(nxt.no, "expected `key: value` inside a sequence item", nxt.raw)
                self.pos += 1
                item[inner.group("key")] = self._value(nxt, inner, inner_indent)
            return item
        return parse_scalar(rest, line.no, line.raw)

    def mapping(self, indent: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while (line := self.peek()) is not None and line.indent == indent:
            if line.text.startswith("- "):
                break
            match = _KEY.match(line.text) or _QUOTED_KEY.match(line.text)
            if match is None:
                raise YamlError(line.no, f"expected `key: value`, got {line.text!r}", line.raw)
            self.pos += 1
            key = match.group("key")
            if key in out:
                raise YamlError(line.no, f"duplicate key {key!r}", line.raw)
            out[key] = self._value(line, match, indent)
        return out

    def _value(self, line: _Line, match: re.Match[str], indent: int) -> Any:
        inline = (match.group("value") or "").strip()
        if inline and not inline.startswith("#"):
            return parse_scalar(inline, line.no, line.raw)
        nested = self.peek()
        if nested is None or nested.indent <= indent:
            return None
        return self.block(nested.indent)


def safe_load(source: str) -> Any:
    """Parse the supported subset. Anything else raises :class:`YamlError`."""
    lines = _scan(source)
    if not lines:
        return None
    parser = _Parser(lines)
    value = parser.block(lines[0].indent)
    if (leftover := parser.peek()) is not None:
        raise YamlError(leftover.no, "unexpected indentation", leftover.raw)
    return value
