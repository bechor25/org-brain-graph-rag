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


def test_the_basic_scheme_redacts_the_encoded_credential_too():
    """`base64(user:token)` shares no substring with the token; redacting one is not the
    other, and the encoded form is what a library echoing the header would print."""
    creds = credentials_for(
        replace(source("jira"), auth_scheme="basic"),
        {"JIRA_TOKEN": TOKEN, "JIRA_USER": "a@b.test"},
    )
    blob = base64.b64encode(f"a@b.test:{TOKEN}".encode()).decode()
    assert blob not in TOKEN and TOKEN not in blob
    assert creds.secrets == (TOKEN, blob)
    assert creds.redact(f"Authorization: Basic {blob}") == "Authorization: Basic ***"
    # the header still carries the real thing
    assert creds.headers() == {"Authorization": f"Basic {blob}"}


def test_a_bearer_or_url_token_needs_no_second_secret():
    """The token is verbatim in both, so one entry covers them and two would be noise."""
    assert credentials_for(source("jira"), {"JIRA_TOKEN": TOKEN}).secrets == (TOKEN,)
    assert credentials_for(source("git"), {"GIT_TOKEN": TOKEN}).secrets == (TOKEN,)


def test_a_clone_timeout_does_not_print_the_token(tmp_path, monkeypatch):
    """`TimeoutExpired.__str__` prints the whole command — including the remote URL."""
    import subprocess

    monkeypatch.setenv("GIT_TOKEN", TOKEN)

    def runner(args, cwd=None, timeout=900.0, secrets=()):
        from brain.harvest.git import _run

        def boom(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd=list(args), timeout=timeout)

        monkeypatch.setattr(subprocess, "run", boom)
        return _run(args, cwd, timeout, secrets)

    connector = GitConnector(tmp_path, source=source("git"), runner=runner)
    result = connector.run()
    details = " ".join(e["detail"] for e in result.errors)
    assert TOKEN not in details
    assert "timed out" in details and "***" in details


def test_the_report_boundary_scrubs_whatever_slipped_through(tmp_path, monkeypatch):
    """Belt and braces: an error appended by something that never heard of redaction."""
    monkeypatch.setenv("GIT_TOKEN", TOKEN)
    connector = GitConnector(tmp_path, source=source("git"), runner=lambda *a, **k: "")
    assert connector.secrets == (TOKEN,)
    connector.errors.append(
        {"when": "now", "kind": "clone", "detail": f"remote https://{TOKEN}@x/y", "fatal": False}
    )
    scrubbed = connector.collected_errors()
    assert TOKEN not in scrubbed[0]["detail"]
    assert "***" in scrubbed[0]["detail"]


def test_an_arbitrary_exception_from_fetch_is_redacted_too(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_TOKEN", TOKEN)

    def runner(*_a, **_kw):
        raise RuntimeError(f"library said: https://{TOKEN}@github.com/x")

    result = GitConnector(tmp_path, source=source("git"), runner=runner).run()
    details = " ".join(e["detail"] for e in result.errors)
    assert TOKEN not in details and "***" in details


# --------------------------------------------------------------------------- the report


def test_no_exception_from_subprocess_leaves_git_unredacted(tmp_path, monkeypatch):
    """`TimeoutExpired` is the one that was found; the guarantee is about all of them."""
    import subprocess

    monkeypatch.setenv("GIT_TOKEN", TOKEN)

    def runner(args, cwd=None, timeout=900.0, secrets=()):
        from brain.harvest.git import _run

        def boom(*_a, **_kw):
            raise OSError(f"cannot exec: {' '.join(args)}")

        monkeypatch.setattr(subprocess, "run", boom)
        return _run(args, cwd, timeout, secrets)

    result = GitConnector(tmp_path, source=source("git"), runner=runner).run()
    details = " ".join(e["detail"] for e in result.errors)
    assert TOKEN not in details and "***" in details


def test_the_clone_does_not_leave_the_token_in_git_config(tmp_path, monkeypatch):
    """`git clone <url-with-token>` writes that URL into `.git/config` and keeps it."""
    monkeypatch.setenv("GIT_TOKEN", TOKEN)
    calls: list[list[str]] = []

    def runner(args, cwd=None, timeout=900.0, secrets=()):
        calls.append(list(args))
        if args[:2] == ["git", "clone"]:
            (tmp_path / "git" / "kafka" / ".git").mkdir(parents=True)
        return ""

    connector = GitConnector(tmp_path, source=source("git"), runner=runner)
    assert connector.ensure_clone() is True

    assert any(TOKEN in arg for arg in calls[0]), "the clone must still authenticate"
    assert calls[1] == ["git", "remote", "set-url", "origin", connector.clone_url]
    assert all(TOKEN not in arg for arg in calls[1])


def test_a_leaking_connector_still_writes_a_clean_harvest_report(tmp_path, monkeypatch):
    """End to end: the token is in the exception text, and not in `harvest.json` on disk.

    The connector here is deliberately hostile — it raises a bare `RuntimeError` quoting
    its own credential, the way a third-party library would. Nothing between it and the
    file knows about that string except the redaction at the report boundary.
    """
    import json

    from brain.harvest.runner import run_harvest

    monkeypatch.setenv("GIT_TOKEN", TOKEN)
    raw_dir = tmp_path / "raw"
    reports = tmp_path / "reports"
    reports.mkdir()

    def runner(*_a, **_kw):
        raise RuntimeError(f"remote https://x-access-token:{TOKEN}@github.com/apache/kafka")

    printed: list[str] = []
    report, code = run_harvest(
        ["git"],
        raw_dir=raw_dir,
        reports_dir=reports,
        factories={"git": lambda d: GitConnector(d, source=source("git"), runner=runner)},
        echo=printed.append,
    )

    written = (reports / "harvest.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert "***" in written
    assert TOKEN not in json.dumps(report)
    assert TOKEN not in "\n".join(printed)
    assert code == 1, "a fatal error must still be a failure, not a quietly clean report"


def test_a_failing_stats_pass_cannot_leak_either(tmp_path, monkeypatch):
    """`stats()` runs after the fetch and its failure is recorded, never raised."""
    monkeypatch.setenv("GIT_TOKEN", TOKEN)

    class Leaky(GitConnector):
        def stats(self, since=None):
            raise RuntimeError(f"reading {self.auth_clone_url}")

    result = Leaky(tmp_path, source=source("git"), runner=lambda *a, **k: "").run()
    stats_errors = [e for e in result.errors if e["kind"] == "stats"]
    assert stats_errors, "the stats failure must be recorded, not swallowed"
    assert TOKEN not in stats_errors[0]["detail"]
    assert "***" in stats_errors[0]["detail"]
