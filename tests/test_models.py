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


def test_ref_via_defaults_to_text_and_round_trips(tmp_path):
    assert Ref(kind="issue", key="KAFKA-1").via == "text"
    path = tmp_path / "refs.jsonl"
    wi = WorkItem(
        id="jira:KAFKA-2",
        key="KAFKA-2",
        source="jira",
        type="Bug",
        title="t",
        status="Open",
        created=datetime(2024, 1, 1, tzinfo=UTC),
        refs=[Ref(kind="issue", key="KAFKA-3", via="link")],
    )
    write_jsonl(path, [wi])
    assert next(read_jsonl(path, WorkItem)).refs[0].via == "link"


def test_person_is_unresolved_until_brain_resolve():
    p = Person(id="jira:jrao", identities=[Identity(source="jira", key="jrao")])
    assert p.resolved is False


def test_write_jsonl_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "out" / "workitems.jsonl"
    wi = WorkItem(
        id="jira:KAFKA-1",
        key="KAFKA-1",
        source="jira",
        type="Bug",
        title="t",
        status="Open",
        created=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert write_jsonl(path, [wi, wi]) == 2
    assert list(path.parent.iterdir()) == [path]

    class Boom(WorkItem):
        def model_dump(self, **kw):
            raise RuntimeError("boom")

    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        write_jsonl(path, [wi, Boom(**wi.model_dump())])
    assert path.read_bytes() == before  # the previous run survived the failed one
