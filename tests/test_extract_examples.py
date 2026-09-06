"""`brain/extract/examples.md` is a contract too — so it is checked, not trusted.

The examples are the strongest instruction the `kg-extractor` agents get: an agent that copies
the shape of a worked example will produce that shape 125 times. An example whose quote drifted
out of its chunk would teach every agent to paraphrase, and nothing downstream would say so
until the merge started rejecting entities in bulk.

So this test re-derives what the file claims: each output validates against the schema, and each
quote is verbatim in the input printed directly above it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from brain.extract.models import BatchInput, BatchOutput
from brain.extract.names import quote_found
from brain.synth.jsonschema_mini import validate as schema_validate

EXAMPLES = Path("brain/extract/examples.md")
SCHEMA = json.loads(Path("brain/extract/schema.json").read_text(encoding="utf-8"))
FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def pairs() -> list[tuple[dict, dict]]:
    """Every (input, output) pair, in the order the file presents them."""
    blocks = [json.loads(m.group(1)) for m in FENCE.finditer(EXAMPLES.read_text(encoding="utf-8"))]
    assert len(blocks) % 2 == 0, f"{len(blocks)} JSON blocks — inputs and outputs must pair up"
    return list(zip(blocks[0::2], blocks[1::2], strict=True))


PAIRS = pairs()


def test_the_file_shows_the_three_examples_the_brief_asks_for():
    """A KIP Motivation, a KIP Rejected Alternatives, and a Bug description."""
    assert len(PAIRS) == 3
    parents = [i["chunks"][0]["parent_key"] for i, _o in PAIRS]
    kinds = [i["chunks"][0]["parent_kind"] for i, _o in PAIRS]
    assert kinds == ["Document", "Document", "WorkItem"]
    assert all(p.startswith("KIP-") for p in parents[:2])
    assert parents[2].startswith("KAFKA-")
    text = EXAMPLES.read_text(encoding="utf-8")
    assert "# Motivation" in text
    assert "# Rejected Alternatives" in text


@pytest.mark.parametrize(("inp", "out"), PAIRS, ids=lambda b: b.get("batch_id", "?"))
def test_each_example_is_a_valid_batch_pair(inp, out):
    parsed_in = BatchInput.model_validate(inp)
    assert schema_validate(out, SCHEMA) == [], out["batch_id"]
    parsed_out = BatchOutput.model_validate(out)
    assert parsed_out.batch_id == parsed_in.batch_id


@pytest.mark.parametrize(("inp", "out"), PAIRS, ids=lambda b: b.get("batch_id", "?"))
def test_every_quote_in_an_example_is_verbatim_in_its_chunk(inp, out):
    texts = {c["chunk_id"]: c["text"] for c in inp["chunks"]}
    assert out["entities"], out["batch_id"]
    for record in out["entities"]:
        assert record["chunk_id"] in texts, record
        assert quote_found(record["quote"], texts[record["chunk_id"]]), record["quote"]
        assert len(record["quote"]) <= 300


@pytest.mark.parametrize(("inp", "out"), PAIRS, ids=lambda b: b.get("batch_id", "?"))
def test_every_relation_in_an_example_is_anchored_and_resolvable(inp, out):
    """An endpoint is an entity of the same batch or a key the graph holds (`KIP-…`)."""
    ids = {c["chunk_id"] for c in inp["chunks"]}
    names = {e["name"] for e in out["entities"]}
    for record in out["relations"]:
        assert record["evidence_chunk_id"] in ids, record
        for side in ("source", "target"):
            name = record[side]
            assert name in names or re.fullmatch(r"(KIP|KAFKA)-\d+", name), (side, record)


def test_the_examples_teach_the_weak_decision_case():
    """A Decision the text does not motivate is kept and marked, not dropped or invented."""
    text = EXAMPLES.read_text(encoding="utf-8")
    assert "weak = true" in text
    decisions = {e["name"] for _i, o in PAIRS for e in o["entities"] if e["kind"] == "Decision"}
    supported = {
        r["target"]
        for _i, o in PAIRS
        for r in o["relations"]
        if r["type"] in {"MOTIVATED_BY", "REJECTS"}
    } | {
        r["source"]
        for _i, o in PAIRS
        for r in o["relations"]
        if r["type"] in {"MOTIVATED_BY", "REJECTS"}
    }
    assert decisions - supported, "no example shows a Decision without a stated reason"


def test_the_examples_show_a_relation_anchored_on_an_existing_key():
    """`DECIDES` from a KIP the graph already holds is the commonest edge in Phase A."""
    anchored = [
        r for _i, o in PAIRS for r in o["relations"] if re.fullmatch(r"KIP-\d+", r["source"])
    ]
    assert anchored
    assert {r["type"] for r in anchored} >= {"DECIDES", "REJECTS"}
