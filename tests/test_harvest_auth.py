"""Tokens from the environment: injected where they belong, printed nowhere.

Anonymous is the POC's normal mode, so the first thing these tests pin is that an unset
variable changes nothing at all. The second is the leak path: git puts the credential in
the clone URL, and git echoes the remote back in its own error messages.
"""

from __future__ import annotations

import base64
from dataclasses import replace

import httpx
import pytest
import respx

from brain.harvest.auth import AuthError, Credentials, credentials_for, redact
from brain.harvest.base import HarvestError, HttpFetcher
from brain.harvest.git import GitConnector
from brain.harvest.jira import JiraConnector
from brain.harvest.registry import get_registry
from tests.harvest_helpers import source

TOKEN = "s3cr3t-token-value"  # noqa: S105 - a fake token for the redaction tests


# --------------------------------------------------------------------------- credentials


def test_an_unset_variable_is_anonymous_and_sends_no_header():
    creds = credentials_for(source("jira"), {})
    assert creds.present is False
    assert creds.headers() == {}
    assert creds.describe() == "anonymous ($JIRA_TOKEN unset)"


def test_a_bearer_token_becomes_an_authorization_header():
    creds = credentials_for(source("jira"), {"JIRA_TOKEN": TOKEN})
    assert creds.headers() == {"Authorization": f"Bearer {TOKEN}"}
    assert creds.describe() == "bearer from $JIRA_TOKEN"


def test_basic_uses_the_user_variable_and_ado_style_empty_user():
    config = replace(source("jira"), auth_scheme="basic")
    with_user = credentials_for(config, {"JIRA_TOKEN": TOKEN, "JIRA_USER": "a@b.test"})
    expected = base64.b64encode(f"a@b.test:{TOKEN}".encode()).decode()
    assert with_user.headers() == {"Authorization": f"Basic {expected}"}

    # Azure DevOps takes a PAT as basic with an empty username.
    pat = credentials_for(get_registry().source("ado"), {"ADO_PAT": TOKEN})
    empty_user = base64.b64encode(f":{TOKEN}".encode()).decode()
    assert pat.headers() == {"Authorization": f"Basic {empty_user}"}


def test_a_set_but_empty_variable_is_an_error_not_a_silent_downgrade():
    """Anonymous against a private Jira fetches the public subset and reports success."""
    with pytest.raises(AuthError, match="set but empty"):
        credentials_for(source("jira"), {"JIRA_TOKEN": "   "})


def test_the_git_token_rides_in_the_clone_url_and_never_in_a_header():
    creds = credentials_for(source("git"), {"GIT_TOKEN": TOKEN})
    assert creds.headers() == {}
    assert creds.clone_url("https://github.com/apache/kafka") == (
        f"https://x-access-token:{TOKEN}@github.com/apache/kafka"
    )


def test_a_clone_url_that_already_has_credentials_or_is_not_http_is_left_alone():
    creds = credentials_for(source("git"), {"GIT_TOKEN": TOKEN})
    assert creds.clone_url("git@github.com:apache/kafka.git") == "git@github.com:apache/kafka.git"
    already = "https://someone:pw@github.com/apache/kafka"
    assert creds.clone_url(already) == already


def test_redact_replaces_every_occurrence():
    assert redact(f"clone {TOKEN} then {TOKEN}", TOKEN) == "clone *** then ***"
    assert redact("nothing to hide") == "nothing to hide"


# --------------------------------------------------------------------------- injection


@respx.mock
def test_the_connector_sends_the_header_on_every_request(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"total": 0, "issues": [], "startAt": 0, "maxResults": 0})

    config = source("jira")
    respx.get(f"{config.base_url}/rest/api/2/search").mock(side_effect=handler)
    JiraConnector(tmp_path, source=config).probe()
    assert seen == [f"Bearer {TOKEN}"]


@respx.mock
def test_no_header_at_all_when_the_variable_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"total": 0, "issues": []})

    config = source("jira")
    respx.get(f"{config.base_url}/rest/api/2/search").mock(side_effect=handler)
    JiraConnector(tmp_path, source=config).probe()
    assert seen == [None]


@respx.mock
def test_a_token_never_reaches_the_error_the_report_records():
    """`HttpFetcher` records failures verbatim; the token must not be in them."""
    fetcher = HttpFetcher(
        "https://example.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
        secrets=(TOKEN,),
        min_interval=0.0,
        sleep=lambda _s: None,
    )
    respx.get("https://example.test/x").mock(
        return_value=httpx.Response(403, text=f"bad credential {TOKEN}")
    )
    with pytest.raises(HarvestError) as exc:
        fetcher.get_json("/x")
    assert TOKEN not in str(exc.value)
    assert "***" in str(exc.value)
    assert all(TOKEN not in e["detail"] for e in fetcher.errors)


def test_the_git_clone_url_is_redacted_out_of_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_TOKEN", TOKEN)

    def runner(args, cwd=None, timeout=900.0, secrets=()):
        from brain.harvest.auth import redact as _redact

        raise HarvestError(_redact(f"{' '.join(args)} failed (128)", *secrets))

    connector = GitConnector(tmp_path, source=source("git"), runner=runner)
    # The public remote is what the instance carries; the credential is added at call time.
    assert connector.clone_url == "https://github.com/apache/kafka"
    assert TOKEN in connector.auth_clone_url

    result = connector.run()
    details = " ".join(e["detail"] for e in result.errors)
    assert TOKEN not in details
    assert "***" in details


def test_credentials_describe_never_contains_the_value():
    creds = Credentials(scheme="bearer", token=TOKEN, env="JIRA_TOKEN")
    assert TOKEN not in creds.describe()
    assert creds.secrets == (TOKEN,)
