"""Tokens, from the environment and nowhere else (ADR-0005 §4).

`sources.yaml` names an environment variable (`auth_env`); this module turns whatever is
in it into the header — or the clone URL — the source expects. Unset means **anonymous**,
which is the POC's normal mode against the public ASF endpoints: an org deployment sets
the variable and changes nothing else.

Two rules the code enforces rather than remembers:

1. **A token never reaches a log, a report or a checkpoint.** The one place it could is
   git, where the credential lives inside the clone URL and `git` echoes that URL back in
   its own error messages. :func:`redact` is applied to every string the git connector
   emits, and :func:`Credentials.secrets` is what it redacts.
2. **A configured `auth_env` that is set but empty is an error, not anonymity.** Falling
   back to anonymous there would turn a mistyped export into a harvest that silently
   fetches only the public subset of a private Jira.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from brain.harvest.registry import SourceConfig

REDACTED = "***"

#: The username git-over-HTTPS wants when the credential is a token rather than a
#: password. GitHub documents `x-access-token`; GitLab and Bitbucket accept it too.
TOKEN_USER = "x-access-token"


class AuthError(ValueError):
    """`auth_env` is configured but what the environment holds cannot be used."""


@dataclass(frozen=True)
class Credentials:
    """What `os.environ[auth_env]` yielded, in the shape the source's scheme needs."""

    scheme: str = "none"
    token: str | None = None
    user: str | None = None
    env: str | None = None

    @property
    def present(self) -> bool:
        return bool(self.token)

    @property
    def secrets(self) -> tuple[str, ...]:
        """Every substring that must never appear in output."""
        return (self.token,) if self.token else ()

    def headers(self) -> dict[str, str]:
        """The `Authorization` header, or `{}` for anonymous / URL-carried credentials."""
        if not self.present or self.scheme in {"none", "url_token"}:
            return {}
        if self.scheme == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if self.scheme == "basic":
            # An empty user is the Azure DevOps PAT form (`:<pat>`), and Atlassian Cloud
            # wants `<email>:<api token>` — both are the same encoding.
            raw = f"{self.user or ''}:{self.token}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        raise AuthError(f"unknown auth scheme {self.scheme!r}")

    def clone_url(self, url: str) -> str:
        """`https://host/org/repo` → `https://x-access-token:<token>@host/org/repo`.

        `git clone` takes no Authorization header, so the credential has to ride in the
        URL. A URL that already carries credentials is left exactly as it is — the
        operator meant it — and a non-http remote (ssh, file) is never rewritten.
        """
        if not self.present or self.scheme != "url_token":
            return url
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or "@" in parts.netloc:
            return url
        user = self.user or TOKEN_USER
        return urlunsplit(parts._replace(netloc=f"{user}:{self.token}@{parts.netloc}"))

    def redact(self, text: str) -> str:
        return redact(text, *self.secrets)

    def describe(self) -> str:
        """One line for the report: the scheme and the variable, never the value."""
        if self.scheme == "none" or not self.env:
            return "anonymous"
        return (
            f"{self.scheme} from ${self.env}" if self.present else f"anonymous (${self.env} unset)"
        )


def redact(text: str, *secrets: str) -> str:
    """Replace every secret with `***`. Cheap, and the only thing standing between a
    token and `data/reports/harvest.json`."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, REDACTED)
    return out


def credentials_for(source: SourceConfig, environ: Mapping[str, str] | None = None) -> Credentials:
    """Read `source.auth_env` out of the environment. Unset → anonymous."""
    env = environ if environ is not None else os.environ
    if source.auth_scheme == "none" or not source.auth_env:
        return Credentials(env=source.auth_env)
    raw = env.get(source.auth_env)
    if raw is None:
        return Credentials(scheme=source.auth_scheme, env=source.auth_env)
    token = raw.strip()
    if not token:
        raise AuthError(
            f"${source.auth_env} is set but empty. Unset it to harvest {source.name!r} "
            "anonymously; an empty token is almost always a broken export, and treating "
            "it as anonymous would silently fetch only the public subset."
        )
    user = env.get(source.auth_user_env) if source.auth_user_env else None
    return Credentials(
        scheme=source.auth_scheme,
        token=token,
        user=(user.strip() if user else None) or None,
        env=source.auth_env,
    )
