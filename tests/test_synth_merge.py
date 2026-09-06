"""`brain synth merge`: what it accepts, what it rejects, and what a rerun must not do."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.synth.build import MANIFEST_NAME, SCHEMA_PATH
from brain.synth.merge import (
    LEDGER_NAME,
    LINK_TYPES,
    MAX_RETRIES,
    QUARANTINE_DIR,
    RETRY_DIR,
    TRUTH_NAME,
    Batch,
    MergeError,
    check_keys,
    normalise_truth,
    run_merge,
)
from brain.synth.models import BatchOutput
from tests.synth_helpers import (
    ado_item,
    batch_output,
    plant_canonical,
    read_jsonl_raw,
    real_item,
    synthetic_person,
    truth,
    write_output,
    xray_test,
)


def merge(tmp_path: Path, **corpus) -> tuple[dict, int]:
    """Plant a mini corpus (once — a rerun keeps what is there) and merge into it."""
    canonical = tmp_path / "canonical"
    if not (canonical / "workitems.jsonl").is_file():
        plant_canonical(canonical, **corpus)
    return run_merge(
        canonical_dir=canonical,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        echo=lambda _: None,
    )


def write_manifest(batches: Path, **over) -> Path:
    """A MANIFEST.json with just what merge reads out of it."""
    path = batches / "synthetic" / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"step": "synth.build", "batches": [], **over}), encoding="utf-8")
    return path


def synthetic(tmp_path: Path, name: str = "workitems") -> list[dict]:
    rows = read_jsonl_raw(tmp_path / "canonical" / f"{name}.jsonl")
    return [r for r in rows if r.get("synthetic")]


# --------------------------------------------------------------------------- happy path


def test_a_valid_batch_lands_in_canonical_with_the_real_records_kept(tmp_path):
    write_output(tmp_path / "batches", batch_output())

    report, code = merge(tmp_path)

    assert code == 0
    rows = read_jsonl_raw(tmp_path / "canonical" / "workitems.jsonl")
    assert {r["key"] for r in rows} == {"KAFKA-100", "KAFKA-101", "XT-10001", "ADO-10001"}
    assert all(r["synthetic"] for r in rows if r["source"] in {"xray", "ado"})
    assert report["counts"]["by_source"] == {"ado": 1, "xray": 1}


def test_the_truth_file_is_written_and_names_the_batches_it_came_from(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            truth=truth(
                text_only_links=[{"from_key": "ADO-10001", "to_key": "KAFKA-101"}],
                stale_states=[
                    {
                        "ado_key": "ADO-10001",
                        "jira_key": "KAFKA-100",
                        "ado_status": "Active",
                        "jira_status": "Resolved",
                    }
                ],
                renames=[
                    {
                        "test_key": "XT-10001",
                        "jira_key": "KAFKA-100",
                        # the phrase has to be in the Test's own text — merge checks
                        "test_phrase": "assignment path",
                        "jira_phrase": "partition assignment",
                    }
                ],
            )
        ),
    )

    merge(tmp_path)

    written = json.loads((tmp_path / "canonical" / TRUTH_NAME).read_text())
    assert written["batches"] == ["shard-01/001"]
    assert written["identity_map"] == {"ado:rao.jun": "jira:jrao"}
    assert written["counts"] == {
        "identity_map": 1,
        "text_only_links": 1,
        "stale_states": 1,
        "renames": 1,
        "duplicate_tests": 0,
    }


def test_two_batches_merge_into_one_truth_and_one_ledger(tmp_path):
    root = tmp_path / "batches"
    write_output(root, batch_output())
    write_output(
        root,
        batch_output(
            "shard-02/001",
            workitems=[xray_test("XT-20001", covers="KAFKA-101")],
            persons=[synthetic_person("dana.lee", "jira:dlee", "Lee, Dana")],
            truth=truth(identity_map={"ado:dana.lee": "jira:dlee"}),
        ),
    )

    report, code = merge(tmp_path)

    assert code == 0
    assert report["counts"]["workitems"] == 3
    written = json.loads((tmp_path / "canonical" / TRUTH_NAME).read_text())
    assert written["identity_map"] == {"ado:dana.lee": "jira:dlee", "ado:rao.jun": "jira:jrao"}
    ledger = json.loads((tmp_path / "canonical" / LEDGER_NAME).read_text())
    assert sorted(ledger["batches"]) == ["shard-01/001", "shard-02/001"]


# --------------------------------------------------------------------------- idempotence


def test_merging_twice_produces_no_duplicates(tmp_path):
    write_output(tmp_path / "batches", batch_output())

    merge(tmp_path)
    first = (tmp_path / "canonical" / "workitems.jsonl").read_text()
    merge(tmp_path)
    second = (tmp_path / "canonical" / "workitems.jsonl").read_text()

    assert first == second, "a second merge changed the canonical file"
    assert len(synthetic(tmp_path)) == 2


@pytest.mark.parametrize("name", ["workitems", "persons"])
def test_a_regenerated_batch_replaces_its_own_records_rather_than_adding_to_them(tmp_path, name):
    """The ledger is what makes the previous run's ids removable — no orphans."""
    root = tmp_path / "batches"
    write_output(root, batch_output())
    merge(tmp_path)

    write_output(
        root,
        batch_output(
            workitems=[xray_test("XT-10009")],
            persons=[synthetic_person("j.rao", "jira:jrao", "J. Rao")],
            truth=truth(identity_map={"ado:j.rao": "jira:jrao"}),
        ),
    )
    merge(tmp_path)

    keys = {r.get("key") or r["id"] for r in synthetic(tmp_path, name)}
    assert keys == ({"XT-10009"} if name == "workitems" else {"ado:j.rao"})


