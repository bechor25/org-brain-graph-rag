"""Jira mapper: the golden record, and the decisions it encodes."""

from __future__ import annotations

from brain.canon.mappers.jira import map_issues
from tests.canon_helpers import issue, load_fixture


def one(raw):
    return map_issues([raw]).workitems[0]


def test_golden_issue_maps_to_the_expected_workitem():
    """A real KAFKA issue, verbatim from `data/raw/`, against a frozen canonical record."""
    raw = load_fixture("jira", "raw_issue.json")
    expected = load_fixture("jira", "expected_workitem.json")

    assert one(raw).model_dump(mode="json", by_alias=True) == expected


def test_both_directions_of_a_formal_link_are_preserved():
    wi = one(load_fixture("jira", "raw_issue.json"))

    assert [(link.type, link.target, link.direction) for link in wi.links] == [
        ("completes", "KAFKA-12999", "in"),
        ("reference", "KAFKA-18881", "out"),
    ]


def test_a_text_mention_that_duplicates_a_formal_link_counts_as_via_link():
    raw = issue(
        "KAFKA-100",
        description="caused by KAFKA-200, see also KAFKA-300",
        issuelinks=[
            {"type": {"name": "Problem/Incident"}, "outwardIssue": {"key": "KAFKA-200"}},
        ],
    )
    wi = one(raw)

    assert [(r.key, r.via) for r in wi.refs] == [("KAFKA-200", "link"), ("KAFKA-300", "text")]


def test_a_formal_link_with_no_text_mention_still_becomes_a_ref():
    raw = issue(
        "KAFKA-100",
        description="no keys here",
        issuelinks=[{"type": {"name": "Blocker"}, "inwardIssue": {"key": "KAFKA-200"}}],
    )

    assert [(r.key, r.via) for r in one(raw).refs] == [("KAFKA-200", "link")]


def test_refs_are_filtered_by_the_allowlist_and_the_blacklist():
    raw = issue(
        "KAFKA-100",
        description="KAFKA-200 encoded UTF-8, template key KAFKA-1, SHA-256, KIP-848",
    )
    bundle = map_issues([raw])

    assert [(r.kind, r.key) for r in bundle.workitems[0].refs] == [
        ("issue", "KAFKA-200"),
        ("kip", "KIP-848"),
    ]
    assert bundle.refs.removed["UTF-8"] == 1
    assert bundle.refs.removed["KAFKA-1"] == 1
    assert bundle.refs.removed["SHA-256"] == 1
    assert bundle.refs.removed_by_reason == {"blacklisted": 1, "not_in_allowlist": 2}


def test_a_synthetic_key_in_real_text_is_kept_as_a_ref():
    """Once the Xray/ADO layer exists, a real issue may name one of its keys."""
    raw = issue("KAFKA-100", description="Covered by XT-12, tracked as ADO-7.")

    assert [(r.kind, r.key) for r in one(raw).refs] == [("issue", "XT-12"), ("issue", "ADO-7")]


def test_an_issue_does_not_reference_itself():
    raw = issue("KAFKA-100", description="KAFKA-100 is this issue; KAFKA-200 is not")

    assert [r.key for r in one(raw).refs] == ["KAFKA-200"]


def test_every_participating_identity_becomes_a_person():
    raw = issue(
        "KAFKA-100",
        reporter={"name": "reporter", "displayName": "The Reporter"},
        assignee={"name": "assignee"},
        creator={"name": "creator"},
        comment={"comments": [{"author": {"name": "commenter"}, "body": "hi", "created": None}]},
    )
    raw["changelog"] = {
        "histories": [
            {
                "author": {"name": "editor"},
                "created": "2024-01-02T00:00:00.000+0000",
                "items": [{"field": "status", "fromString": "Open", "toString": "Resolved"}],
            }
        ]
    }
    bundle = map_issues([raw])

    assert set(bundle.persons) == {
        "jira:reporter",
        "jira:assignee",
        "jira:creator",
        "jira:commenter",
        "jira:editor",
    }
    assert bundle.persons["jira:reporter"].identities[0].display == "The Reporter"
    assert bundle.persons["jira:reporter"].resolved is False
    # The work item stores the source key, not the (pre-resolution) person id.
    assert bundle.workitems[0].reporter == "reporter"


def test_components_and_versions_become_containers():
    raw = issue(
        "KAFKA-100",
        components=[{"name": "streams"}, {"name": "clients"}],
        fixVersions=[{"name": "3.7.0"}],
        versions=[{"name": "3.6.0"}],
    )
    bundle = map_issues([raw])

    assert set(bundle.containers) == {
        "jira:component:streams",
        "jira:component:clients",
        "jira:version:3.7.0",
        "jira:version:3.6.0",
    }
    assert bundle.containers["jira:version:3.7.0"].kind == "version"
    assert bundle.workitems[0].fix_versions == ["3.7.0"]
    assert bundle.workitems[0].affects_versions == ["3.6.0"]


def test_changelog_keeps_the_human_readable_values():
    raw = issue("KAFKA-100")
    raw["changelog"] = {
        "histories": [
            {
                "author": {"name": "editor"},
                "created": "2024-01-02T00:00:00.000+0000",
                "items": [
                    {
                        "field": "status",
                        "from": "1",
                        "fromString": "Open",
                        "to": "5",
                        "toString": "Resolved",
                    }
                ],
            }
        ]
    }
    entry = one(raw).changelog[0]

    assert (entry.field, entry.from_, entry.to, entry.by) == (
        "status",
        "Open",
        "Resolved",
        "editor",
    )
    assert (entry.from_id, entry.to_id) == ("1", "5")


