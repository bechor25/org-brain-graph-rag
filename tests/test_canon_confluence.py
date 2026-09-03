"""Confluence mapper: storage → Markdown, and the `KIP-N` collision rule."""

from __future__ import annotations

from brain.canon.mappers.confluence import decide_keys, map_pages, storage_to_markdown
from tests.canon_helpers import load_fixture, page


def one(raw):
    return map_pages([raw]).documents[0]


def test_golden_page_maps_to_the_expected_document():
    """A real KIP page, verbatim from `data/raw/`, against a frozen canonical record."""
    raw = load_fixture("confluence", "raw_page.json")
    expected = load_fixture("confluence", "expected_document.json")

    assert one(raw).model_dump(mode="json", by_alias=True) == expected


# --------------------------------------------------------------------------- storage → md


def test_headings_links_and_lists_survive_the_conversion():
    md = storage_to_markdown(
        '<h1>Motivation</h1><p>see <a href="https://example.com/x">here</a></p>'
        "<ul><li>one</li><li>two</li></ul>"
    )

    assert md.startswith("# Motivation")
    assert "[here](https://example.com/x)" in md
    assert "* one" in md and "* two" in md


def test_the_jira_macro_key_stays_a_word_the_regex_can_find():
    """`…serverId…KAFKA-20186` glued together is a ref the graph never gets."""
    storage = (
        '<ac:structured-macro ac:name="jira">'
        '<ac:parameter ac:name="serverId">5aa69414-a9e9</ac:parameter>'
        '<ac:parameter ac:name="key">KAFKA-20186</ac:parameter>'
        "</ac:structured-macro>"
    )

    assert " KAFKA-20186" in f" {storage_to_markdown(storage)}"


def test_a_user_macro_becomes_a_mention_the_regexes_can_see():
    storage = '<p>thanks <ac:link><ri:user ri:userkey="073590d2893a" /></ac:link></p>'

    assert "@073590d2893a" in storage_to_markdown(storage)


def test_an_internal_page_link_becomes_its_target_title():
    storage = '<p>see <ac:link><ri:page ri:content-title="KIP-848: Next Gen" /></ac:link></p>'

    assert "KIP-848: Next Gen" in storage_to_markdown(storage)


def test_a_code_macro_becomes_a_fenced_block():
    storage = (
        '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
        '<![CDATA[props.put("group.id", "g");]]>'
        "</ac:plain-text-body></ac:structured-macro>"
    )
    md = storage_to_markdown(storage)

    assert "```" in md and 'props.put("group.id", "g");' in md


def test_tables_become_readable_text():
    storage = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"

    assert "| a | b |" in storage_to_markdown(storage)


def test_identifiers_are_not_backslash_escaped():
    assert storage_to_markdown("<p>group_id and max_bytes</p>") == "group_id and max_bytes"


# --------------------------------------------------------------------------- KIP keys


def collision(*titles):
    pages = [
        page(str(1000 + i), t, body=f"<p>{'x' * (100 * (i + 1))}</p>") for i, t in enumerate(titles)
    ]
    return pages, decide_keys(pages)


def test_a_draft_never_owns_the_number():
    pages, (keys, decisions) = collision(
        "[DRAFT] KIP-271: Add redirector", "KIP-271: Add redirector"
    )

    assert keys["1001"] == "KIP-271"
    assert keys["1000"] == "confluence:1000"
    assert decisions[0]["canonical"]["id"] == "1001"
    assert decisions[0]["ambiguous"] is False


def test_copy_of_old_and_release_notes_pages_lose_to_the_proposal():
    for variant in (
        "Copy of KIP-1139: OAuth",
        "OLD KIP-1139: OAuth",
        "OAuth (KIP-1139) - Preview Release Notes",
    ):
        pages, (keys, _) = collision(variant, "KIP-1139: OAuth")
        assert keys[pages[1]["id"]] == "KIP-1139", variant


def test_a_page_that_only_mentions_the_number_loses_to_the_one_that_starts_with_it():
    _, (keys, _) = collision("Aggregation details for KIP-450", "KIP-450: Sliding Windows")

    assert keys["1001"] == "KIP-450"


