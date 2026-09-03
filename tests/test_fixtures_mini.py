from pathlib import Path

from brain.canon.io import read_jsonl
from brain.canon.models import Change, Container, Document, Person, WorkItem

MINI = Path("data/fixtures/mini")


def test_mini_corpus_parses_and_has_expected_shape():
    wis = list(read_jsonl(MINI / "workitems.jsonl", WorkItem))
    docs = list(read_jsonl(MINI / "documents.jsonl", Document))
    people = list(read_jsonl(MINI / "persons.jsonl", Person))
    changes = list(read_jsonl(MINI / "changes.jsonl", Change))
    containers = list(read_jsonl(MINI / "containers.jsonl", Container))
    assert len(wis) == 6 and len(docs) == 1 and len(people) == 3
    assert len(changes) == 4 and len(containers) == 4
    # the text-only reference: KAFKA-100 mentions KIP-5 only in its description
    k100 = next(w for w in wis if w.key == "KAFKA-100")
    assert ("kip", "KIP-5") in {(r.kind, r.key) for r in k100.refs}
    # a person with three identities across systems
    assert any(len(p.identities) == 3 for p in people)
    # synthetic layer is flagged
    assert any(w.synthetic for w in wis) and all(w.synthetic for w in wis if w.source == "xray")
