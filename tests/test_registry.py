"""`sources.yaml` — resolution, validation, and the two things it must not change.

The two are the point of the whole task:

1. **The Kafka entries reproduce today's behaviour exactly.** A connector's query
   *signature* is what a `checkpoint.json` on disk is stamped with, so a registry that
   builds a different JQL — even one that means the same thing — invalidates 130 MB of
   raw data and silently re-fetches it. The signatures below are the ones recorded in
   `data/raw/<source>/checkpoint.json` by the cold pull of 2026-09-03 (lesson 01).
2. **The canon allowlist is the registry's.** `KAFKA` comes from `project_keys`, the
   synthetic prefixes from the `synthetic` block, and nothing from a Python constant.
"""

from __future__ import annotations

from datetime import date

import pytest

from brain.canon.mentions import RefPolicy, default_policy, extract_refs, filter_refs
from brain.harvest.confluence import ConfluenceConnector, build_cql
from brain.harvest.git import GitConnector, parse_window
from brain.harvest.jira import JiraConnector, build_jql
from brain.harvest.registry import (
    DocumentKeySpec,
    Registry,
    RegistryError,
    get_registry,
    load_registry,
    registry_path,
)
from brain.harvest.runner import build_connector, resolve_sources

#: Signatures the 2026-09-03 cold pull wrote into `data/raw/<source>/checkpoint.json`.
#: A change here means every existing checkpoint is stale and the corpus is re-fetched.
RECORDED_SIGNATURES = {
    "jira": "5da028abe2be",
    "confluence": "162b225214ef",
    "git": "c7899d52babc",
}


@pytest.fixture
def registry() -> Registry:
    return get_registry()


def write(tmp_path, body: str):
    path = tmp_path / "sources.yaml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL = """
version: 1
sources:
  - name: jira
    type: jira
    base_url: https://example.test/jira
    query: 'project = X'
    project_keys: [X]
"""


# --------------------------------------------------------------------------- resolution


def test_the_repo_registry_loads_and_names_the_three_kafka_sources(registry):
    assert registry_path().name == "sources.yaml"
    assert registry.names() == ("jira", "confluence", "git")
    assert [s.type for s in registry.enabled()] == ["jira", "confluence", "git"]


def test_resolve_all_is_every_enabled_source_and_a_name_is_itself(registry):
    assert registry.resolve("all") == ["jira", "confluence", "git"]
    assert registry.resolve("git") == ["git"]


def test_a_disabled_source_is_out_of_all_but_can_still_be_named(registry):
    """That is how a new connector is tried once before it joins the nightly run."""
    assert "ado" not in registry.resolve("all")
    assert registry.resolve("ado") == ["ado"]
    assert registry.source("ado").enabled is False


def test_an_unknown_name_says_what_the_file_does_define(registry):
    with pytest.raises(RegistryError, match="unknown source 'nope'.*jira, confluence, git"):
        registry.resolve("nope")


def test_a_registered_type_without_a_connector_names_the_gap(registry, tmp_path):
    with pytest.raises(RegistryError, match="no connector yet"):
        build_connector(registry.source("ado"), tmp_path)


def test_the_runner_resolves_through_the_registry():
    assert resolve_sources("all") == ["jira", "confluence", "git"]


# --------------------------------------------------------------------------- behaviour is unchanged


@pytest.mark.parametrize("name", ["jira", "confluence", "git"])
def test_registry_driven_connectors_keep_the_recorded_query_signature(name, tmp_path, registry):
    """The acceptance criterion, as a unit test: same signature, same checkpoint, no refetch."""
    connector = build_connector(registry.source(name), tmp_path)
    assert connector.signature(None) == RECORDED_SIGNATURES[name]


def test_the_jql_still_quotes_the_reserved_word_connect_and_ends_with_order_by(registry):
    jql = build_jql(registry.source("jira"))
    assert jql == (
        'project = KAFKA AND component in (streams, "connect", clients) '
        'AND created >= "2023-01-01" AND created <= "2025-12-31" ORDER BY created ASC'
    )
    assert "component in (streams, connect, clients)" not in jql


def test_since_extends_the_clause_list_and_order_by_stays_last(registry):
    jql = build_jql(registry.source("jira"), date(2025, 6, 1))
    assert 'AND updated >= "2025-06-01" ORDER BY created ASC' in jql
    assert jql.index("updated >=") < jql.index("ORDER BY")


def test_the_cql_and_the_git_window_come_from_the_registry(registry):
    assert (
        build_cql(registry.source("confluence")) == 'space=KAFKA and type=page and title ~ "KIP-"'
    )
    assert parse_window(registry.source("git").query) == ("2023-01-01", "2025-12-31")


def test_a_git_query_without_an_end_means_to_head():
    assert parse_window("2023-01-01") == ("2023-01-01", None)
    assert parse_window("2023-01-01..") == ("2023-01-01", None)
    with pytest.raises(RegistryError, match="<since>..<until>"):
        parse_window("")


def test_a_connector_writes_under_its_registry_name_not_its_type(tmp_path, registry):
    """Two Jira instances are two directories; the name is the identity."""
    from dataclasses import replace

    config = replace(registry.source("jira"), name="jira-eu")
    connector = JiraConnector(tmp_path, source=config)
    assert connector.name == "jira-eu"
    assert connector.source_dir() == tmp_path / "jira-eu"


# --------------------------------------------------------------------------- canon policy


def test_the_issue_allowlist_is_project_keys_plus_the_synthetic_prefixes(registry):
    assert registry.issue_project_allowlist() == {"KAFKA", "XT", "XE", "XP", "XS", "ADO"}
    assert default_policy().allowlist == registry.issue_project_allowlist()
    assert default_policy().blacklist == {"KAFKA-1"}


def test_canon_filters_on_the_registry_allowlist_and_blacklist():
    refs = extract_refs("KAFKA-15123 and UTF-8 and XT-9 and KAFKA-1")
    kept, removed = filter_refs(refs)
    assert [r.key for r in kept] == ["KAFKA-15123", "XT-9"]
    assert dict(removed) == {"UTF-8": "not_in_allowlist", "KAFKA-1": "blacklisted"}


def test_a_different_registry_is_a_different_allowlist(tmp_path):
    """The whole point of the move: another org's keys are a config change."""
    path = write(
        tmp_path,
        """
        version: 1
        canon:
          issue_key_blacklist: [ACME-1]
        sources:
          - name: tracker
            type: jira
            base_url: https://acme.test/jira
            query: 'project = ACME'
            project_keys: [ACME, PLAT]
        """,
    )
    policy = RefPolicy.from_registry(load_registry(path))
    assert policy.allowlist == {"ACME", "PLAT"}
    kept, removed = filter_refs(extract_refs("ACME-7 KAFKA-15123 ACME-1"), **policy.__dict__)
    assert [r.key for r in kept] == ["ACME-7"]
    assert dict(removed) == {"KAFKA-15123": "not_in_allowlist", "ACME-1": "blacklisted"}


