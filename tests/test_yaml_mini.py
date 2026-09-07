"""The YAML subset loader: what it parses, and what it refuses rather than guesses.

The second half matters more than the first. A hand-rolled parser that *succeeds* on a
construct it does not implement returns something subtly different from what the author
wrote, and the first symptom is a harvest against the wrong query. Every construct the
loader does not implement has a test here that it raises with a line number.
"""

from __future__ import annotations

import pytest

from brain.common.yaml_mini import YamlError, parse_scalar, safe_load


def test_block_mapping_and_scalar_types():
    data = safe_load(
        """
        name: jira
        enabled: true
        disabled: false
        limit: 25
        ratio: 1.5
        nothing: null
        tilde: ~
        empty:
        """
    )
    assert data == {
        "name": "jira",
        "enabled": True,
        "disabled": False,
        "limit": 25,
        "ratio": 1.5,
        "nothing": None,
        "tilde": None,
        "empty": None,
    }


def test_nested_mappings_and_sequences_of_mappings():
    data = safe_load(
        """
        sources:
          - name: jira
            options:
              page_size: 500
              expand: changelog
          - name: git
            options:
              batch: 1000
        """
    )
    assert data == {
        "sources": [
            {"name": "jira", "options": {"page_size": 500, "expand": "changelog"}},
            {"name": "git", "options": {"batch": 1000}},
        ]
    }


def test_flow_sequence_and_flow_mapping():
    data = safe_load("keys: [KAFKA, ADO]\nprefixes: {XT: xray, ADO: ado}\nempty: []")
    assert data == {"keys": ["KAFKA", "ADO"], "prefixes": {"XT": "xray", "ADO": "ado"}, "empty": []}


def test_a_quoted_scalar_keeps_colons_backslashes_and_hashes():
    """A JQL string and a regex both need this, and neither may be re-interpreted."""
    data = safe_load(
        """
        query: 'project = KAFKA AND component in (streams, "connect") AND x >= "2023-01-01"'
        pattern: 'KIP-(\\d+)'
        note: "a # inside quotes is not a comment"
        """
    )
    assert data["query"].endswith('x >= "2023-01-01"')
    assert data["pattern"] == r"KIP-(\d+)"
    assert data["note"] == "a # inside quotes is not a comment"


def test_comments_are_dropped_but_a_url_fragment_is_not():
    data = safe_load(
        """
        # a whole-line comment
        base_url: https://cwiki.apache.org/confluence#frag
        project: KAFKA # the real one
        """
    )
    assert data == {
        "base_url": "https://cwiki.apache.org/confluence#frag",
        "project": "KAFKA",
    }


def test_a_plain_scalar_may_contain_a_colon():
    assert safe_load("query: space=KAFKA and title ~ KIP-") == {
        "query": "space=KAFKA and title ~ KIP-"
    }


def test_single_quote_escaping():
    assert parse_scalar("'it''s fine'") == "it's fine"


def test_double_quote_escapes():
    assert parse_scalar('"a\\tb\\nc"') == "a\tb\nc"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("a: &anchor 1", "anchors"),
        ("a: *ref", "anchors"),
        ("a: !!str 1", "tags"),
        ("a: |\n  text", "block scalars"),
        ("---\na: 1", "multi-document"),
        ("a: 1\n...", "multi-document"),
        ("base: {a: 1}\nmerged:\n  <<: *base", "merge keys"),
        ("? complex\n: value", "explicit keys"),
        ("a:\n\tb: 1", "tab"),
        ("a: [x, [y]]", "nested flow collections"),
        ("a: [x, y", "not closed"),
        ("a: 'unterminated", "unterminated"),
        ("a: 1\na: 2", "duplicate key"),
        ("just a line", "expected `key: value`"),
    ],
)
def test_unsupported_or_malformed_constructs_raise_with_a_line_number(source, message):
    with pytest.raises(YamlError) as exc:
        safe_load(source)
    assert message in str(exc.value)
    assert "line " in str(exc.value)


def test_an_empty_document_is_none():
    assert safe_load("# only a comment\n") is None