def test_a_record_a_human_added_by_hand_is_not_swept_away(tmp_path):
    """Only ids the ledger claims or a current batch carries are ever removed."""
    canonical = plant_canonical(tmp_path / "canonical")
    handmade = xray_test("XT-99999") | {"id": "xray:XT-99999"}
    (canonical / "workitems.jsonl").write_text(
        (canonical / "workitems.jsonl").read_text() + json.dumps(handmade) + "\n",
        encoding="utf-8",
    )
    write_output(tmp_path / "batches", batch_output())

    merge(tmp_path)

    assert "XT-99999" in {r["key"] for r in synthetic(tmp_path)}


# --------------------------------------------------------------------------- rejections


def test_a_key_that_a_real_work_item_already_uses_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(workitems=[ado_item("ADO-10001") | {"key": "ADO-10001"}]),
    )
    real = [real_item("KAFKA-100"), real_item("ADO-10001", id="jira:ADO-10001")]

    report, code = merge(tmp_path, workitems=real)

    assert code == 1
    assert report["batches"]["valid"] == 0
    assert any("already has this key" in e for e in report["rejected"][0]["errors"])
    assert not synthetic(tmp_path), "a rejected batch must not reach canonical"


def test_the_same_key_minted_by_two_batches_fails_the_second_only(tmp_path):
    root = tmp_path / "batches"
    write_output(root, batch_output())
    write_output(root, batch_output("shard-02/001"))

    report, code = merge(tmp_path)

    assert code == 1
    assert [r["batch"] for r in report["rejected"]] == ["shard-02/001"]
    assert {r["key"] for r in synthetic(tmp_path)} == {"XT-10001", "ADO-10001"}


def test_a_number_outside_the_shards_block_is_rejected(tmp_path):
    write_output(tmp_path / "batches", batch_output(workitems=[xray_test("XT-20001")]))

    report, code = merge(tmp_path)

    assert code == 1
    assert any("outside this shard's block" in e for e in report["rejected"][0]["errors"])


def test_a_prefix_paired_with_the_wrong_source_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(workitems=[xray_test("XT-10001", id="ado:XT-10001", source="ado")]),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("belongs to 'xray'" in e for e in report["rejected"][0]["errors"])


def test_a_truth_claim_about_a_record_the_layer_does_not_contain_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(truth=truth(duplicate_tests=[{"a": "XT-10001", "b": "XT-10777"}])),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("XT-10777 is not a record" in e for e in report["rejected"][0]["errors"])


def test_an_identity_map_pointing_at_nobody_real_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(truth=truth(identity_map={"ado:rao.jun": "jira:nobody"})),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("not a real person id" in e for e in report["rejected"][0]["errors"])


def test_unreadable_json_is_a_rejection_not_a_crash(tmp_path):
    path = tmp_path / "batches" / "synthetic" / "shard-01" / "001.out.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    report, code = merge(tmp_path)

    assert code == 1
    assert any("unreadable" in e for e in report["rejected"][0]["errors"])


def test_merge_refuses_when_there_are_no_batches_at_all(tmp_path):
    with pytest.raises(MergeError, match="no batches to merge"):
        merge(tmp_path)


# --------------------------------------------------------------------------- warnings


