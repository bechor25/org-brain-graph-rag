"""`sources.yaml` — the one place a source's URL, query, keys and auth variable live.

ADR-0005: the module boundary is the canonical model, and everything *above* it that is
organisation-specific is configuration. A new org system is a connector, a mapper, a
golden test and one entry here — never a new constant in `brain/harvest/*.py`.

Three consumers, and each one is a place where a hardcoded value used to sit:

* `brain harvest` resolves `--source <name>` to an entry and builds the connector from it
  (`base_url`, `query`, the per-connector `options`).
* `brain canon` reads the issue-key allowlist — the union of every enabled source's
  `project_keys` plus the synthetic prefixes — and the document-title pattern that turns a
  Confluence title into `Document.key`.
* the connectors read `auth_env` and take the token from the environment, never from here.

Loud validation is the point. An unknown key in `sources.yaml` is an error with the file
path and the key name, not a silently ignored line: a misspelled `project_key` that is
quietly dropped shows up much later as refs that vanished from the graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from brain.common.yaml_mini import YamlError, safe_load

DEFAULT_FILENAME = "sources.yaml"

#: Source types the registry accepts. `ado`/`xray` are the templates in
#: `docs/guides/adding-a-connector.md` — a registry entry may name them, and
#: `brain harvest` says what is missing if one is enabled without a connector.
SOURCE_TYPES: frozenset[str] = frozenset({"jira", "confluence", "git", "ado", "xray"})

#: How a token from `os.environ[auth_env]` is presented. `url_token` is git's: the token
#: goes into the clone URL, because `git clone` takes no Authorization header.
AUTH_SCHEMES: frozenset[str] = frozenset({"bearer", "basic", "url_token", "none"})

_TOP_KEYS: frozenset[str] = frozenset({"version", "sources", "canon", "synthetic"})
_SOURCE_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "type",
        "enabled",
        "base_url",
        "query",
        "project_keys",
        "auth_env",
        "auth_scheme",
        "auth_user_env",
        "options",
        "document",
    }
)
_CANON_KEYS: frozenset[str] = frozenset({"issue_key_blacklist"})
_SYNTHETIC_KEYS: frozenset[str] = frozenset({"key_prefixes"})
_DOCUMENT_KEYS: frozenset[str] = frozenset(
    {"kind", "title_pattern", "title_pattern_loose", "key_format"}
)

SUPPORTED_VERSIONS: frozenset[int] = frozenset({1})


class RegistryError(ValueError):
    """`sources.yaml` is missing, malformed, or names something that does not exist."""


def _unknown(path: Path, where: str, got: Any, allowed: frozenset[str]) -> None:
    if not isinstance(got, dict):
        raise RegistryError(f"{path}: {where} must be a mapping, got {type(got).__name__}")
    extra = sorted(set(got) - allowed)
    if extra:
        raise RegistryError(
            f"{path}: {where} has unknown key(s) {', '.join(extra)}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )


def _str_list(path: Path, where: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RegistryError(f"{path}: {where} must be a list, got {type(value).__name__}")
    return tuple(str(v) for v in value)


# --------------------------------------------------------------------------- document key


@dataclass(frozen=True)
class DocumentKeySpec:
    """How a document title becomes `Document.key` (`"KIP-848: …"` → `KIP-848`).

    `loose` is the tolerated spelling the corpus also contains ("KIP 156 Add option …").
    It is recognised and counted but never used as a key: accepting it recovers 13 pages
    and creates more collisions than it resolves (lesson 01).
    """

    kind: str = "KIP"
    pattern: re.Pattern[str] = field(default_factory=lambda: re.compile(r"KIP-(\d+)", re.I))
    loose: re.Pattern[str] | None = field(default_factory=lambda: re.compile(r"KIP\s+(\d+)", re.I))
    key_format: str = "KIP-{number}"

    def key(self, title: str) -> str | None:
        m = self.pattern.search(title or "")
        return self.format(m.group(1)) if m else None

    def loose_key(self, title: str) -> str | None:
        if self.loose is None:
            return None
        m = self.loose.search(title or "")
        return self.format(m.group(1)) if m else None

    def format(self, number: str) -> str:
        """`"0848"` → `KIP-848`: the number is normalized so `KIP-0848` cannot be a second key."""
        try:
            value: object = int(number)
        except ValueError:
            value = number
        return self.key_format.format(number=value)

    @classmethod
    def parse(cls, path: Path, name: str, raw: Any) -> DocumentKeySpec:
        if raw is None:
            return cls()
        _unknown(path, f"sources[{name}].document", raw, _DOCUMENT_KEYS)
        pattern = str(raw.get("title_pattern") or r"KIP-(\d+)")
        loose_raw = raw.get("title_pattern_loose")
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            loose = re.compile(str(loose_raw), re.IGNORECASE) if loose_raw else None
        except re.error as exc:
            raise RegistryError(
                f"{path}: sources[{name}].document pattern is invalid: {exc}"
            ) from exc
        if compiled.groups != 1 or (loose is not None and loose.groups != 1):
            raise RegistryError(
                f"{path}: sources[{name}].document patterns need exactly one capture group "
                "— the number that becomes the key"
            )
        key_format = str(raw.get("key_format") or "KIP-{number}")
        if "{number}" not in key_format:
            raise RegistryError(
                f"{path}: sources[{name}].document.key_format must contain {{number}}"
            )
        return cls(
            kind=str(raw.get("kind") or "KIP"),
            pattern=compiled,
            loose=loose,
            key_format=key_format,
        )


# --------------------------------------------------------------------------- one source


@dataclass(frozen=True)
class SourceConfig:
    """One entry of `sources.yaml`, validated."""

    name: str
    type: str
    base_url: str
    query: str
    project_keys: tuple[str, ...] = ()
    auth_env: str | None = None
    auth_scheme: str = "none"
    auth_user_env: str | None = None
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)
    document: DocumentKeySpec = field(default_factory=DocumentKeySpec)

    def option(self, key: str, default: Any = None) -> Any:
        value = self.options.get(key, default)
        return default if value is None else value

    def int_option(self, key: str, default: int) -> int:
        value = self.option(key, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise RegistryError(
                f"sources[{self.name}].options.{key} must be an integer, got {value!r}"
            ) from exc

    @classmethod
    def parse(cls, path: Path, index: int, raw: Any) -> SourceConfig:
        where = f"sources[{index}]"
        if not isinstance(raw, dict):
            raise RegistryError(f"{path}: {where} must be a mapping, got {type(raw).__name__}")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise RegistryError(f"{path}: {where} has no `name`")
        _unknown(path, f"sources[{name}]", raw, _SOURCE_KEYS)

        type_ = str(raw.get("type") or "").strip()
        if type_ not in SOURCE_TYPES:
            raise RegistryError(
                f"{path}: sources[{name}].type is {type_!r}; expected one of "
                f"{', '.join(sorted(SOURCE_TYPES))}"
            )
        base_url = str(raw.get("base_url") or "").strip()
        if not base_url:
            raise RegistryError(f"{path}: sources[{name}] has no `base_url`")
        query = raw.get("query")
        if query is None:
            raise RegistryError(f"{path}: sources[{name}] has no `query`")

        scheme = str(raw.get("auth_scheme") or "none").strip().lower()
        if scheme not in AUTH_SCHEMES:
            raise RegistryError(
                f"{path}: sources[{name}].auth_scheme is {scheme!r}; expected one of "
                f"{', '.join(sorted(AUTH_SCHEMES))}"
            )
        options = raw.get("options") or {}
        if not isinstance(options, dict):
            raise RegistryError(f"{path}: sources[{name}].options must be a mapping")

        # A token that looks like a secret rather than a variable name is the one mistake
        # this file must never carry to disk — it would be committed.
        auth_env = raw.get("auth_env")
        if auth_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(auth_env)):
            raise RegistryError(
                f"{path}: sources[{name}].auth_env must be an environment VARIABLE NAME "
                f"(got {auth_env!r}). Secrets never live in sources.yaml."
            )

        return cls(
            name=name,
            type=type_,
            base_url=base_url,
            query=str(query),
            project_keys=_str_list(path, f"sources[{name}].project_keys", raw.get("project_keys")),
            auth_env=str(auth_env) if auth_env else None,
            auth_scheme=scheme,
            auth_user_env=str(raw["auth_user_env"]) if raw.get("auth_user_env") else None,
            enabled=bool(raw.get("enabled", True)),
            options=dict(options),
            document=DocumentKeySpec.parse(path, name, raw.get("document")),
        )


# --------------------------------------------------------------------------- the registry


@dataclass(frozen=True)
class Registry:
    """Every source, plus the two corpus-wide policies canon reads."""

    path: Path
    version: int
    sources: tuple[SourceConfig, ...]
    issue_key_blacklist: frozenset[str]
    #: synthetic key prefix -> the canonical `source` it belongs to (`XT` -> `xray`).
    synthetic_prefixes: dict[str, str]

    # -- lookup ------------------------------------------------------------

    def source(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name == name:
                return s
        raise RegistryError(
            f"unknown source {name!r}; {self.path} defines "
            f"{', '.join(s.name for s in self.sources) or '(none)'}"
        )

    def enabled(self) -> tuple[SourceConfig, ...]:
        return tuple(s for s in self.sources if s.enabled)

    def names(self, *, only_enabled: bool = True) -> tuple[str, ...]:
        return tuple(s.name for s in (self.enabled() if only_enabled else self.sources))

    def resolve(self, selector: str) -> list[str]:
        """`"all"` → every enabled source, in file order; otherwise one name, enabled or not.

        Naming a disabled source explicitly is allowed: that is how a new connector is
        tried once before it joins `--source all`.
        """
        if selector == "all":
            names = list(self.names())
            if not names:
                raise RegistryError(f"{self.path} has no enabled source")
            return names
        return [self.source(selector).name]

    # -- canon policies ----------------------------------------------------

    def issue_project_allowlist(self) -> frozenset[str]:
        """Project keys an `ABC-123` text match may become an issue ref for.

        The union of every *enabled* source's `project_keys` and the synthetic prefixes.
        The regex cannot tell `KAFKA-15123` from `UTF-8` or `SHA-256`; only a key some
        configured source actually owns can.
        """
        keys = {k.upper() for s in self.enabled() for k in s.project_keys}
        return frozenset(keys | {p.upper() for p in self.synthetic_prefixes})

    def document_spec(self, name: str) -> DocumentKeySpec:
        return self.source(name).document

    # -- parsing -----------------------------------------------------------

    @classmethod
    def parse(cls, path: Path, raw: Any) -> Registry:
        if raw is None:
            raise RegistryError(f"{path} is empty")
        _unknown(path, "the top level", raw, _TOP_KEYS)
        version = raw.get("version", 1)
        if version not in SUPPORTED_VERSIONS:
            raise RegistryError(
                f"{path}: version {version!r} is not supported "
                f"(this build reads {sorted(SUPPORTED_VERSIONS)})"
            )
        entries = raw.get("sources")
        if not isinstance(entries, list) or not entries:
            raise RegistryError(f"{path}: `sources` must be a non-empty list")
        sources = tuple(SourceConfig.parse(path, i, e) for i, e in enumerate(entries))
        seen: set[str] = set()
        for s in sources:
            if s.name in seen:
                raise RegistryError(f"{path}: duplicate source name {s.name!r}")
            seen.add(s.name)

        canon = raw.get("canon") or {}
        _unknown(path, "canon", canon, _CANON_KEYS)
        blacklist = frozenset(
            k.upper()
            for k in _str_list(path, "canon.issue_key_blacklist", canon.get("issue_key_blacklist"))
        )

        synthetic = raw.get("synthetic") or {}
        _unknown(path, "synthetic", synthetic, _SYNTHETIC_KEYS)
        prefixes_raw = synthetic.get("key_prefixes") or {}
        if not isinstance(prefixes_raw, dict):
            raise RegistryError(
                f"{path}: synthetic.key_prefixes must be a mapping prefix -> source"
            )
        prefixes = {str(k).upper(): str(v) for k, v in prefixes_raw.items()}
        for prefix in prefixes:
            if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", prefix):
                raise RegistryError(
                    f"{path}: synthetic.key_prefixes[{prefix!r}] is not a key prefix "
                    "(1-10 upper-case letters/digits)"
                )
        return cls(
            path=path,
            version=int(version),
            sources=sources,
            issue_key_blacklist=blacklist,
            synthetic_prefixes=prefixes,
        )


# --------------------------------------------------------------------------- loading


#: Where `sources.yaml` lives when `$SOURCES_FILE` does not say otherwise.
REPO_ROOT = Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    """`$SOURCES_FILE` (or `.env`), else `sources.yaml` in the repo root.

    A relative setting is tried against the working directory first and the repo root
    second, so `uv run brain harvest` works from a subdirectory without configuration.
    """
    from brain.config import get_settings

    configured = Path(get_settings().sources_file)
    if configured.is_absolute() or configured.exists():
        return configured
    return REPO_ROOT / configured


def load_registry(path: Path | str | None = None) -> Registry:
    """Read and validate a registry file. Never cached — `get_registry` is the cached one."""
    target = Path(path) if path is not None else registry_path()
    if not target.is_file():
        raise RegistryError(
            f"{target} not found. Every source lives in {DEFAULT_FILENAME}; copy the one "
            "in the repo root or point $SOURCES_FILE at yours "
            "(docs/guides/adding-a-connector.md)."
        )
    try:
        raw = safe_load(target.read_text(encoding="utf-8"))
    except YamlError as exc:
        raise RegistryError(f"{target}: {exc}") from exc
    except OSError as exc:
        raise RegistryError(f"{target} could not be read: {exc}") from exc
    return Registry.parse(target, raw)


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    """The process-wide registry. Cleared by :func:`reset_caches` (tests, and `--sources`)."""
    return load_registry()


def reset_caches() -> None:
    """Forget the cached registry and everything derived from it.

    Called by the test suite's autouse fixture and whenever a caller points the pipeline
    at a different `sources.yaml`; a stale allowlist is a silently different corpus.
    """
    get_registry.cache_clear()
    from brain.canon import mentions

    mentions.default_policy.cache_clear()
