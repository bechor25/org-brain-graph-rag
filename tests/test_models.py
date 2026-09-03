from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from brain.canon.io import read_jsonl, write_jsonl
from brain.canon.models import ChangelogEntry, Identity, Person, Ref, WorkItem


def test_workitem_minimal_and_defaults():
    wi = WorkItem(
        id="jira:KAFKA-1",
        key="KAFKA-1",
        source="jira",
        type="Bug",
        title="t",
        status="Open",
        created=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert wi.components == [] and wi.synthetic is False and wi.refs == []


def test_changelog_alias_from():
    e = ChangelogEntry.model_validate(
        {
            "field": "status",
            "from": "Open",
            "to": "Resolved",
            "at": "2024-02-01T00:00:00Z",
            "by": "jrao",
        }
    )
    assert e.from_ == "Open" and e.to == "Resolved"
    assert e.model_dump(by_alias=True)["from"] == "Open"


def test_person_requires_identity():
    with pytest.raises(ValidationError):
        Person(id="p1", identities=[])
    p = Person(id="p1", identities=[Identity(source="jira", key="jrao", display="Jun Rao")])
    assert p.identities[0].email is None


def test_source_is_closed_enum():
    with pytest.raises(ValidationError):
        WorkItem(
            id="x",
            key="X-1",
            source="trello",
            type="Task",
            title="t",
            status="Open",
            created=datetime(2024, 1, 1, tzinfo=UTC),
        )


def test_jsonl_roundtrip_and_corrupt_line(tmp_path):
    path = tmp_path / "r.jsonl"
    refs = [Ref(kind="issue", key="KAFKA-1"), Ref(kind="kip", key="KIP-5")]
    assert write_jsonl(path, refs) == 2
    assert list(read_jsonl(path, Ref)) == refs

    path.write_text('{"kind": "issue", "key": "KAFKA-1"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.jsonl:2:"):
        list(read_jsonl(path, Ref))