def test_a_link_to_a_real_key_outside_the_slice_warns_and_still_merges(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(workitems=[xray_test("XT-10001", covers="KAFKA-40404")]),
    )

    report, code = merge(tmp_path)

    assert code == 0, "a plausible reference outside the scoped slice is not a defect"
    assert report["warnings"]["dangling_targets"] == 1
    assert report["warnings"]["top_dangling"] == [["KAFKA-40404", 1]]
    assert len(synthetic(tmp_path)) == 1


def test_a_dangling_synthetic_target_is_an_error_not_a_warning(tmp_path):
    """The layer is self-contained: its writer controls both ends of an XT/ADO link."""
    write_output(
        tmp_path / "batches",
        batch_output(
            workitems=[xray_test("XT-10001", links=[{"type": "executes", "target": "XT-10404"}])]
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("XT-10404 is not in the layer" in e for e in report["rejected"][0]["errors"])


def test_a_link_into_a_batch_that_was_rejected_is_reported_not_silently_merged(tmp_path):
    """Cross-batch links are checked before the verdict; the loser's citations survive it."""
    root = tmp_path / "batches"
    write_output(
        root,
        batch_output(
            workitems=[
                xray_test("XT-10001", links=[{"type": "executes", "target": "XT-20001"}]),
                ado_item("ADO-10001"),
            ]
        ),
    )
    write_output(
        root,
        batch_output(
            "shard-02/001",
            workitems=[xray_test("XT-20001")],
            persons=[synthetic_person("dana.lee", "jira:dlee", "Lee, Dana")],
            # well-formed enough to parse, but claims a duplicate of a Test nobody wrote
            truth=truth(
                identity_map={"ado:dana.lee": "jira:dlee"},
                duplicate_tests=[{"a": "XT-20001", "b": "XT-20777"}],
            ),
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert [r["batch"] for r in report["rejected"]] == ["shard-02/001"]
    assert report["warnings"]["orphaned_by_rejection"] == 1
    assert report["warnings"]["orphaned_per_batch"] == {"shard-01/001": ["XT-10001 -> XT-20001"]}


def test_a_link_into_a_batch_that_did_not_even_parse_is_an_orphan_not_an_invention(tmp_path):
    """A schema-broken batch still says which keys it meant to write; a citation is not a lie."""
    root = tmp_path / "batches"
    write_output(
        root,
        batch_output(
            workitems=[
                xray_test("XT-10001", links=[{"type": "executes", "target": "XT-20001"}]),
                ado_item("ADO-10001"),
            ]
        ),
    )
    write_output(
        root,
        batch_output(
            "shard-02/001",
            workitems=[xray_test("XT-20001", type="Spike")],  # schema rejects: unknown type
            persons=[synthetic_person("dana.lee", "jira:dlee", "Lee, Dana")],
            truth=truth(identity_map={"ado:dana.lee": "jira:dlee"}),
        ),
    )

    report, code = merge(tmp_path)

    assert [r["batch"] for r in report["rejected"]] == ["shard-02/001"]
    assert report["warnings"]["orphaned_per_batch"] == {"shard-01/001": ["XT-10001 -> XT-20001"]}
    assert {r["key"] for r in synthetic(tmp_path)} == {"XT-10001", "ADO-10001"}


def test_a_link_to_a_pre_assigned_epic_another_shard_owns_is_not_an_error(tmp_path):
    """`epics[]` told this batch to link the key and not emit it; the owner merges later."""
    root = tmp_path / "batches"
    write_manifest(root, epics=[{"key": "ADO-1", "kip": "KIP-5", "title": "KIP-5"}])
    write_output(
        root,
        batch_output(
            workitems=[ado_item("ADO-10001", links=[{"type": "parent", "target": "ADO-1"}])]
        ),
    )

    report, code = merge(tmp_path)

    assert code == 0
    assert report["warnings"]["orphaned_per_batch"] == {"shard-01/001": ["ADO-10001 -> ADO-1"]}
    assert report["epics"]["missing"] == ["ADO-1"]


def test_next_ids_that_would_collide_next_batch_warns(tmp_path):
    write_output(tmp_path / "batches", batch_output(next_ids={"XT": 10001}))

    report, code = merge(tmp_path)

    assert code == 0
    warnings = report["warnings"]["per_batch"]["shard-01/001"]
    assert any("next_ids[XT]=10001 is not past 10001" in w for w in warnings)


# ------------------------------------------------------------------ retry and quarantine


def bad_batch(n: int) -> dict:
    """Invalid in a way whose *text* differs per attempt, so the sha256 changes."""
    return batch_output(workitems=[xray_test("XT-10001", type="Spike", title=f"attempt {n}")])


def test_a_failing_batch_copies_its_input_into_retry_with_the_reason(tmp_path):
    root = tmp_path / "batches"
    write_output(root, bad_batch(1))
    shard = root / "synthetic" / "shard-01"
    (shard / "001.in.json").write_text(json.dumps({"batch_id": "shard-01/001"}), encoding="utf-8")

    merge(tmp_path)

    payload = json.loads((shard / RETRY_DIR / "001.in.json").read_text())
    assert payload["batch_id"] == "shard-01/001"
    assert payload["_synth_retry"]["attempt"] == 1
    assert any("Spike" in r for r in payload["_synth_retry"]["reasons"])
    assert not (shard / QUARANTINE_DIR / "001.in.json").exists()


def test_the_third_failing_attempt_is_quarantined(tmp_path):
    root = tmp_path / "batches"
    shard = root / "synthetic" / "shard-01"
    for attempt in range(1, MAX_RETRIES + 2):
        write_output(root, bad_batch(attempt))
        report, code = merge(tmp_path)
        assert code == 1

    assert (shard / QUARANTINE_DIR / "001.in.json").is_file()
    assert not (shard / RETRY_DIR / "001.in.json").exists()
    state = json.loads((tmp_path / "canonical" / LEDGER_NAME).read_text())["failed"]
    assert state["shard-01/001"]["state"] == "quarantine"


def test_rerunning_over_an_unchanged_bad_batch_is_not_a_new_attempt(tmp_path):
    """Otherwise two idle merges would quarantine work the agent never got to redo."""
    write_output(tmp_path / "batches", bad_batch(1))

    merge(tmp_path)
    merge(tmp_path)
    report, _ = merge(tmp_path)

    ledger = json.loads((tmp_path / "canonical" / LEDGER_NAME).read_text())
    assert ledger["failed"]["shard-01/001"]["attempt"] == 1
    assert ledger["failed"]["shard-01/001"]["state"] == "retry"


def test_a_fixed_batch_clears_its_retry_copy(tmp_path):
    root = tmp_path / "batches"
    write_output(root, bad_batch(1))
    merge(tmp_path)
    assert (root / "synthetic" / "shard-01" / RETRY_DIR / "001.in.json").is_file()

    write_output(root, batch_output())
    report, code = merge(tmp_path)

    assert code == 0
    assert not (root / "synthetic" / "shard-01" / RETRY_DIR / "001.in.json").exists()
    assert json.loads((tmp_path / "canonical" / LEDGER_NAME).read_text())["failed"] == {}


# --------------------------------------------------------------------------- normalising


def test_the_specs_own_spelling_of_a_text_only_link_is_accepted_and_noted(tmp_path):
    # KAFKA-101, not KAFKA-100: the record formally links the latter, so it is not text-only
    raw = batch_output(
        truth=truth(text_only_links=[{"ado_key": "ADO-10001", "jira_key": "KAFKA-101"}])
    )
    write_output(tmp_path / "batches", raw)

    report, code = merge(tmp_path)

    assert code == 0
    assert any("normalised" in n for n in report["notes"]["shard-01/001"])
    written = json.loads((tmp_path / "canonical" / TRUTH_NAME).read_text())
    assert written["text_only_links"] == [{"from_key": "ADO-10001", "to_key": "KAFKA-101"}]


def test_normalise_truth_leaves_an_already_correct_link_alone():
    raw = {"truth": {"text_only_links": [{"from_key": "XT-1", "to_key": "KAFKA-1"}]}}

    assert normalise_truth(raw) == []
    assert raw["truth"]["text_only_links"] == [{"from_key": "XT-1", "to_key": "KAFKA-1"}]


# ------------------------------------------------------------------ the closed link set


def test_a_link_type_outside_the_closed_set_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            workitems=[xray_test("XT-10001", links=[{"type": "verifies", "target": "KAFKA-100"}])]
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    # the schema catches it first; `check_keys` is the second lock, asserted below
    assert any("'verifies' is not one of" in e for e in report["rejected"][0]["errors"])


def test_check_keys_rejects_a_link_type_the_schema_would_have_caught_first():
    """The second lock has to be real: a schema someone loosens must not open the gate."""
    batch = Batch(batch_id="shard-01/001", shard="shard-01", index=1, path=Path("x"), sha256="x")
    batch.output = BatchOutput.model_validate(
        batch_output(
            workitems=[xray_test("XT-10001", links=[{"type": "verifies", "target": "KAFKA-100"}])]
        )
    )

    check_keys(batch)

    assert any("link type 'verifies'" in e for e in batch.errors)


def test_the_schema_and_the_merge_agree_on_the_link_types():
    """Two places enforce it; a drift between them is a hole, so assert they are one list."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["$defs"]["link"]["properties"]["type"]["enum"]) == set(LINK_TYPES)


def test_a_parent_that_names_nothing_is_rejected_like_a_link(tmp_path):
    write_output(
        tmp_path / "batches", batch_output(workitems=[xray_test("XT-10001", parent="XT-10404")])
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any(
        "parent target XT-10404 is not in the layer" in e for e in report["rejected"][0]["errors"]
    )


def test_a_parent_outside_the_slice_warns_like_a_link(tmp_path):
    write_output(
        tmp_path / "batches", batch_output(workitems=[xray_test("XT-10001", parent="KAFKA-40404")])
    )

    report, code = merge(tmp_path)

    assert code == 0
    assert report["warnings"]["top_dangling"] == [["KAFKA-40404", 1]]


# ------------------------------------------------------- the reserved block is Epics only


def test_a_non_epic_key_from_the_reserved_block_is_rejected(tmp_path):
    """`ADO-1…ADO-143` are build's pre-assigned Epics; nothing else may mint below 10000."""
    write_output(tmp_path / "batches", batch_output(workitems=[xray_test("XT-7")]))

    report, code = merge(tmp_path)

    assert code == 1
    assert any("reserved block" in e for e in report["rejected"][0]["errors"])


def test_a_pre_assigned_epic_key_is_accepted_when_the_manifest_says_so(tmp_path):
    root = tmp_path / "batches"
    write_manifest(root, epics=[{"key": "ADO-1", "kip": "KIP-5", "title": "KIP-5"}])
    write_output(root, batch_output(workitems=[ado_item("ADO-1", "Epic")]))

    report, code = merge(tmp_path)

    assert code == 0
    assert report["epics"] == {
        "expected": 1,
        "present": 1,
        "missing": [],
        "duplicated": [],
        "extra": [],
        "ok": True,
    }


def test_a_pre_assigned_key_used_for_something_that_is_not_an_epic_is_rejected(tmp_path):
    root = tmp_path / "batches"
    write_manifest(root, epics=[{"key": "ADO-1", "kip": "KIP-5", "title": "KIP-5"}])
    write_output(root, batch_output(workitems=[ado_item("ADO-1", "Feature")]))

    report, code = merge(tmp_path)

    assert code == 1
    assert any("must be an Epic" in e for e in report["rejected"][0]["errors"])


def test_a_missing_pre_assigned_epic_is_reported_not_merged_away(tmp_path):
    root = tmp_path / "batches"
    write_manifest(
        root,
        epics=[
            {"key": "ADO-1", "kip": "KIP-5", "title": "KIP-5"},
            {"key": "ADO-2", "kip": "KIP-6", "title": "KIP-6"},
        ],
    )
    write_output(root, batch_output(workitems=[ado_item("ADO-1", "Epic")]))

    report, code = merge(tmp_path)

    assert code == 0, "a partly-merged layer is not a failure; the gap is a report line"
    assert report["epics"]["missing"] == ["ADO-2"]
    assert report["epics"]["ok"] is False


def test_an_epic_the_manifest_never_assigned_is_reported_as_extra(tmp_path):
    root = tmp_path / "batches"
    write_manifest(root, epics=[{"key": "ADO-1", "kip": "KIP-5", "title": "KIP-5"}])
    write_output(
        root, batch_output(workitems=[ado_item("ADO-1", "Epic"), ado_item("ADO-10001", "Epic")])
    )

    report, code = merge(tmp_path)

    assert report["epics"]["extra"] == ["ADO-10001"]


# ------------------------------------------------------------ truth checked against fact


def test_a_stale_state_that_the_record_contradicts_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            workitems=[ado_item("ADO-10001", status="Closed")],
            truth=truth(
                stale_states=[
                    {
                        "ado_key": "ADO-10001",
                        "jira_key": "KAFKA-100",
                        "ado_status": "Active",
                        "jira_status": "Resolved",
                    }
                ]
            ),
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any(
        "is 'Closed', not the claimed 'Active'" in e for e in report["rejected"][0]["errors"]
    )


def test_a_stale_state_that_misreports_the_real_jira_status_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            truth=truth(
                stale_states=[
                    {
                        "ado_key": "ADO-10001",
                        "jira_key": "KAFKA-100",
                        "ado_status": "Active",
                        "jira_status": "Open",  # KAFKA-100 is Resolved in the corpus
                    }
                ]
            )
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("KAFKA-100 is 'Resolved'" in e for e in report["rejected"][0]["errors"])


def test_a_text_only_link_the_record_also_states_formally_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            truth=truth(text_only_links=[{"from_key": "ADO-10001", "to_key": "KAFKA-100"}])
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("not text-only" in e for e in report["rejected"][0]["errors"])


def test_a_rename_phrase_that_appears_in_no_test_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            truth=truth(
                renames=[
                    {
                        "test_key": "XT-10001",
                        "jira_key": "KAFKA-100",
                        "test_phrase": "quorum controller",
                        "jira_phrase": "KRaft",
                    }
                ]
            )
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("does not say 'quorum controller'" in e for e in report["rejected"][0]["errors"])


def test_a_rename_phrase_is_matched_past_case_and_spacing(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(
            truth=truth(
                renames=[
                    {
                        "test_key": "XT-10001",
                        "jira_key": "KAFKA-100",
                        "test_phrase": "Assignment   Path",
                        "jira_phrase": "partition assignment",
                    }
                ]
            )
        ),
    )

    report, code = merge(tmp_path)

    assert code == 0


def test_a_duplicate_pair_naming_something_that_is_not_a_test_is_rejected(tmp_path):
    write_output(
        tmp_path / "batches",
        batch_output(truth=truth(duplicate_tests=[{"a": "XT-10001", "b": "ADO-10001"}])),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("is a User Story, not a Test" in e for e in report["rejected"][0]["errors"])


def test_a_synthetic_identity_that_reuses_the_real_username_is_rejected(tmp_path):
    """`ado:jrao` -> `jira:jrao` gives entity resolution nothing to resolve."""
    write_output(
        tmp_path / "batches",
        batch_output(
            workitems=[xray_test("XT-10001", reporter="jrao")],
            persons=[synthetic_person("jrao", "jira:jrao", "Rao, Jun")],
            truth=truth(identity_map={"ado:jrao": "jira:jrao"}),
        ),
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert any("reuses 'jrao'" in e for e in report["rejected"][0]["errors"])


# ------------------------------------------------------------ what the agents themselves say


def test_a_failure_the_agent_recorded_in_status_json_is_a_rejection_not_a_gap(tmp_path):
    root = tmp_path / "batches"
    write_output(root, batch_output())
    (root / "synthetic" / "shard-01" / "status.json").write_text(
        json.dumps(
            {
                "shard": "shard-01",
                "done": ["shard-01/001"],
                "failed": [{"batch": "shard-01/002", "reason": "no eligible bug for the defect"}],
            }
        ),
        encoding="utf-8",
    )

    report, code = merge(tmp_path)

    assert code == 1
    assert report["batches"]["reported_failed"] == 1
    reported = [r for r in report["rejected"] if r["source"] == "status.json"]
    assert reported == [
        {
            "batch": "shard-01/002",
            "source": "status.json",
            "output": None,
            "errors": ["no eligible bug for the defect"],
            "error_count": 1,
        }
    ]
    assert len(synthetic(tmp_path)) == 2, "the valid batch still merges"


def test_a_batch_marked_done_with_no_output_on_disk_warns(tmp_path):
    root = tmp_path / "batches"
    write_output(root, batch_output())
    (root / "synthetic" / "shard-01" / "status.json").write_text(
        json.dumps({"shard": "shard-01", "done": ["shard-01/001", "shard-01/002"], "failed": []}),
        encoding="utf-8",
    )

    report, code = merge(tmp_path)

    assert code == 0
    assert report["warnings"]["done_without_output"] == ["shard-01/002"]


# --------------------------------------------------------------------------- provenance


def test_the_ledger_carries_the_batch_shard_and_time_per_record(tmp_path):
    """`brain load` stamps a synthetic node with the batch that wrote it from this map."""
    write_output(tmp_path / "batches", batch_output())

    merge(tmp_path)

    ledger = json.loads((tmp_path / "canonical" / LEDGER_NAME).read_text())
    assert set(ledger["provenance"]) == {"xray:XT-10001", "ado:ADO-10001", "ado:rao.jun"}
    entry = ledger["provenance"]["xray:XT-10001"]
    assert entry["batch_id"] == "shard-01/001" and entry["shard"] == "shard-01"
    assert entry["merged_at"] == ledger["updated_at"]
