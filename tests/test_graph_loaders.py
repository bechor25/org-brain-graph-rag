"""Row building for every loader — the decisions that happen before any Cypher runs."""

from __future__ import annotations

from brain.canon.models import Identity, Ref
from brain.graph.corpus import load_corpus
from brain.graph.loaders import changes as changes_loader
from brain.graph.loaders import containers as containers_loader
from brain.graph.loaders import refs as refs_loader
from brain.graph.provenance import SyntheticProvenance
from tests.graph_helpers import (
    change,
    container,
    corpus,
    document,
    person,
    work_item,
)

NO_PROV = SyntheticProvenance(entries={})


def test_refs_only_reach_targets_that_exist_and_the_rest_are_counted_by_kind():
    c = corpus(
        workitems=[
            work_item(
                key="KAFKA-100",
                refs=[
                    Ref(kind="issue", key="KAFKA-101"),  # exists
                    Ref(kind="issue", key="KAFKA-9999"),  # outside the slice
                    Ref(kind="kip", key="KIP-5"),  # exists
                    Ref(kind="kip", key="KIP-999"),  # never harvested
                    Ref(kind="pr", key="14001"),  # exists
                    Ref(kind="pr", key="99999"),  # outside the slice
                    Ref(kind="url", key="https://example.org"),  # no node kind
                ],
            ),
            work_item(key="KAFKA-101"),
        ],
        documents=[document(key="KIP-5")],
        changes=[change(id="pr:14001", kind="pr")],
    )
    rows, stats = refs_loader.build_rows(c)
    assert stats["dangling_refs"] == {"issue": 1, "kip": 1, "pr": 1}
    assert [r["dst"] for r in rows[("WorkItem", "issue")]] == ["KAFKA-101"]
    assert [r["dst"] for r in rows[("WorkItem", "kip")]] == ["KIP-5"]
    assert [r["dst"] for r in rows[("WorkItem", "pr")]] == [14001]


def test_a_formal_link_ref_beats_the_text_mention_of_the_same_target():
    c = corpus(
        workitems=[
            work_item(
                key="KAFKA-100",
                refs=[
                    Ref(kind="issue", key="KAFKA-101", via="text"),
                    Ref(kind="issue", key="KAFKA-101", via="link"),
                ],
            ),
            work_item(key="KAFKA-101"),
        ]
    )
    rows, _ = refs_loader.build_rows(c)
    (row,) = rows[("WorkItem", "issue")]
    assert row["props"] == {"via": "link", "kinds": ["issue"]}


def test_user_refs_become_mentions_only_when_the_person_exists():
    c = corpus(
        workitems=[
            work_item(key="KAFKA-100", refs=[Ref(kind="user", key="dlee")]),
            work_item(key="KAFKA-101", refs=[Ref(kind="user", key="nobody")]),
        ],
        persons=[person("jira", "dlee")],
    )
    rows, stats = refs_loader.build_rows(c)
    assert [r["dst"] for r in rows[("__mentions__", "WorkItem")]] == ["jira:dlee"]
    assert stats["dangling_refs"]["user"] == 1


def test_a_mention_resolves_across_sources_when_only_one_person_claims_the_key():
    """A Confluence page writing `@mjsax` means the Jira person; nothing else it can be."""
    c = corpus(
        documents=[document(key="KIP-5", refs=[Ref(kind="user", key="mjsax")])],
        persons=[person("jira", "mjsax")],
    )
    rows, stats = refs_loader.build_rows(c)
    assert [r["dst"] for r in rows[("__mentions__", "Document")]] == ["jira:mjsax"]
    assert "user" not in stats["dangling_refs"]


def test_a_person_with_several_identities_is_found_by_each_of_them():
    p = person(
        "jira",
        "jrao",
        identities=[
            Identity(source="jira", key="jrao", display="Jun Rao"),
            Identity(source="git", key="junrao@example.org", email="junrao@example.org"),
        ],
    )
    c = corpus(persons=[p])
    assert c.person("git", "junrao@example.org") == "jira:jrao"
    assert c.person("jira", "jrao") == "jira:jrao"
    assert c.person("jira", "unknown") is None


def test_container_kinds_without_a_label_are_reported_not_guessed():
    grouped, unknown = containers_loader.node_rows(
        [container("component", "clients"), container("testset", "regression")], NO_PROV
    )
    assert list(grouped) == ["Component"]
    assert unknown == {"testset": 1}


def test_the_same_container_name_from_two_sources_is_one_node():
    grouped, _ = containers_loader.node_rows(
        [container("component", "clients"), container("component", "clients")], NO_PROV
    )
    assert len(grouped["Component"]) == 1


def test_commit_and_pull_request_rows_split_one_canonical_type_into_two_labels():
    rows = [
        change(id="a" * 40, kind="commit", pr="pr:14001", files=["a.java", "a.java"]),
        change(id="pr:14001", kind="pr"),
        change(id="pr:not-a-number", kind="pr"),
    ]
    commits = changes_loader.commit_rows(rows, NO_PROV)
    prs, unusable = changes_loader.pr_rows(rows, NO_PROV)
    assert [r["key"] for r in commits] == ["a" * 40]
    assert commits[0]["props"]["pr"] == "pr:14001"
    assert [r["key"] for r in prs] == [14001]
    assert unusable == 1
    assert [r["key"] for r in changes_loader.file_rows(rows)] == ["a.java"]


def test_the_mini_fixture_loads_as_a_corpus():
    c = load_corpus_from_fixture()
    assert len(c.workitems) == 6
    assert c.pr_numbers == {14001}
    assert c.person("git", "junrao@example.org") == "jira:jrao"


def load_corpus_from_fixture():
    from pathlib import Path

    return load_corpus(Path("data/fixtures/mini"))
