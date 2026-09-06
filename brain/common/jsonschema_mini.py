"""A JSON Schema validator for exactly the subset this repo's `schema.json` files use.

Two callers: `brain/synth/schema.json` (the synthetic batch contract) and
`brain/extract/schema.json` (the extraction batch contract).

Why not `jsonschema`: the conventions pin dependencies in `uv.lock` and ask for a brief
before adding one. The subset a hand-written contract actually needs is small, and the
risk of a hand-rolled validator — *silently* accepting a keyword it does not implement —
is removed by :func:`unsupported_keywords`, which the test suite runs over the real schema
so an unimplemented keyword is a failing test rather than a validation hole.

Not implemented on purpose (and therefore rejected by the guard): `allOf`, `anyOf`,
`oneOf`, `not`, `if/then/else`, `dependentRequired`, `patternProperties`, `propertyNames`,
`format`, `multipleOf`, `exclusiveMinimum`/`exclusiveMaximum`, remote `$ref`. A union is
spelled as a type list (`"type": ["string", "null"]`) instead, which covers every case the
contract has. If the schema ever needs one of these, add it here with a test — or take the
brief and add `jsonschema`.
"""

from __future__ import annotations

import re
from typing import Any

#: Keywords this module enforces.
SUPPORTED: frozenset[str] = frozenset(
    {
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "$ref",
        "$defs",
    }
)

#: Keywords that carry no constraint. Ignoring these is correct, not a hole.
ANNOTATIONS: frozenset[str] = frozenset(
    {"$schema", "$id", "title", "description", "examples", "default", "deprecated"}
)

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ValueError):
    """The schema itself uses something this validator does not implement."""


def unsupported_keywords(schema: Any) -> set[str]:
    """Every keyword in `schema` that is neither enforced nor a known annotation.

    Walks the whole document, including `$defs` and nested subschemas. An empty result is
    the proof that validating against this schema is validating against all of it.
    """
    found: set[str] = set()

    def walk(node: Any, *, in_properties: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if in_properties:
            # keys here are property *names*, not keywords
            for value in node.values():
                walk(value)
            return
        for key, value in node.items():
            if key not in SUPPORTED and key not in ANNOTATIONS:
                found.add(key)
            if key in {"properties", "$defs"}:
                walk(value, in_properties=True)
            elif key in {"items", "additionalProperties"}:
                walk(value)

    walk(schema)
    return found


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return every violation as a `path: message` string. Empty list means valid.

    All errors, not the first: a batch that fails on three fields should come back with
    three reasons, so the agent regenerating it fixes them in one pass.
    """
    bad = unsupported_keywords(schema)
    if bad:
        raise SchemaError(f"schema uses unimplemented keywords: {sorted(bad)}")
    errors: list[str] = []
    _check(instance, schema, "$", schema, errors)
    return errors


# --------------------------------------------------------------------------- internals


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref is supported, got {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return {dict: "object", list: "array", str: "string"}.get(type(value), type(value).__name__)


def _matches_type(value: Any, name: str) -> bool:
    expected = _TYPES.get(name)
    if expected is None:
        raise SchemaError(f"unknown type {name!r}")
    if name in {"integer", "number"} and isinstance(value, bool):
        return False  # JSON booleans are not numbers, whatever Python thinks
    if name != "boolean" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _check(value: Any, schema: dict[str, Any], path: str, root: dict[str, Any], out: list[str]):
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), path, root, out)
        return

    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_matches_type(value, n) for n in names):
            out.append(f"{path}: expected {'|'.join(names)}, got {_type_name(value)}")
            return

    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must be {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, str):
        _check_string(value, schema, path, out)
    elif isinstance(value, list):
        _check_array(value, schema, path, root, out)
    elif isinstance(value, dict):
        _check_object(value, schema, path, root, out)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > maximum {schema['maximum']}")


#: An unescaped `$` at the end of a pattern. Python's `$` also matches *before* a final
#: newline, so `"ADO-1\n"` satisfies `^ADO-[0-9]+$` and a key with a stray newline would
#: reach `brain load`. `\Z` is the end of the string and nothing else.
_TRAILING_DOLLAR = re.compile(r"(?<!\\)\$$")


def anchored(pattern: str) -> str:
    """`^ADO-[0-9]+$` -> `^ADO-[0-9]+\\Z`. Any other pattern is left as written."""
    return _TRAILING_DOLLAR.sub(r"\\Z", pattern)


def _check_string(value: str, schema: dict[str, Any], path: str, out: list[str]) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        out.append(f"{path}: shorter than {schema['minLength']} characters")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{path}: longer than {schema['maxLength']} characters")
    if "pattern" in schema and not re.search(anchored(schema["pattern"]), value):
        out.append(f"{path}: {value!r} does not match {schema['pattern']}")


def _check_array(
    value: list[Any], schema: dict[str, Any], path: str, root: dict[str, Any], out: list[str]
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        out.append(f"{path}: needs at least {schema['minItems']} items, has {len(value)}")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        out.append(f"{path}: allows at most {schema['maxItems']} items, has {len(value)}")
    if schema.get("uniqueItems") and len(value) != len({repr(v) for v in value}):
        out.append(f"{path}: items must be unique")
    if "items" in schema:
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i}]", root, out)


def _check_object(
    value: dict[str, Any], schema: dict[str, Any], path: str, root: dict[str, Any], out: list[str]
) -> None:
    props: dict[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            out.append(f"{path}: missing required property {name!r}")
    extra = schema.get("additionalProperties", True)
    for name, child in value.items():
        if name in props:
            _check(child, props[name], f"{path}.{name}", root, out)
        elif extra is False:
            out.append(f"{path}: unexpected property {name!r}")
        elif isinstance(extra, dict):
            _check(child, extra, f"{path}.{name}", root, out)
