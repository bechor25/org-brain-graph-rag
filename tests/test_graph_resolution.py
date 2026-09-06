"""The half of `brain resolve` that lives in `brain load`: applying the ledger."""

from __future__ import annotations

from brain.canon.models import Identity, Person
from brain.graph.corpus import load_corpus
from brain.graph.loaders.persons import node_rows
from brain.graph.provenance import SyntheticProvenance
from brain.graph.resolution import Resolution, fold_persons
from brain.resolve.ledger import ResolutionLedger


def person(pid: str, source: str, key: str, display: str) -> Person:
    return Person(id=pid, identities=[Identity(source=source, key=key, display=display)])


def three_identities() -> list[Person]:
    return [
        person("jira:jrao", "jira", "jrao", "Jun Rao"),
        person("git:junrao@example.org", "git", "junrao@example.org", "Jun Rao"),
        person("confluence:rao.jun", "confluence", "rao.jun", "Rao, Jun"),
    ]


def ledger_for(canonical: str, members: list[str], tier: int = 1) -> ResolutionLedger:
    ledger = ResolutionLedger()
    ledger.record(
        "person",
        [(m, canonical, {"tier": tier, "rule": "email", "score": 1.0}) for m in members],
        resolved_at="2026-09-06T00:00:00+00:00",
    )
    ledger.available = True
    return ledger


def test_without_a_ledger_every_identity_stays_its_own_record():
    kept, resolution = fold_persons(three_identities(), ResolutionLedger())
    assert len(kept) == 3 and not resolution.available


def test_the_ledger_folds_three_identities_into_one_record():
    ledger = ledger_for("jira:jrao", ["git:junrao@example.org", "confluence:rao.jun"])
    kept, resolution = fold_persons(three_identities(), ledger)

    assert [p.id for p in kept] == ["jira:jrao"]
    assert {(i.source, i.key) for i in kept[0].identities} == {
        ("jira", "jrao"),
        ("git", "junrao@example.org"),
        ("confluence", "rao.jun"),
    }
    assert kept[0].resolved is True
    assert resolution.folded == 2
    assert resolution.merged_from["jira:jrao"] == ["confluence:rao.jun", "git:junrao@example.org"]
    assert resolution.aliases["jira:jrao"] == ["Rao, Jun"]


def test_a_canonical_id_no_canonical_record_describes_is_reported_not_invented():
    ledger = ledger_for("jira:ghost", ["git:junrao@example.org"])
    kept, resolution = fold_persons(three_identities(), ledger)

    assert len(kept) == 3
    assert resolution.unknown_canonical == ["git:junrao@example.org -> jira:ghost"]


def test_an_identity_the_canonical_files_no_longer_hold_is_reported_not_dropped_silently():
    ledger = ledger_for("jira:jrao", ["git:gone@example.org"])
    kept, resolution = fold_persons(three_identities(), ledger)

    assert len(kept) == 3
    assert resolution.unknown_identities == ["git:gone@example.org"]


def test_load_routes_every_identity_of_a_merged_person_to_the_survivor(tmp_path):
    path = tmp_path / "persons.jsonl"
    path.write_text(
        "\n".join(p.model_dump_json() for p in three_identities()) + "\n", encoding="utf-8"
    )
    ledger = ledger_for("jira:jrao", ["git:junrao@example.org", "confluence:rao.jun"])

    corpus = load_corpus(tmp_path, ledger)

    assert len(corpus.persons) == 1
    # Every edge in `brain load` resolves through these two lookups.
    assert corpus.person("git", "junrao@example.org") == "jira:jrao"
    assert corpus.person("confluence", "rao.jun") == "jira:jrao"
    assert corpus.person_mentioned("jira", "jrao") == "jira:jrao"


def test_the_node_row_repeats_the_ledger_and_never_invents_a_resolution():
    ledger = ledger_for("jira:jrao", ["git:junrao@example.org"], tier=2)
    kept, resolution = fold_persons(three_identities(), ledger)
    rows = {r["key"]: r for r in node_rows(kept, SyntheticProvenance(entries={}), resolution)}

    merged = rows["jira:jrao"]["props"]
    assert merged["resolved"] is True
    assert merged["resolution_tier"] == 2
    assert merged["merged_from"] == ["git:junrao@example.org"]
    assert merged["identity_keys"] == ["jira:jrao", "git:junrao@example.org"]

    untouched = rows["confluence:rao.jun"]
    assert "resolved" not in untouched["props"]
    assert untouched["on_create"] == {"resolved": False}


def test_without_a_resolution_the_row_is_exactly_what_load_always_wrote():
    rows = node_rows(three_identities(), SyntheticProvenance(entries={}), Resolution())
    assert all("resolved" not in r["props"] for r in rows)
    assert all(r["on_create"] == {"resolved": False} for r in rows)