def test_the_document_title_pattern_comes_from_the_registry(registry):
    spec = registry.document_spec("confluence")
    assert spec.key("KIP-848: The Next Generation") == "KIP-848"
    assert spec.key("Copy of KIP-0848") == "KIP-848"  # the number is normalized
    assert spec.key("KIP Release Notes") is None
    assert spec.loose_key("KIP 156 Add option dry run") == "KIP-156"


def test_a_design_doc_pattern_can_be_reconfigured_without_touching_code(tmp_path):
    path = write(
        tmp_path,
        """
        version: 1
        sources:
          - name: wiki
            type: confluence
            base_url: https://acme.test/wiki
            query: 'space=ENG'
            document:
              kind: KIP
              title_pattern: 'RFC-(\\d+)'
              key_format: 'RFC-{number}'
        """,
    )
    spec = load_registry(path).document_spec("wiki")
    assert spec.key("RFC-12: retention") == "RFC-12"
    assert spec.key("KIP-848") is None
    assert spec.loose_key("RFC 12") is None  # no loose pattern configured


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 1\nsources: []", "non-empty list"),
        ("version: 9\n" + MINIMAL.split("version: 1\n", 1)[1], "version 9"),
        (MINIMAL + "  - name: jira\n    type: git\n    base_url: x\n    query: y", "duplicate"),
        (MINIMAL.replace("type: jira", "type: gitlab"), "expected one of"),
        (MINIMAL.replace("    base_url: https://example.test/jira\n", ""), "no `base_url`"),
        (MINIMAL.replace("    query: 'project = X'\n", ""), "no `query`"),
        (MINIMAL + "    nickname: j", "unknown key(s) nickname"),
        (MINIMAL + "    auth_scheme: magic", "auth_scheme"),
        (MINIMAL + "    auth_env: 'ghp_realsecret!'", "VARIABLE NAME"),
        ("version: 1\ncredentials: {}\n" + MINIMAL.split("version: 1\n", 1)[1], "unknown key(s)"),
        (MINIMAL + "\nsynthetic:\n  key_prefixes:\n    X-T: xray", "not a key prefix"),
    ],
)
def test_a_bad_registry_is_refused_with_the_file_and_the_reason(tmp_path, body, message):
    with pytest.raises(RegistryError, match=message.replace("(", r"\(").replace(")", r"\)")):
        load_registry(write(tmp_path, body))


def test_a_missing_registry_says_where_to_get_one(tmp_path):
    with pytest.raises(RegistryError, match="not found.*sources.yaml"):
        load_registry(tmp_path / "nope.yaml")


def test_options_have_defaults_so_a_new_source_needs_five_lines(tmp_path):
    config = load_registry(write(tmp_path, MINIMAL)).source("jira")
    assert config.int_option("page_size", 500) == 500
    assert config.option("expand", "changelog") == "changelog"
    assert config.enabled is True
    assert config.auth_scheme == "none"
    assert isinstance(config.document, DocumentKeySpec)


def test_a_non_integer_option_is_an_error_not_a_crash_at_page_500(tmp_path):
    config = load_registry(write(tmp_path, MINIMAL + "    options:\n      page_size: many")).source(
        "jira"
    )
    with pytest.raises(RegistryError, match="page_size must be an integer"):
        config.int_option("page_size", 500)


def test_connectors_can_be_built_for_every_enabled_source(tmp_path, registry):
    for name in registry.names():
        connector = build_connector(registry.source(name), tmp_path)
        assert connector.name == name
        assert connector.query_text(None)
        assert isinstance(connector, JiraConnector | ConfluenceConnector | GitConnector)
