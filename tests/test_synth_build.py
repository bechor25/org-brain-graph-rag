"""`brain synth build`: what it selects, what it attaches, and what it reserves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.canon.io import read_jsonl
from brain.canon.models import WorkItem
from brain.synth.build import (
    DESCRIPTION_CHARS,
    KIP_EXCERPT_CHARS,
    MANIFEST_NAME,
    RESERVED_MAX,
    SCHEMA_PATH,
    SPEC_PATH,
    STATUS_NAME,
    assign_shards,
    plan_batches,
    plan_epics,
    run_build,
    select_items,
    shard_range,
)
from tests.synth_helpers import plant_canonical, real_item

MINI = Path("data/fixtures/mini")
#: The largest `.in.json` an agent should be asked to read in one go.
MAX_BATCH_BYTES = 150 * 1024


def build(tmp_path: Path, **kwargs) -> dict:
    canonical = plant_canonical(tmp_path / "canonical", **kwargs.pop("corpus", {}))
    manifest, code = run_build(
        canonical_dir=canonical,
        batches_dir=tmp_path / "batches",
        echo=lambda _: None,
        **kwargs,
    )
    assert code == 0
    return manifest


# --------------------------------------------------------------------------- selection


def test_selection_takes_the_five_eligible_types_with_a_description():
    items = list(read_jsonl(MINI / "workitems.jsonl", WorkItem))
    selected = select_items(items)

    assert [w.key for w in selected] == ["KAFKA-100", "KAFKA-101", "KAFKA-102"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "Sub-task"),
        ("type", "Test"),
        ("description", "   "),
        ("synthetic", True),
        ("source", "ado"),
    ],
)
def test_selection_excludes(field, value):
    kept = real_item("KAFKA-100")
    dropped = real_item("KAFKA-200", **{field: value})

    selected = select_items([WorkItem.model_validate(kept), WorkItem.model_validate(dropped)])

    assert [w.key for w in selected] == ["KAFKA-100"]


def test_selection_sorts_naturally_not_lexically():
    items = [WorkItem.model_validate(real_item(k)) for k in ("KAFKA-10", "KAFKA-9", "KAFKA-100")]

    assert [w.key for w in select_items(items)] == ["KAFKA-9", "KAFKA-10", "KAFKA-100"]


# --------------------------------------------------------------------------- sharding


def test_a_fix_version_is_never_split_across_shards():
    """One TestPlan per version is only possible if one agent sees the whole version."""
    items = [
        WorkItem.model_validate(
            real_item(f"KAFKA-{i}", fix_versions=["4.0.0" if i < 6 else "3.8.0"])
        )
        for i in range(1, 11)
    ]

    buckets = assign_shards(select_items(items), 3)

    for bucket in buckets:
        versions = {v for w in bucket for v in w.fix_versions}
        assert len(versions) <= 1, "a version landed in more than one shard"
    assert sum(len(b) for b in buckets) == 10


def test_unversioned_items_balance_the_shards():
    items = [
        WorkItem.model_validate(real_item(f"KAFKA-{i}", fix_versions=[])) for i in range(1, 10)
    ]

    sizes = sorted(len(b) for b in assign_shards(select_items(items), 3))

    assert sizes == [3, 3, 3]


def test_shard_ranges_are_disjoint_and_above_the_reserved_block():
    ranges = [shard_range(i) for i in range(3)]

    assert ranges == [(10001, 20000), (20001, 30000), (30001, 40000)]
    assert all(low > RESERVED_MAX for low, _ in ranges)


# --------------------------------------------------------------------------- attachment


def test_batch_carries_the_kips_the_items_cite(tmp_path):
    build(tmp_path)
    payload = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-01" / "001.in.json").read_text()
    )

    assert [d["key"] for d in payload["documents"]] == ["KIP-5"]
    doc = payload["documents"][0]
    assert doc["title"].startswith("KIP-5:")
    assert len(doc["excerpt"]) <= KIP_EXCERPT_CHARS + 60  # + the truncation marker
    assert doc["body_chars"] > len(doc["excerpt"])


def test_batch_carries_the_real_identities_with_their_person_id(tmp_path):
    build(tmp_path)
    payload = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-01" / "001.in.json").read_text()
    )

    by_key = {p["key"]: p for p in payload["persons"]}
    assert set(by_key) == {"jrao", "dlee"}
    assert by_key["jrao"] == {
        "person_id": "jira:jrao",
        "source": "jira",
        "key": "jrao",
        "display": "Jun Rao",
        "used_as": ["reporter"],
    }
    # dlee is both the assignee and the commenter — the roles are what the agent needs
    assert by_key["dlee"]["used_as"] == ["assignee", "commenter"]


def test_batch_carries_the_components_and_versions_involved(tmp_path):
    build(tmp_path)
    payload = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-01" / "001.in.json").read_text()
    )

    assert [(c["kind"], c["name"]) for c in payload["containers"]] == [
        ("component", "clients"),
        ("version", "3.7.0"),
    ]


def test_a_long_description_is_truncated_and_says_so(tmp_path):
    long_text = "x" * (DESCRIPTION_CHARS + 500)
    build(tmp_path, corpus={"workitems": [real_item("KAFKA-100", description=long_text)]})
    payload = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-01" / "001.in.json").read_text()
    )

    item = payload["workitems"][0]
    assert item["description_chars"] == len(long_text)
    assert "truncated" in item["description"]
    assert len(item["description"]) < len(long_text)


# --------------------------------------------------------------------------- numbering


def test_only_the_first_batch_of_a_shard_carries_a_seed(tmp_path):
    build(
        tmp_path,
        corpus={"workitems": [real_item(f"KAFKA-{i}", fix_versions=[]) for i in range(1, 5)]},
        shards=1,
        batch_size=2,
    )
    shard = tmp_path / "batches" / "synthetic" / "shard-01"

    first = json.loads((shard / "001.in.json").read_text())["numbering"]
    second = json.loads((shard / "002.in.json").read_text())["numbering"]

    assert first["XT"] == {"range_start": 10001, "range_end": 20000, "next": 10001}
    assert second["XT"] == {"range_start": 10001, "range_end": 20000, "next": None}


def test_every_prefix_gets_the_shards_range(tmp_path):
    build(
        tmp_path,
        corpus={
            "workitems": [
                real_item("KAFKA-100", fix_versions=["4.0.0"]),
                real_item("KAFKA-101", fix_versions=["3.8.0"]),
            ]
        },
        shards=2,
        batch_size=1,
    )
    second = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-02" / "001.in.json").read_text()
    )

    assert set(second["numbering"]) == {"XT", "XE", "XP", "XS", "ADO"}
    assert all(n["range_start"] == 20001 for n in second["numbering"].values())


# --------------------------------------------------------------------------- epics


def test_a_kip_cited_from_two_shards_gets_one_epic_with_one_owner():
    items = [
        WorkItem.model_validate(
            real_item(
                f"KAFKA-{i}",
                fix_versions=[f"{i}.0.0"],
                refs=[{"kind": "kip", "key": "KIP-5", "via": "text"}],
            )
        )
        for i in (1, 2, 3)
    ]
    planned = plan_batches(select_items(items), shards=3, batch_size=40)

    epics, owner = plan_epics(planned, {})

    assert list(epics) == ["KIP-5"]
    assert epics["KIP-5"].key == "ADO-1"
    assert owner["KIP-5"] in {b.batch_id for b in planned}
    owned = [b.batch_id for b in planned if owner["KIP-5"] == b.batch_id]
    assert len(owned) == 1


def test_epic_keys_come_out_of_the_reserved_block_and_follow_kip_order():
    items = [
        WorkItem.model_validate(
            real_item(f"KAFKA-{i}", refs=[{"kind": "kip", "key": key, "via": "text"}])
        )
        for i, key in enumerate(("KIP-100", "KIP-9", "KIP-50"), start=1)
    ]
    planned = plan_batches(select_items(items), shards=1, batch_size=40)

    epics, _ = plan_epics(planned, {})

    assert [(k, e.key) for k, e in epics.items()] == [
        ("KIP-9", "ADO-1"),
        ("KIP-50", "ADO-2"),
        ("KIP-100", "ADO-3"),
    ]


def test_the_owning_batch_is_the_only_one_told_to_write_the_epic(tmp_path):
    build(
        tmp_path,
        corpus={
            "workitems": [
                real_item("KAFKA-100", fix_versions=["4.0.0"]),
                real_item("KAFKA-101", fix_versions=["3.8.0"]),
            ]
        },
        shards=2,
        batch_size=40,
    )
    root = tmp_path / "batches" / "synthetic"
    owned = [
        e["owned"]
        for shard in ("shard-01", "shard-02")
        for e in json.loads((root / shard / "001.in.json").read_text())["epics"]
    ]

    assert owned.count(True) == 1
    assert owned.count(False) == 1


# --------------------------------------------------------------------------- outputs


def test_build_writes_the_spec_status_and_manifest(tmp_path):
    manifest = build(
        tmp_path,
        corpus={
            "workitems": [
                real_item("KAFKA-100", fix_versions=["4.0.0"]),
                real_item("KAFKA-101", fix_versions=["3.8.0"]),
            ]
        },
        shards=2,
        batch_size=1,
    )
    root = tmp_path / "batches" / "synthetic"

    assert (root / "shard-01" / "synthetic_spec.md").is_file()
    assert (root / MANIFEST_NAME).is_file()
    status = json.loads((root / "shard-01" / STATUS_NAME).read_text())
    assert status == {
        "shard": "shard-01",
        "done": [],
        "failed": [],
        "next_ids": {p: 10001 for p in ("XT", "XE", "XP", "XS", "ADO")},
    }
    assert manifest["spec"]["sha256"]
    assert set(manifest["canonical_sha256"]) == {
        "workitems.jsonl",
        "persons.jsonl",
        "containers.jsonl",
        "documents.jsonl",
    }
    assert [b["id"] for b in manifest["batches"]] == ["shard-01/001", "shard-02/001"]


def test_rebuilding_does_not_clobber_the_agents_status(tmp_path):
    build(tmp_path)
    status_path = tmp_path / "batches" / "synthetic" / "shard-01" / STATUS_NAME
    status_path.write_text(json.dumps({"shard": "shard-01", "done": ["shard-01/001"]}))

    build(tmp_path)

    assert json.loads(status_path.read_text())["done"] == ["shard-01/001"]


def test_every_batch_stays_under_the_size_an_agent_reads_in_one_go(tmp_path):
    manifest = build(
        tmp_path,
        corpus={
            "workitems": [
                real_item(f"KAFKA-{i}", description="word " * 900, fix_versions=[])
                for i in range(1, 41)
            ]
        },
        shards=1,
        batch_size=40,
    )

    assert manifest["sizes"]["max_bytes"] < MAX_BATCH_BYTES


def test_build_refuses_an_empty_slice(tmp_path):
    with pytest.raises(ValueError, match="no eligible work items"):
        build(tmp_path, corpus={"workitems": [real_item("KAFKA-1", type="Sub-task")]})


def test_a_rebuild_with_a_bigger_batch_size_removes_the_inputs_it_no_longer_plans(tmp_path):
    """Otherwise an agent writes an output for a batch that no longer exists."""
    corpus = {"workitems": [real_item(f"KAFKA-{i}", fix_versions=[]) for i in range(1, 5)]}
    build(tmp_path, corpus=corpus, shards=1, batch_size=1)
    shard = tmp_path / "batches" / "synthetic" / "shard-01"
    assert sorted(p.name for p in shard.glob("*.in.json")) == [
        "001.in.json",
        "002.in.json",
        "003.in.json",
        "004.in.json",
    ]

    manifest = build(tmp_path, corpus=corpus, shards=1, batch_size=4)

    assert [p.name for p in shard.glob("*.in.json")] == ["001.in.json"]
    assert manifest["sharding"]["removed_stale_inputs"] == [
        "shard-01/002.in.json",
        "shard-01/003.in.json",
        "shard-01/004.in.json",
    ]


def test_an_agents_output_is_never_removed_by_a_rebuild(tmp_path):
    corpus = {"workitems": [real_item(f"KAFKA-{i}", fix_versions=[]) for i in range(1, 5)]}
    build(tmp_path, corpus=corpus, shards=1, batch_size=1)
    out = tmp_path / "batches" / "synthetic" / "shard-01" / "004.out.json"
    out.write_text("{}", encoding="utf-8")

    build(tmp_path, corpus=corpus, shards=1, batch_size=4)

    assert out.is_file(), "merge reports an orphan output; build must not hide it"


def test_the_contract_and_the_schema_are_found_from_any_working_directory(tmp_path, monkeypatch):
    """They ship with the package; only `data/` is relative to where brain is run."""
    monkeypatch.chdir(tmp_path)

    assert SPEC_PATH.is_absolute() and SPEC_PATH.is_file()
    assert SCHEMA_PATH.is_absolute() and SCHEMA_PATH.is_file()


def test_the_batch_names_the_schema_repo_relatively_not_by_absolute_path(tmp_path):
    build(tmp_path)
    payload = json.loads(
        (tmp_path / "batches" / "synthetic" / "shard-01" / "001.in.json").read_text()
    )

    assert payload["schema_ref"] == "brain/synth/schema.json"