def test_two_real_kips_sharing_a_number_are_decided_by_body_and_flagged_ambiguous():
    pages, (keys, decisions) = collision("KIP-1001: Metric A", "KIP-1001: Metric B")

    assert keys["1001"] == "KIP-1001"  # the longer body
    assert decisions[0]["ambiguous"] is True
    assert decisions[0]["decided_by"] == "body_length"
    assert decisions[0]["variants"] == [{"id": "1000", "title": "KIP-1001: Metric A"}]


def test_the_report_names_every_ambiguous_collision_and_labels_the_demoted_page():
    bundle = map_pages(
        [
            page("1000", "KIP-1001: Metric A"),
            page("1001", "KIP-1001: Metric B", body="<p>a longer body</p>"),
        ]
    )
    demoted = next(d for d in bundle.documents if d.id == "confluence:1000")

    assert demoted.labels == ["kip-variant", "ambiguous-kip"]
    assert bundle.stats["kip_key"]["ambiguous_kips"] == [
        {
            "key": "KIP-1001",
            "canonical_page_id": "1001",
            "demoted_page_id": "1000",
            "titles": {"canonical": "KIP-1001: Metric B", "demoted": "KIP-1001: Metric A"},
        }
    ]


def test_a_marker_decision_is_not_ambiguous_and_says_which_term_decided():
    _, (_, decisions) = collision("[DRAFT] KIP-9: a", "KIP-9: a")

    assert decisions[0]["ambiguous"] is False
    assert decisions[0]["decided_by"] == "starts_with_key"


def test_a_variant_becomes_a_page_that_points_at_the_canonical_key():
    docs = map_pages(
        [
            page("1", "KIP-848: The Next Generation", body="<p>" + "x" * 500 + "</p>"),
            page("2", "The Next Generation (KIP-848) - Preview Release Notes"),
        ]
    ).documents
    canonical, variant = docs[0], docs[1]

    assert (canonical.key, canonical.kind) == ("KIP-848", "KIP")
    assert (variant.key, variant.kind) == ("confluence:2", "Page")
    assert "kip-variant" in variant.labels
    assert variant.kip_of == "KIP-848"
    assert variant.ancestors == []  # `ancestors` stays the page tree, nothing else


def test_a_page_without_a_number_is_a_page_keyed_by_id():
    doc = one(page("42", "KIP Discussion Recordings"))

    assert (doc.key, doc.kind) == ("confluence:42", "Page")
    assert "kip-variant" not in doc.labels


def test_the_space_form_is_reported_but_not_accepted_as_a_key():
    bundle = map_pages([page("7", "KIP 230: Name Windowing Joins")])

    assert bundle.documents[0].key == "confluence:7"
    assert bundle.stats["kip_key"]["pages_space_form"] == [
        {"id": "7", "title": "KIP 230: Name Windowing Joins", "would_be_key": "KIP-230"}
    ]


def test_ancestors_are_deduplicated_document_keys_not_raw_page_ids():
    bundle = map_pages(
        [
            page("1", "KIP-1: parent"),
            page("2", "KIP-2: child", ancestors=[{"id": "1"}, {"id": "999"}, {"id": "1"}]),
        ]
    )

    assert bundle.documents[1].ancestors == ["KIP-1", "confluence:999"]
    assert bundle.documents[1].kip_of is None


# --------------------------------------------------------------------------- bodies, refs


def test_an_empty_body_is_a_warning_not_a_failure():
    bundle = map_pages([page("929", "KIP-929 : Observer Replicas", body="")])

    assert bundle.documents[0].body_md == ""
    assert bundle.warnings == [
        {
            "source": "confluence",
            "code": "empty_body",
            "id": "929",
            "key": "KIP-929",
            "title": "KIP-929 : Observer Replicas",
        }
    ]
    assert bundle.stats["empty_bodies"] == 1


def test_a_kip_page_does_not_reference_its_own_number():
    doc = one(page("1", "KIP-848: Rebalance", body="<p>KIP-848 replaces KIP-429</p>"))

    assert [(r.kind, r.key) for r in doc.refs] == [("kip", "KIP-429")]


def test_the_author_and_the_last_editor_both_become_persons():
    bundle = map_pages([page("1", "KIP-1: a")])

    assert set(bundle.persons) == {"confluence:author-key", "confluence:editor"}
    assert bundle.documents[0].author == "author-key"
    assert bundle.containers["confluence:space:KAFKA"].kind == "space"
