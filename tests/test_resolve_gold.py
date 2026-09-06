from __future__ import annotations

import json

import pytest

from brain.canon.models import Identity, Person
from brain.resolve.gold import (
    GOLD_NAME,
    GoldError,
    hard_negatives,
    person_gold,
    positives,
    read_gold,
    run_gold,
    truth_groups,
)


def write_corpus(tmp_path, identity_map, displays):
    (tmp_path / "synthetic_truth.json").write_text(
        json.dumps({"identity_map": identity_map, "note": "evaluation only"}), encoding="utf-8"
    )
    people = [
        Person(
            id=pid,
            identities=[Identity(source=pid.split(":")[0], key=pid.split(":")[1], display=d)],
        )
        for pid, d in displays.items()
    ]
    (tmp_path / "persons.jsonl").write_text(
        "\n".join(p.model_dump_json() for p in people) + "\n", encoding="utf-8"
    )


def test_truth_groups_put_the_real_identity_in_its_own_group():
    groups = truth_groups({"ado:a": "jira:x", "xray:b": "jira:x", "ado:c": "jira:y"})
    assert groups == {"jira:x": ["ado:a", "jira:x", "xray:b"], "jira:y": ["ado:c", "jira:y"]}


def test_positives_are_every_pair_inside_a_group_including_the_real_one():
    groups = truth_groups({"ado:a": "jira:x", "xray:b": "jira:x"})
    assert positives(groups) == [
        ("ado:a", "jira:x"),
        ("ado:a", "xray:b"),
        ("jira:x", "xray:b"),
    ]


def test_hard_negatives_are_lookalikes_from_different_real_people_only():
    groups = truth_groups({"ado:an1": "jira:sanghyeok", "ado:an2": "jira:shichao"})
    displays = {
        "ado:an1": "S. An",
        "ado:an2": "S. An",
        "jira:sanghyeok": "Sanghyeok An",
        "jira:shichao": "Shichao An",
    }
    pairs = {(a, b) for a, b, _ in hard_negatives(groups, displays)}

    assert ("ado:an1", "ado:an2") in pairs
    # two identities of the same real person are never a negative
    assert ("ado:an1", "jira:sanghyeok") not in pairs
    # and nothing that does not look alike gets in
    assert all(a != b for a, b in pairs)


def test_a_pair_with_no_shared_token_is_not_a_hard_negative():
    groups = truth_groups({"ado:a": "jira:x", "ado:b": "jira:y"})
    displays = {
        "ado:a": "Jun Rao",
        "ado:b": "Bruno Cadonna",
        "jira:x": "Jun Rao",
        "jira:y": "Bruno Cadonna",
    }
    assert hard_negatives(groups, displays) == []


def test_person_gold_labels_positives_same_and_lookalikes_different(tmp_path):
    write_corpus(
        tmp_path,
        {"ado:an1": "jira:sanghyeok", "ado:an2": "jira:shichao"},
        {
            "ado:an1": "S. An",
            "ado:an2": "S. An",
            "jira:sanghyeok": "Sanghyeok An",
            "jira:shichao": "Shichao An",
        },
    )
    rows = person_gold(tmp_path)
    by_pair = {(r["a"], r["b"]): r for r in rows}

    assert by_pair[("ado:an1", "jira:sanghyeok")]["label"] == "same"
    assert by_pair[("ado:an1", "jira:sanghyeok")]["source"] == "identity_map"
    assert by_pair[("ado:an1", "ado:an2")]["label"] == "different"
    assert by_pair[("ado:an1", "ado:an2")]["source"] == "hard_negative"
    assert by_pair[("ado:an1", "ado:an2")]["a_display"] == "S. An"


def test_a_missing_truth_file_is_a_refusal_with_a_reason(tmp_path):
    with pytest.raises(GoldError, match="identity map"):
        person_gold(tmp_path)


def test_run_gold_writes_one_sorted_json_object_per_line(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    write_corpus(
        canonical,
        {"ado:a": "jira:x"},
        {"ado:a": "A Person", "jira:x": "A Person"},
    )
    eval_dir = tmp_path / "eval"
    summary, code = run_gold(
        canonical_dir=canonical,
        eval_dir=eval_dir,
        batches_dir=tmp_path / "batches",
        kinds=["person"],
        echo=lambda _m: None,
    )

    assert code == 0 and summary["kinds"]["person"]["same"] == 1
    rows = read_gold(eval_dir / GOLD_NAME)
    assert rows == [
        {
            "a": "ado:a",
            "a_display": "A Person",
            "b": "jira:x",
            "b_display": "A Person",
            "kind": "person",
            "label": "same",
            "source": "identity_map",
        }
    ]


def test_rebuilding_one_kind_keeps_the_other_kinds_rows(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    write_corpus(canonical, {"ado:a": "jira:x"}, {"ado:a": "A", "jira:x": "A"})
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / GOLD_NAME).write_text(
        json.dumps({"kind": "entity", "a": "Feature|a", "b": "Feature|b", "label": "same"}) + "\n",
        encoding="utf-8",
    )

    run_gold(
        canonical_dir=canonical,
        eval_dir=eval_dir,
        batches_dir=tmp_path / "batches",
        kinds=["person"],
        echo=lambda _m: None,
    )
    kinds = {r["kind"] for r in read_gold(eval_dir / GOLD_NAME)}
    assert kinds == {"person", "entity"}