def test_an_assignee_change_keeps_the_identity_keys_not_only_the_display_names():
    """`ASSIGNED_TO` needs the keys: display names are shared by different people here."""
    raw = issue("KAFKA-100")
    raw["changelog"] = {
        "histories": [
            {
                "author": {"name": "editor"},
                "created": "2024-01-02T00:00:00.000+0000",
                "items": [
                    {
                        "field": "assignee",
                        "from": "jrao",
                        "fromString": "Jun Rao",
                        "to": "chia7712",
                        "toString": "Chia-Ping Tsai",
                    }
                ],
            }
        ]
    }
    entry = one(raw).changelog[0]

    assert (entry.from_id, entry.to_id) == ("jrao", "chia7712")
    assert (entry.from_, entry.to) == ("Jun Rao", "Chia-Ping Tsai")


def test_the_stats_census_link_types_and_changelog_fields():
    raw = issue(
        "KAFKA-100",
        issuelinks=[{"type": {"name": "Blocker"}, "outwardIssue": {"key": "KAFKA-200"}}],
    )
    raw["changelog"] = {
        "histories": [
            {
                "author": {"name": "e"},
                "created": "2024-01-02T00:00:00.000+0000",
                "items": [{"field": "RemoteIssueLink"}, {"field": "status"}],
            }
        ]
    }
    stats = map_issues([raw]).stats

    assert stats["link_types"] == {"blocker": 1}
    assert stats["changelog_fields"] == {"RemoteIssueLink": 1, "status": 1}
    assert stats["changelog_remote_issue_link_noise"] == 1


def test_an_issue_without_a_component_is_warned_about():
    bundle = map_issues([issue("KAFKA-100", components=[])])

    assert bundle.workitems[0].components == []
    assert bundle.warnings == [
        {"source": "jira", "code": "issue_without_component", "key": "KAFKA-100"}
    ]
    assert bundle.stats["without_components"] == 1


def test_unmapped_fields_count_only_the_records_that_carried_a_value():
    bundle = map_issues(
        [
            issue("KAFKA-1", votes={"votes": 3}, customfield_1=None),
            issue("KAFKA-2", votes=None, customfield_1=None),
        ]
    )
    unmapped = bundle.stats["unmapped_fields"]

    assert unmapped["records"] == 2
    assert unmapped["dropped_with_data"]["fields.votes"] == 1
    assert "fields.customfield_1" in unmapped["dropped_always_empty"]


def test_the_golden_issue_reports_what_the_canonical_model_drops():
    bundle = map_issues([load_fixture("jira", "raw_issue.json")])
    dropped = bundle.stats["unmapped_fields"]["dropped_with_data"]

    assert dropped["fields.votes"] == 1
    assert dropped["fields.watches"] == 1
    assert dropped["fields.workratio"] == 1
    assert any(name.startswith("fields.customfield_") for name in dropped)


def test_resolution_and_resolved_at_are_kept():
    wi = one(load_fixture("jira", "raw_issue.json"))

    assert wi.resolution == "Fixed"
    assert wi.resolved_at.isoformat() == "2025-11-03T13:45:53+00:00"
    # ...and they are no longer reported as lost in normalization
    dropped = map_issues([load_fixture("jira", "raw_issue.json")]).stats["unmapped_fields"]
    assert "fields.resolution" not in dropped["dropped_with_data"]
    assert "fields.resolutiondate" not in dropped["dropped_with_data"]


def test_jira_mentions_in_comments_become_user_refs():
    raw = issue(
        "KAFKA-100",
        description="[~jrao] please look at kafka-200",
        comment={
            "comments": [{"author": {"name": "a"}, "body": "[~chia7712] ok", "created": None}]
        },
    )

    assert [(r.kind, r.key) for r in one(raw).refs] == [
        ("user", "jrao"),
        ("issue", "KAFKA-200"),
        ("user", "chia7712"),
    ]


def test_container_names_are_stripped_and_reported():
    bundle = map_issues(
        [
            issue(
                "KAFKA-100",
                components=[{"name": "producer "}, {"name": "clients"}],
                fixVersions=[{"name": " 3.7.0"}],
            )
        ]
    )

    assert bundle.workitems[0].components == ["producer", "clients"]
    assert bundle.workitems[0].fix_versions == ["3.7.0"]
    assert set(bundle.containers) == {
        "jira:component:producer",
        "jira:component:clients",
        "jira:version:3.7.0",
    }
    normalized = bundle.stats["normalized_container_names"]
    assert normalized["names_changed"] == 2 and normalized["occurrences"] == 2


def test_the_two_ref_percentages_answer_different_questions():
    """A mention Jira also states as a link counts for one metric and not the other."""
    bundle = map_issues(
        [
            issue(
                "KAFKA-1",
                description="caused by KAFKA-200",
                issuelinks=[{"type": {"name": "Blocker"}, "outwardIssue": {"key": "KAFKA-200"}}],
            ),
            issue("KAFKA-2", description="see KAFKA-300"),
            issue("KAFKA-3", description="nothing here"),
        ]
    )

    assert bundle.stats["workitems_with_any_text_issue_mention"] == 2
    assert bundle.stats["workitems_with_text_only_issue_ref"] == 1
