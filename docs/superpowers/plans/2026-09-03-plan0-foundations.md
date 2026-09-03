# Org Brain POC — Plan 0: Foundations & Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the complete local platform for the organizational-brain Graph RAG POC (Neo4j+APOC+GDS, local bilingual embeddings, `brain` CLI with `doctor`, canonical data model, mini fixture corpus) and define all 15 Opus agents plus shared conventions and documentation templates.

**Architecture:** Python 3.11 package `brain/` (uv, typer CLI, pydantic v2) talks to Neo4j via the official driver (`RoutingControl.READ` is the server-enforced read-only guard — Community Edition has no RBAC) and to a native Ollama instance serving `bge-m3` (1024-dim, Hebrew+English). Everything else (harvest, load, extract, retrieve, eval) is a CLI subcommand stub here and gets filled in by Plans 1–3. Agents that act as the pipeline's "LLM" get only Read/Write/Glob tools so they physically cannot touch the database.

**Tech Stack:** Python 3.11, uv, typer, pydantic v2 + pydantic-settings, neo4j driver 5.x/6.x, httpx, pytest + respx, ruff, Docker Compose (`neo4j` image with APOC + GDS plugins), Ollama (`bge-m3`), Claude Code subagents (`.claude/agents/*.md`, `model: opus`).

**Spec:** `docs/superpowers/specs/2026-09-03-org-brain-graph-rag-design.md` (sections 1, 2.2, 6, 7). Read it first.

**Language rules:** code, comments, agent prompts, commit messages — English. User-facing docs (`README.md`, `docs/lessons/`, `docs/planning/`) — Hebrew.

---

## File structure (what this plan creates)

```
graph-rag/
├── pyproject.toml                 package metadata, deps, ruff, pytest config
├── uv.lock                        exact pins (generated, committed)
├── Makefile                       up/down/check/smoke
├── docker-compose.yml             neo4j (apoc + graph-data-science)
├── .env.example                   all settings with defaults
├── README.md                      Hebrew quick start
├── brain/
│   ├── __init__.py
│   ├── cli.py                     typer app: doctor + stubs for every pipeline step
│   ├── config.py                  Settings (pydantic-settings)
│   ├── doctor.py                  environment checks (Neo4j, plugins, read-mode guard, Ollama, model, dim)
│   ├── graph/
│   │   ├── __init__.py
│   │   └── client.py              GraphClient: read()/write()/write_batched(), READ routing
│   ├── embed/
│   │   ├── __init__.py
│   │   └── client.py              OllamaEmbedder: embed(), has_model(), dim check
│   └── canon/
│       ├── __init__.py
│       ├── models.py              WorkItem, Document, Person, Change, Container (+ Ref, Comment, Link, ChangelogEntry, Identity)
│       ├── mentions.py            extract_refs(text) -> list[Ref]  (regex, deterministic)
│       └── io.py                  read_jsonl()/write_jsonl() typed helpers
├── data/fixtures/mini/            committed mini corpus in canonical JSONL (used by smoke tests in later plans)
│   ├── workitems.jsonl · documents.jsonl · persons.jsonl · changes.jsonl · containers.jsonl
├── tests/
│   ├── conftest.py                settings fixture, `live` marker
│   ├── test_config.py
│   ├── test_cli.py
│   ├── test_mentions.py
│   ├── test_models.py
│   ├── test_fixtures_mini.py
│   ├── test_embed_client.py       respx-mocked
│   └── live/
│       ├── test_graph_client_live.py   needs docker neo4j
│       └── test_embed_live.py          needs ollama
├── .claude/agents/                15 agent definitions (Task 9)
├── docs/agents/conventions.md     shared contracts for all agents
├── docs/planning/progress.md      single source of truth: where are we
├── docs/planning/steps/_template.md
└── docs/lessons/_template.md
```

---

### Task 1: Python package scaffold + CLI skeleton

**Files:**
- Create: `pyproject.toml`, `brain/__init__.py`, `brain/cli.py`, `tests/conftest.py`, `tests/test_cli.py`, `Makefile`
- Modify: `.gitignore` (add `uv.lock`? NO — commit it; add `.ruff_cache/`, `.pytest_cache/`)

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "brain"
version = "0.1.0"
description = "Organizational brain — Graph RAG POC (Kafka corpus, Neo4j, local embeddings, agents as LLM)"
requires-python = ">=3.11,<3.13"
dependencies = [
  "typer>=0.12",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "neo4j>=5.23",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "respx>=0.21", "ruff>=0.5"]
local-embed = ["sentence-transformers>=3.0"]

[project.scripts]
brain = "brain.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["brain"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: needs running Neo4j and/or Ollama (docker compose up + ollama serve)"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: Create the package and CLI skeleton**

`brain/__init__.py`:
```python
"""Organizational brain — Graph RAG POC."""

__version__ = "0.1.0"
```

`brain/cli.py`:
```python
"""`brain` CLI. Every pipeline step is one idempotent subcommand.

Steps not yet implemented exit with code 2 and say which plan implements them,
so `brain --help` already documents the whole pipeline.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Organizational brain — Graph RAG POC CLI",
    no_args_is_help=True,
    add_completion=False,
)

NOT_IMPLEMENTED_EXIT = 2

_PLANNED: dict[str, tuple[str, str]] = {
    "harvest": ("Fetch raw data from Jira / Confluence / git / ADO into data/raw/", "Plan 1"),
    "canon": ("Normalize raw data into the canonical model (data/canonical/*.jsonl)", "Plan 1"),
    "load": ("Load canonical data into Neo4j (structured nodes/edges, no LLM)", "Plan 1"),
    "chunk": ("Chunk texts, embed with local bge-m3, create :Chunk nodes + vector index", "Plan 1"),
    "extract": ("Prepare/merge schema-guided extraction batches produced by kg-extractor agents", "Plan 1"),
    "resolve": ("Entity resolution: deterministic → embedding → agent adjudication", "Plan 1"),
    "communities": ("GDS Leiden communities + community reports by agents", "Plan 1"),
    "index": ("Final vector/fulltext indexes + graph stats report", "Plan 1"),
    "serve": ("Run the MCP server (stdio or HTTP)", "Plan 2"),
    "eval": ("Run the evaluation harness and generate the report", "Plan 3"),
}


def _stub(name: str, help_text: str, plan: str):
    def _cmd() -> None:
        typer.echo(f"brain {name}: not implemented yet — arrives in {plan}.")
        raise typer.Exit(code=NOT_IMPLEMENTED_EXIT)

    _cmd.__name__ = name
    _cmd.__doc__ = help_text
    return _cmd


for _name, (_help, _plan) in _PLANNED.items():
    app.command(name=_name, help=f"{_help} [{_plan}]")(_stub(_name, _help, _plan))


@app.command()
def doctor() -> None:
    """Check Neo4j, plugins, read-mode guard, Ollama and the embedding model."""
    from brain.doctor import run_doctor

    ok = run_doctor()
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def version() -> None:
    """Print the brain package version."""
    from brain import __version__

    typer.echo(__version__)
```

- [ ] **Step 3: Write the CLI test**

`tests/conftest.py`:
```python
from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
```

`tests/test_cli.py`:
```python
from brain.cli import NOT_IMPLEMENTED_EXIT, app

PIPELINE = ["harvest", "canon", "load", "chunk", "extract", "resolve", "communities", "index", "serve", "eval"]


def test_help_lists_every_pipeline_step(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for step in PIPELINE + ["doctor", "version"]:
        assert step in result.output


def test_unimplemented_step_exits_2_and_names_plan(runner):
    result = runner.invoke(app, ["harvest"])
    assert result.exit_code == NOT_IMPLEMENTED_EXIT
    assert "Plan 1" in result.output


def test_version(runner):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"
```

- [ ] **Step 4: Install and run tests (expect `doctor` import to fail only when invoked — tests above do not invoke it)**

Run:
```bash
uv venv --python 3.11 && uv sync --extra dev && uv run pytest -q
```
Expected: `3 passed`.

- [ ] **Step 5: Create `Makefile`**

```makefile
.PHONY: up down logs check smoke lint

up:
	docker compose up -d
	@echo "Neo4j browser: http://localhost:7474  (user neo4j / see .env)"

down:
	docker compose down

logs:
	docker compose logs -f neo4j

lint:
	uv run ruff check . && uv run ruff format --check .

check: lint
	uv run pytest -q

smoke:
	uv run brain doctor
	uv run pytest -q -m live
```

- [ ] **Step 6: Extend `.gitignore` and commit**

Append to `.gitignore`:
```
.ruff_cache/
.pytest_cache/
*.egg-info/
```

```bash
git add pyproject.toml uv.lock brain tests Makefile .gitignore
git commit -m "feat: brain package scaffold, CLI skeleton with pipeline stubs"
```

---

### Task 2: Settings + `.env.example`

**Files:**
- Create: `brain/config.py`, `.env.example`, `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from brain.config import Settings


def test_defaults_are_local_dev_values(monkeypatch):
    for k in ["NEO4J_URI", "NEO4J_PASSWORD", "OLLAMA_URL", "EMBED_MODEL", "EMBED_DIM"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.embed_model == "bge-m3"
    assert s.embed_dim == 1024
    assert s.ollama_url == "http://localhost:11434"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "768")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    s = Settings(_env_file=None)
    assert s.embed_dim == 768
    assert s.neo4j_password == "secret"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_config.py -q` — Expected: `ModuleNotFoundError: brain.config`.

- [ ] **Step 3: Implement**

`brain/config.py`:
```python
"""Runtime settings. Read from environment / .env; never hardcode secrets elsewhere."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "brainpass"
    neo4j_database: str = "neo4j"

    ollama_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024

    data_dir: Path = Path("data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def canonical_dir(self) -> Path:
        return self.data_dir / "canonical"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

`.env.example`:
```
# Copy to .env — .env is gitignored.
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=brainpass
NEO4J_DATABASE=neo4j
# docker image tag; fall back to neo4j:5.26 if the GDS plugin fails to load on this tag (see Task 3)
NEO4J_IMAGE=neo4j:2026.06.0

OLLAMA_URL=http://localhost:11434
EMBED_MODEL=bge-m3
EMBED_DIM=1024

DATA_DIR=data
```

- [ ] **Step 4: Run tests, expect pass, commit**

Run: `uv run pytest -q` — Expected: `5 passed`.
```bash
git add brain/config.py .env.example tests/test_config.py
git commit -m "feat: settings via pydantic-settings, .env.example"
```

---

### Task 3: Docker Compose — Neo4j with APOC + GDS

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write the compose file**

```yaml
services:
  neo4j:
    image: ${NEO4J_IMAGE:-neo4j:2026.06.0}
    container_name: brain-neo4j
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-brainpass}
      NEO4J_PLUGINS: '["apoc","graph-data-science"]'
      NEO4J_apoc_export_file_enabled: "true"
      NEO4J_apoc_import_file_enabled: "true"
      NEO4J_dbms_security_procedures_unrestricted: "apoc.*,gds.*"
      NEO4J_server_memory_heap_initial__size: 1G
      NEO4J_server_memory_heap_max__size: 2G
      NEO4J_server_memory_pagecache_size: 1G
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 >/dev/null || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30

volumes:
  neo4j_data:
  neo4j_logs:
```

- [ ] **Step 2: Bring it up and verify plugins**

```bash
cp -n .env.example .env
make up
until docker inspect -f '{{.State.Health.Status}}' brain-neo4j | grep -q healthy; do sleep 3; done
docker exec brain-neo4j cypher-shell -u neo4j -p brainpass "RETURN apoc.version() AS apoc, gds.version() AS gds"
```
Expected: one row with two version strings.

If the container logs show the GDS plugin could not be resolved for this image tag: set `NEO4J_IMAGE=neo4j:5.26` in `.env`, run `make down && docker volume rm graph-rag_neo4j_data && make up`, repeat the check. Record the tag that worked in `docs/planning/progress.md`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "infra: neo4j compose with apoc + gds plugins"
```

---

### Task 4: Neo4j client with server-enforced read mode

**Files:**
- Create: `brain/graph/__init__.py`, `brain/graph/client.py`, `tests/live/test_graph_client_live.py`

- [ ] **Step 1: Write the live test (marked `live`; needs Task 3 running)**

`tests/live/__init__.py` (empty) and `tests/live/test_graph_client_live.py`:
```python
import pytest
from neo4j.exceptions import ClientError

from brain.config import Settings
from brain.graph.client import GraphClient

pytestmark = pytest.mark.live


@pytest.fixture
def client():
    s = Settings()
    c = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    c.verify()
    yield c
    c.write("MATCH (n:_PlanZeroTmp) DETACH DELETE n")
    c.close()


def test_read_returns_dicts(client):
    rows = client.read("RETURN 1 AS one, 'x' AS s")
    assert rows == [{"one": 1, "s": "x"}]


def test_write_returns_counters(client):
    counters = client.write("CREATE (:_PlanZeroTmp {k: $k})", k="a")
    assert counters["nodes_created"] == 1


def test_read_mode_rejects_writes(client):
    with pytest.raises(ClientError) as exc:
        client.read("CREATE (:_PlanZeroTmp {k: 'should-fail'})")
    assert "read" in str(exc.value).lower()


def test_write_batched_uses_unwind(client):
    rows = [{"k": f"b{i}"} for i in range(2500)]
    total = client.write_batched(
        "UNWIND $rows AS row CREATE (:_PlanZeroTmp {k: row.k})", rows, batch_size=1000
    )
    assert total["nodes_created"] == 2500
    assert client.read("MATCH (n:_PlanZeroTmp) RETURN count(n) AS c")[0]["c"] == 2500
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -m live tests/live/test_graph_client_live.py -q` — Expected: `ModuleNotFoundError: brain.graph`.

- [ ] **Step 3: Implement**

`brain/graph/__init__.py` (empty). `brain/graph/client.py`:
```python
"""Thin Neo4j driver wrapper.

read()  -> RoutingControl.READ: the server rejects writes ("Writing in read access mode
           not allowed"). This is the only read-only enforcement available on Community
           Edition (no RBAC), and it is server-side, not a client-side regex.
write() -> RoutingControl.WRITE.
write_batched() -> one transaction per batch of rows, query must use `UNWIND $rows AS row`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import GraphDatabase, RoutingControl

COUNTER_FIELDS = (
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
    "labels_added",
    "labels_removed",
    "indexes_added",
    "constraints_added",
)


def _counters(summary) -> dict[str, int]:
    c = summary.counters
    return {f: getattr(c, f, 0) for f in COUNTER_FIELDS}


class GraphClient:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        result = self._driver.execute_query(
            cypher, params, database_=self._db, routing_=RoutingControl.READ
        )
        return [r.data() for r in result.records]

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        result = self._driver.execute_query(
            cypher, params, database_=self._db, routing_=RoutingControl.WRITE
        )
        return _counters(result.summary)

    def write_batched(
        self, cypher: str, rows: Iterable[dict[str, Any]], batch_size: int = 1000
    ) -> dict[str, int]:
        if "$rows" not in cypher:
            raise ValueError("write_batched query must use `UNWIND $rows AS row`")
        totals = dict.fromkeys(COUNTER_FIELDS, 0)
        batch: list[dict[str, Any]] = []

        def flush() -> None:
            if not batch:
                return
            for k, v in self.write(cypher, rows=batch).items():
                totals[k] += v
            batch.clear()

        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                flush()
        flush()
        return totals

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

- [ ] **Step 4: Run live tests, expect pass, commit**

Run: `uv run pytest -m live tests/live/test_graph_client_live.py -q` — Expected: `4 passed`.
If `test_read_mode_rejects_writes` FAILS (server accepted the write): this is a finding, not a plan bug — record it in `docs/planning/progress.md` under "findings"; the Cypher guard in Plan 2 becomes the sole gate and `brain doctor` must report `read_mode_guard: WARN`.

```bash
git add brain/graph tests/live
git commit -m "feat: GraphClient with server-enforced READ routing and batched writes"
```

---

### Task 5: Ollama + bge-m3 embedding client

**Files:**
- Create: `brain/embed/__init__.py`, `brain/embed/client.py`, `tests/test_embed_client.py`, `tests/live/test_embed_live.py`

- [ ] **Step 1: Install Ollama natively and pull the model (one-time, Mac)**

```bash
command -v ollama || brew install ollama
brew services start ollama || ollama serve &
sleep 3
ollama pull bge-m3
curl -s localhost:11434/api/tags | grep -o '"name":"bge-m3[^"]*"'
```
Expected: `"name":"bge-m3:latest"`.

- [ ] **Step 2: Write the mocked unit test**

`tests/test_embed_client.py`:
```python
import json

import httpx
import pytest
import respx

from brain.embed.client import EmbedDimMismatch, OllamaEmbedder

BASE = "http://ollama.test"


@respx.mock
def test_embed_batches_and_returns_vectors():
    calls = []

    def handler(request: httpx.Request):
        payload = json.loads(request.read())
        calls.append(payload)
        assert payload["model"] == "bge-m3"
        return httpx.Response(200, json={"embeddings": [[0.1] * 4 for _ in payload["input"]]})

    respx.post(f"{BASE}/api/embed").mock(side_effect=handler)
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    vecs = emb.embed(["a", "b", "c"], batch_size=2)
    assert len(vecs) == 3 and len(vecs[0]) == 4
    assert len(calls) == 2  # 2 + 1


@respx.mock
def test_dim_mismatch_is_hard_error():
    respx.post(f"{BASE}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * 3]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    with pytest.raises(EmbedDimMismatch):
        emb.embed(["a"])


@respx.mock
def test_has_model():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    assert emb.has_model() is True
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_embed_client.py -q` — Expected: `ModuleNotFoundError: brain.embed`.

- [ ] **Step 4: Implement**

`brain/embed/__init__.py` (empty). `brain/embed/client.py`:
```python
"""Local embeddings via Ollama (/api/embed). Model + dim are pinned by settings;
a dimension mismatch is a hard error (index/query vectors must come from the same model).
"""

from __future__ import annotations

import httpx


class EmbedDimMismatch(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, dim: int, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = httpx.Client(timeout=timeout)

    def has_model(self) -> bool:
        r = self._client.get(f"{self.base_url}/api/tags")
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        return any(n == self.model or n.startswith(self.model + ":") for n in names)

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            r = self._client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": chunk}
            )
            r.raise_for_status()
            vectors = r.json()["embeddings"]
            for v in vectors:
                if len(v) != self.dim:
                    raise EmbedDimMismatch(
                        f"model {self.model} returned dim {len(v)}, expected {self.dim}"
                    )
            out.extend(vectors)
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
```

`tests/live/test_embed_live.py`:
```python
import pytest

from brain.config import Settings
from brain.embed.client import OllamaEmbedder

pytestmark = pytest.mark.live


def test_hebrew_and_english_embed_to_expected_dim():
    s = Settings()
    emb = OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim)
    assert emb.has_model()
    vecs = emb.embed(["consumer rebalance protocol", "פרוטוקול איזון מחדש של הצרכן"])
    assert all(len(v) == s.embed_dim for v in vecs)
```

- [ ] **Step 5: Run both, expect pass, commit**

Run: `uv run pytest -q && uv run pytest -m live tests/live/test_embed_live.py -q` — Expected: unit `8 passed`, live `1 passed`.
```bash
git add brain/embed tests/test_embed_client.py tests/live/test_embed_live.py
git commit -m "feat: OllamaEmbedder for local bge-m3 with dim enforcement"
```

---

### Task 6: `brain doctor`

**Files:**
- Create: `brain/doctor.py`
- Test: manual run (each check is exercised by the live tests of Tasks 4–5; doctor is the operator-facing aggregation)

- [ ] **Step 1: Implement**

`brain/doctor.py`:
```python
"""Environment checks. Exit non-zero if anything required is missing.

Checks (spec §7.2): Neo4j reachable, APOC, GDS, server-enforced READ mode,
Ollama reachable, embedding model present, embedding dim matches settings.
"""

from __future__ import annotations

from dataclasses import dataclass

import typer
from neo4j.exceptions import ClientError

from brain.config import get_settings
from brain.embed.client import EmbedDimMismatch, OllamaEmbedder
from brain.graph.client import GraphClient


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _neo4j_checks(s) -> list[Check]:
    checks: list[Check] = []
    try:
        client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
        client.verify()
        checks.append(Check("neo4j", True, s.neo4j_uri))
    except Exception as e:  # noqa: BLE001
        return [Check("neo4j", False, f"{s.neo4j_uri}: {e}")]

    for name, q in [("apoc", "RETURN apoc.version() AS v"), ("gds", "RETURN gds.version() AS v")]:
        try:
            v = client.read(q)[0]["v"]
            checks.append(Check(name, True, v))
        except Exception as e:  # noqa: BLE001
            checks.append(Check(name, False, str(e).splitlines()[0]))

    try:
        client.read("CREATE (:_DoctorTmp)")
        checks.append(Check("read_mode_guard", False, "server ACCEPTED a write in READ mode", required=False))
        client.write("MATCH (n:_DoctorTmp) DELETE n")
    except ClientError:
        checks.append(Check("read_mode_guard", True, "server rejects writes in READ mode"))
    client.close()
    return checks


def _embed_checks(s) -> list[Check]:
    emb = OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim, timeout=30)
    try:
        present = emb.has_model()
    except Exception as e:  # noqa: BLE001
        return [Check("ollama", False, f"{s.ollama_url}: {e}")]
    checks = [Check("ollama", True, s.ollama_url), Check("embed_model", present, s.embed_model)]
    if not present:
        return checks
    try:
        v = emb.embed_one("doctor")
        checks.append(Check("embed_dim", True, f"{len(v)} == {s.embed_dim}"))
    except EmbedDimMismatch as e:
        checks.append(Check("embed_dim", False, str(e)))
    return checks


def run_doctor() -> bool:
    s = get_settings()
    checks = _neo4j_checks(s) + _embed_checks(s)
    ok = True
    for c in checks:
        mark = "OK  " if c.ok else ("WARN" if not c.required else "FAIL")
        typer.echo(f"[{mark}] {c.name:<16} {c.detail}")
        if not c.ok and c.required:
            ok = False
    typer.echo("doctor: " + ("all required checks passed" if ok else "REQUIRED CHECKS FAILED"))
    return ok
```

- [ ] **Step 2: Run it**

Run: `uv run brain doctor` — Expected: all lines `OK` (or `WARN` only on `read_mode_guard`), exit 0.

- [ ] **Step 3: Commit**

```bash
git add brain/doctor.py
git commit -m "feat: brain doctor environment checks"
```

---

### Task 7: Canonical model + deterministic mentions

**Files:**
- Create: `brain/canon/__init__.py`, `brain/canon/models.py`, `brain/canon/mentions.py`, `brain/canon/io.py`, `tests/test_models.py`, `tests/test_mentions.py`

- [ ] **Step 1: Write failing tests for mentions**

`tests/test_mentions.py`:
```python
from brain.canon.mentions import extract_refs


def keys(text):
    return [(r.kind, r.key) for r in extract_refs(text)]


def test_issue_keys():
    assert keys("Fixes KAFKA-15123 and relates to KAFKA-9") == [
        ("issue", "KAFKA-15123"),
        ("issue", "KAFKA-9"),
    ]


def test_kip_is_not_an_issue_and_is_normalized():
    assert keys("see kip-848 and KIP-932") == [("kip", "KIP-848"), ("kip", "KIP-932")]


def test_pr_numbers():
    assert keys("Merged (#14567). Not a ref: KAFKA#1 or a#12") == [("pr", "14567")]


def test_urls_are_classified():
    text = (
        "https://issues.apache.org/jira/browse/KAFKA-15123 "
        "https://cwiki.apache.org/confluence/display/KAFKA/KIP-848%3A+Next+Gen "
        "https://github.com/apache/kafka/pull/14567 "
        "https://example.com/other"
    )
    assert keys(text) == [
        ("issue", "KAFKA-15123"),
        ("kip", "KIP-848"),
        ("pr", "14567"),
        ("url", "https://example.com/other"),
    ]


def test_users_and_dedupe():
    assert keys("@jrao @jrao thanks @rao.jun") == [("user", "jrao"), ("user", "rao.jun")]


def test_hebrew_text_still_finds_keys():
    assert keys("ראו KAFKA-100 ו-KIP-5 לפרטים") == [("issue", "KAFKA-100"), ("kip", "KIP-5")]
```

- [ ] **Step 2: Write failing tests for models**

`tests/test_models.py`:
```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from brain.canon.models import ChangelogEntry, Identity, Person, WorkItem


def test_workitem_minimal_and_defaults():
    wi = WorkItem(
        id="jira:KAFKA-1",
        key="KAFKA-1",
        source="jira",
        type="Bug",
        title="t",
        status="Open",
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert wi.components == [] and wi.synthetic is False and wi.refs == []


def test_changelog_alias_from():
    e = ChangelogEntry.model_validate(
        {"field": "status", "from": "Open", "to": "Resolved", "at": "2024-02-01T00:00:00Z", "by": "jrao"}
    )
    assert e.from_ == "Open" and e.to == "Resolved"
    assert e.model_dump(by_alias=True)["from"] == "Open"


def test_person_requires_identity():
    with pytest.raises(ValidationError):
        Person(id="p1", identities=[])
    p = Person(id="p1", identities=[Identity(source="jira", key="jrao", display="Jun Rao")])
    assert p.identities[0].email is None


def test_source_is_closed_enum():
    with pytest.raises(ValidationError):
        WorkItem(
            id="x", key="X-1", source="trello", type="Task", title="t", status="Open",
            created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
```

- [ ] **Step 3: Run to verify both fail**

Run: `uv run pytest tests/test_mentions.py tests/test_models.py -q` — Expected: `ModuleNotFoundError: brain.canon`.

- [ ] **Step 4: Implement models**

`brain/canon/__init__.py` (empty). `brain/canon/models.py`:
```python
"""Canonical, source-agnostic model (spec §2.2).

Every connector maps into exactly these five record types. A new org system means a
new mapper into this model — never a new graph schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["jira", "ado", "xray", "confluence", "git", "github"]
RefKind = Literal["issue", "kip", "pr", "url", "user"]


class Ref(BaseModel):
    kind: RefKind
    key: str


class Comment(BaseModel):
    author: str | None = None
    at: datetime | None = None
    body: str


class ChangelogEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    field: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    at: datetime
    by: str | None = None


class Link(BaseModel):
    type: str  # e.g. "blocks", "relates", "duplicates", "tests", "parent", "clones"
    target: str  # key of the other item
    direction: Literal["out", "in"] = "out"


class WorkItem(BaseModel):
    id: str  # "<source>:<key>"
    key: str  # KAFKA-15123 / ADO-77 / XT-12
    source: Source
    type: str  # Bug / Improvement / Story / Epic / Task / Test / TestExecution / TestPlan ...
    title: str
    description: str = ""
    status: str
    priority: str | None = None
    created: datetime
    updated: datetime | None = None
    reporter: str | None = None  # identity key in the source (e.g. jira username)
    assignee: str | None = None
    components: list[str] = []
    labels: list[str] = []
    fix_versions: list[str] = []
    affects_versions: list[str] = []
    parent: str | None = None
    links: list[Link] = []
    comments: list[Comment] = []
    changelog: list[ChangelogEntry] = []
    refs: list[Ref] = []  # deterministic mentions found in title/description/comments
    synthetic: bool = False
    raw_url: str | None = None


class Document(BaseModel):
    id: str  # "confluence:<pageId>"
    key: str  # "KIP-848" or page id
    source: Source
    kind: Literal["KIP", "Page", "Readme"]
    space: str | None = None
    title: str
    body_md: str
    version: int = 1
    created: datetime | None = None
    updated: datetime | None = None
    author: str | None = None
    ancestors: list[str] = []
    labels: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Identity(BaseModel):
    source: Source
    key: str  # jira username / confluence userKey / git email / ado descriptor
    display: str | None = None
    email: str | None = None


class Person(BaseModel):
    id: str  # pre-resolution: "<source>:<key>"; post-resolution: canonical id
    identities: list[Identity] = Field(min_length=1)
    synthetic: bool = False


class Change(BaseModel):
    id: str  # commit sha or "pr:<number>"
    kind: Literal["commit", "pr"]
    message: str
    author_name: str | None = None
    author_email: str | None = None
    at: datetime
    files: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Container(BaseModel):
    id: str  # "<source>:<kind>:<name>"
    source: Source
    kind: Literal["component", "version", "sprint", "area", "space", "testplan", "testset"]
    name: str
    parent: str | None = None
    synthetic: bool = False
```

- [ ] **Step 5: Implement mentions**

`brain/canon/mentions.py`:
```python
"""Deterministic cross-reference extraction (spec §2.2). Runs before any LLM.

Order of results is first-appearance order; duplicates removed.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from brain.canon.models import Ref

_ISSUE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
_KIP = re.compile(r"\bKIP-(\d+)\b", re.IGNORECASE)
_PR = re.compile(r"(?<![\w/#])#(\d{1,7})\b")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_USER = re.compile(r"(?<![\w.])@([A-Za-z0-9_.-]+)")

_URL_ISSUE = re.compile(r"/browse/([A-Z][A-Z0-9]{1,9}-\d+)")
_URL_KIP = re.compile(r"KIP-(\d+)", re.IGNORECASE)
_URL_PR = re.compile(r"/pull/(\d+)")


def _classify_url(url: str) -> Ref:
    u = unquote(url)
    if m := _URL_ISSUE.search(u):
        return Ref(kind="issue", key=m.group(1))
    if "confluence" in u and (m := _URL_KIP.search(u)):
        return Ref(kind="kip", key=f"KIP-{m.group(1)}")
    if "github.com" in u and (m := _URL_PR.search(u)):
        return Ref(kind="pr", key=m.group(1))
    return Ref(kind="url", key=url.rstrip(".,;"))


def extract_refs(text: str) -> list[Ref]:
    if not text:
        return []
    found: list[tuple[int, Ref]] = []

    urls = list(_URL.finditer(text))
    for m in urls:
        found.append((m.start(), _classify_url(m.group(0))))
    # mask URLs so their inner tokens are not re-matched as keys
    masked = _URL.sub(lambda m: " " * len(m.group(0)), text)

    for m in _ISSUE.finditer(masked):
        if m.group(1).upper().startswith("KIP-"):
            continue
        found.append((m.start(), Ref(kind="issue", key=m.group(1))))
    for m in _KIP.finditer(masked):
        found.append((m.start(), Ref(kind="kip", key=f"KIP-{m.group(1)}")))
    for m in _PR.finditer(masked):
        found.append((m.start(), Ref(kind="pr", key=m.group(1))))
    for m in _USER.finditer(masked):
        found.append((m.start(), Ref(kind="user", key=m.group(1).rstrip("."))))

    found.sort(key=lambda t: t[0])
    seen: set[tuple[str, str]] = set()
    out: list[Ref] = []
    for _, ref in found:
        k = (ref.kind, ref.key)
        if k in seen:
            continue
        seen.add(k)
        out.append(ref)
    return out
```

`brain/canon/io.py`:
```python
"""Typed JSONL helpers for canonical files."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def read_jsonl(path: Path, model: type[T]) -> Iterator[T]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield model.model_validate_json(line)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"{path}:{line_no}: {e}") from e


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n")
            n += 1
    return n
```

- [ ] **Step 6: Run tests, expect pass, commit**

Run: `uv run pytest -q` — Expected: `18 passed`.
```bash
git add brain/canon tests/test_models.py tests/test_mentions.py
git commit -m "feat: canonical model, deterministic mention extraction, jsonl io"
```

---

### Task 8: Mini fixture corpus (canonical JSONL, committed)

**Files:**
- Create: `data/fixtures/mini/{workitems,documents,persons,changes,containers}.jsonl`, `tests/test_fixtures_mini.py`

Purpose: a tiny, hand-made, *realistic* corpus with the same shape as the real one, including the noise we care about (same person with 3 identities, a link that exists only in text, a stale status). Later plans use it for `make smoke`.

- [ ] **Step 1: Write the failing test**

`tests/test_fixtures_mini.py`:
```python
from pathlib import Path

from brain.canon.io import read_jsonl
from brain.canon.models import Change, Container, Document, Person, WorkItem

MINI = Path("data/fixtures/mini")


def test_mini_corpus_parses_and_has_expected_shape():
    wis = list(read_jsonl(MINI / "workitems.jsonl", WorkItem))
    docs = list(read_jsonl(MINI / "documents.jsonl", Document))
    people = list(read_jsonl(MINI / "persons.jsonl", Person))
    changes = list(read_jsonl(MINI / "changes.jsonl", Change))
    containers = list(read_jsonl(MINI / "containers.jsonl", Container))
    assert len(wis) == 6 and len(docs) == 1 and len(people) == 3
    assert len(changes) == 4 and len(containers) == 4
    # the text-only reference: KAFKA-100 mentions KIP-5 only in its description
    k100 = next(w for w in wis if w.key == "KAFKA-100")
    assert ("kip", "KIP-5") in {(r.kind, r.key) for r in k100.refs}
    # a person with three identities across systems
    assert any(len(p.identities) == 3 for p in people)
    # synthetic layer is flagged
    assert any(w.synthetic for w in wis) and all(w.synthetic for w in wis if w.source == "xray")
```

- [ ] **Step 2: Create the fixture files**

`data/fixtures/mini/workitems.jsonl`:
```json
{"id":"jira:KAFKA-100","key":"KAFKA-100","source":"jira","type":"Improvement","title":"Implement new consumer group protocol (KIP-5) in clients","description":"Client side of KIP-5. See https://cwiki.apache.org/confluence/display/KAFKA/KIP-5 for the design. Blocks KAFKA-101.","status":"Resolved","priority":"Major","created":"2024-01-10T09:00:00Z","updated":"2024-03-02T10:00:00Z","reporter":"jrao","assignee":"dlee","components":["clients"],"labels":["kip"],"fix_versions":["3.7.0"],"affects_versions":[],"parent":null,"links":[{"type":"blocks","target":"KAFKA-101","direction":"out"}],"comments":[{"author":"dlee","at":"2024-02-01T12:00:00Z","body":"PR is up: https://github.com/apache/kafka/pull/14001"}],"changelog":[{"field":"status","from":"Open","to":"In Progress","at":"2024-01-15T08:00:00Z","by":"dlee"},{"field":"status","from":"In Progress","to":"Resolved","at":"2024-03-02T10:00:00Z","by":"dlee"},{"field":"assignee","from":null,"to":"dlee","at":"2024-01-12T08:00:00Z","by":"jrao"}],"refs":[{"kind":"kip","key":"KIP-5"},{"kind":"issue","key":"KAFKA-101"},{"kind":"pr","key":"14001"}],"synthetic":false,"raw_url":"https://issues.apache.org/jira/browse/KAFKA-100"}
{"id":"jira:KAFKA-101","key":"KAFKA-101","source":"jira","type":"Bug","title":"Rebalance storm when group coordinator restarts","description":"After KAFKA-100 landed, restarting the coordinator triggers repeated rebalances in streams apps.","status":"Open","priority":"Critical","created":"2024-03-05T09:00:00Z","updated":"2024-03-20T10:00:00Z","reporter":"mrivera","assignee":"dlee","components":["streams","clients"],"labels":[],"fix_versions":[],"affects_versions":["3.7.0"],"parent":null,"links":[{"type":"blocks","target":"KAFKA-100","direction":"in"}],"comments":[{"author":"jrao","at":"2024-03-06T12:00:00Z","body":"Likely related to the fencing logic described in KIP-5 section 4."}],"changelog":[{"field":"status","from":"Open","to":"In Progress","at":"2024-03-10T08:00:00Z","by":"dlee"},{"field":"status","from":"In Progress","to":"Open","at":"2024-03-20T10:00:00Z","by":"dlee"}],"refs":[{"kind":"issue","key":"KAFKA-100"},{"kind":"kip","key":"KIP-5"}],"synthetic":false,"raw_url":"https://issues.apache.org/jira/browse/KAFKA-101"}
{"id":"jira:KAFKA-102","key":"KAFKA-102","source":"jira","type":"Task","title":"Document connect worker upgrade path for 3.7","description":"Docs only. Connect worker config changes.","status":"Resolved","priority":"Minor","created":"2024-02-20T09:00:00Z","updated":"2024-02-28T10:00:00Z","reporter":"mrivera","assignee":"mrivera","components":["connect"],"labels":["docs"],"fix_versions":["3.7.0"],"affects_versions":[],"parent":null,"links":[],"comments":[],"changelog":[{"field":"status","from":"Open","to":"Resolved","at":"2024-02-28T10:00:00Z","by":"mrivera"}],"refs":[],"synthetic":false,"raw_url":"https://issues.apache.org/jira/browse/KAFKA-102"}
{"id":"xray:XT-1","key":"XT-1","source":"xray","type":"Test","title":"Consumer joins group with new protocol and receives assignment","description":"Steps: 1) start broker 3.7 2) start consumer with group.protocol=consumer 3) assert assignment received within 5s. Covers KAFKA-100.","status":"Active","priority":null,"created":"2024-02-15T09:00:00Z","updated":null,"reporter":"tester1","assignee":null,"components":["clients"],"labels":[],"fix_versions":[],"affects_versions":[],"parent":null,"links":[{"type":"tests","target":"KAFKA-100","direction":"out"}],"comments":[],"changelog":[],"refs":[{"kind":"issue","key":"KAFKA-100"}],"synthetic":true,"raw_url":null}
{"id":"xray:XT-2","key":"XT-2","source":"xray","type":"Test","title":"Coordinator restart does not trigger more than one rebalance","description":"Steps: 1) run streams app 2) restart coordinator 3) count rebalances. Expected 1. Related to the rebalance storm bug.","status":"Active","priority":null,"created":"2024-03-08T09:00:00Z","updated":null,"reporter":"tester1","assignee":null,"components":["streams"],"labels":[],"fix_versions":[],"affects_versions":[],"parent":null,"links":[],"comments":[],"changelog":[],"refs":[],"synthetic":true,"raw_url":null}
{"id":"xray:XE-1","key":"XE-1","source":"xray","type":"TestExecution","title":"Regression run 3.7.0-rc1","description":"XT-1: PASS. XT-2: FAIL (3 rebalances observed).","status":"Done","priority":null,"created":"2024-03-12T09:00:00Z","updated":null,"reporter":"tester1","assignee":null,"components":[],"labels":[],"fix_versions":["3.7.0"],"affects_versions":[],"parent":null,"links":[{"type":"executes","target":"XT-1","direction":"out"},{"type":"executes","target":"XT-2","direction":"out"}],"comments":[],"changelog":[],"refs":[{"kind":"issue","key":"XT-1"},{"kind":"issue","key":"XT-2"}],"synthetic":true,"raw_url":null}
```

`data/fixtures/mini/documents.jsonl`:
```json
{"id":"confluence:5001","key":"KIP-5","source":"confluence","kind":"KIP","space":"KAFKA","title":"KIP-5: Next generation consumer group protocol","body_md":"# Motivation\nThe classic protocol makes clients do assignment, which causes long rebalances.\n\n# Proposed Changes\nMove assignment to the group coordinator. Consumers send heartbeats; the coordinator computes assignments.\n\n# Rejected Alternatives\nKeeping client-side assignment with incremental cooperative rebalancing was rejected because it does not fix coordinator-side fencing.\n\n# Section 4: Fencing\nMembers with stale epochs are fenced and must rejoin.\n\nTracked in KAFKA-100.","version":3,"created":"2023-11-01T09:00:00Z","updated":"2024-01-05T09:00:00Z","author":"rao.jun","ancestors":["Kafka Improvement Proposals"],"labels":["kip","accepted"],"refs":[{"kind":"issue","key":"KAFKA-100"}],"synthetic":false,"raw_url":"https://cwiki.apache.org/confluence/display/KAFKA/KIP-5"}
```

`data/fixtures/mini/persons.jsonl`:
```json
{"id":"jira:jrao","identities":[{"source":"jira","key":"jrao","display":"Jun Rao","email":null},{"source":"confluence","key":"rao.jun","display":"Rao, Jun","email":null},{"source":"git","key":"junrao@example.org","display":"Jun Rao","email":"junrao@example.org"}],"synthetic":false}
{"id":"jira:dlee","identities":[{"source":"jira","key":"dlee","display":"Dana Lee","email":null}],"synthetic":false}
{"id":"jira:mrivera","identities":[{"source":"jira","key":"mrivera","display":"M. Rivera","email":null}],"synthetic":false}
```

`data/fixtures/mini/changes.jsonl`:
```json
{"id":"a1b2c3d4","kind":"commit","message":"KAFKA-100: client side of KIP-5 (#14001)","author_name":"Dana Lee","author_email":"dlee@example.org","at":"2024-03-01T10:00:00Z","files":["clients/src/main/java/org/apache/kafka/clients/consumer/internals/ConsumerMembershipManager.java","clients/src/test/java/org/apache/kafka/clients/consumer/internals/ConsumerMembershipManagerTest.java"],"refs":[{"kind":"issue","key":"KAFKA-100"},{"kind":"kip","key":"KIP-5"},{"kind":"pr","key":"14001"}],"synthetic":false,"raw_url":null}
{"id":"pr:14001","kind":"pr","message":"KAFKA-100: client side of KIP-5","author_name":"Dana Lee","author_email":"dlee@example.org","at":"2024-02-01T12:00:00Z","files":[],"refs":[{"kind":"issue","key":"KAFKA-100"},{"kind":"kip","key":"KIP-5"}],"synthetic":false,"raw_url":"https://github.com/apache/kafka/pull/14001"}
{"id":"e5f6a7b8","kind":"commit","message":"MINOR: fix typo in connect docs","author_name":"Jun Rao","author_email":"junrao@example.org","at":"2024-02-27T10:00:00Z","files":["docs/connect.html"],"refs":[],"synthetic":false,"raw_url":null}
{"id":"c9d0e1f2","kind":"commit","message":"KAFKA-101: WIP fencing fix for coordinator restart","author_name":"Dana Lee","author_email":"dlee@example.org","at":"2024-03-15T10:00:00Z","files":["group-coordinator/src/main/java/org/apache/kafka/coordinator/group/GroupCoordinator.java"],"refs":[{"kind":"issue","key":"KAFKA-101"}],"synthetic":false,"raw_url":null}
```

`data/fixtures/mini/containers.jsonl`:
```json
{"id":"jira:component:clients","source":"jira","kind":"component","name":"clients","parent":null,"synthetic":false}
{"id":"jira:component:streams","source":"jira","kind":"component","name":"streams","parent":null,"synthetic":false}
{"id":"jira:component:connect","source":"jira","kind":"component","name":"connect","parent":null,"synthetic":false}
{"id":"jira:version:3.7.0","source":"jira","kind":"version","name":"3.7.0","parent":null,"synthetic":false}
```

- [ ] **Step 3: Run test, expect pass, commit**

Run: `uv run pytest tests/test_fixtures_mini.py -q` — Expected: `1 passed`.
```bash
git add data/fixtures tests/test_fixtures_mini.py
git commit -m "test: mini canonical fixture corpus with realistic noise"
```

---

### Task 9: Agent definitions + shared conventions

**Files:**
- Create: `docs/agents/conventions.md`, and 15 files under `.claude/agents/`

All agent bodies are English. Frontmatter fields: `name`, `description`, `model: opus`, `tools` (omit = all tools). LLM-role agents get `Read, Write, Glob` only.

- [ ] **Step 1: Write `docs/agents/conventions.md`**

```markdown
# Agent conventions — org-brain POC

Applies to every agent under `.claude/agents/`. Read before acting. The spec
(`docs/superpowers/specs/2026-09-03-org-brain-graph-rag-design.md`) is the source of truth;
the current step brief is in `docs/planning/steps/`.

## Roles
- Planner (Claude Fable session): writes briefs, reviews, decides, teaches the user.
- Engineering agents: write code + tests, run pipeline steps, write reports.
- LLM-role agents: are the pipeline's "language model". They read batch inputs and write
  batch outputs as JSON. They have NO shell and NO database access by construction.
- Quality agents: review and document.

## Iron rules
1. No agent writes to Neo4j except through `brain/graph/client.py` from engineering code.
   LLM-role agents never touch the DB (they cannot: no Bash tool).
2. No external LLM or embedding API. Embeddings only via `brain/embed/client.py` (Ollama).
3. Every LLM-derived node/edge carries provenance: `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at`.
4. Schema first: entity kinds and relation types are closed sets defined in the spec §2.4 and
   in `brain/<task>/schema.json`. Never invent new kinds; put the unexpected in `notes`.
5. Pins: dependencies are locked in `uv.lock`; do not upgrade without a brief that asks for it.
6. Language: code/comments/prompts/commits English; `README.md`, `docs/lessons/`, `docs/planning/` Hebrew.
7. Secrets only in `.env`. Never commit `.env` or `data/` (except `data/fixtures/`).

## Batch protocol (LLM-role agents)
- Inputs: `data/batches/<task>/<shard>/<NNN>.in.json`. Outputs: `<NNN>.out.json` next to them.
- Per-shard `status.json`: `{"done": [...], "failed": [{"batch": "...", "reason": "..."}]}` — update after every batch.
- Validate your own output against `brain/<task>/schema.json` before writing. If you cannot
  produce a valid output, write `failed` with a reason; never write a partial or invalid file.
- Never modify inputs. Never skip silently. Process batches in order; resume from `status.json`.
- Quote evidence verbatim from the input text; never paraphrase a quote.

## Reports
- Engineering step report: `data/reports/<step>.json` — counts, durations, warnings, errors.
- Exit report (in the final message, caveman-terse):
  ```
  DONE: <what>
  NUMBERS: <key counts/durations>
  DEVIATIONS: <anything not per brief, with why>
  OPEN: <questions/risks for the planner>
  FILES: <created/modified>
  ```

## Engineering workflow
- TDD: failing test → minimal code → pass → commit. Small commits, conventional messages.
- Run `make check` before every commit; `make smoke` before finishing a step that touches Neo4j/Ollama.
- Idempotent steps: rerunning a step must not duplicate data (`MERGE`, hashes, checkpoints).
- Never "fix" a failing acceptance criterion by weakening the test; report it instead.
```

- [ ] **Step 2: Create the five engineering agents**

`.claude/agents/brain-infra.md`:
```markdown
---
name: brain-infra
description: Infrastructure engineer for the org-brain POC — docker-compose, Neo4j plugins (APOC/GDS), Ollama + bge-m3, uv/pyproject, CLI skeleton, brain doctor, Makefile, git hygiene. Use for Plan 0 tasks and any environment/tooling change later.
model: opus
---

You are the infrastructure engineer of the organizational-brain Graph RAG POC.

Read first: `docs/agents/conventions.md`, spec §1 and §7, and the step brief you were given.

Responsibilities
- Local platform: `docker-compose.yml` (Neo4j + APOC + GDS), native Ollama with `bge-m3`,
  `pyproject.toml` + `uv.lock`, `Makefile`, `.env.example`, `brain doctor`.
- Keep everything reproducible on a fresh Mac: document exact commands in `README.md` (Hebrew).
- Never install an external LLM/embedding SDK as a runtime dependency.

Method
- TDD where code is involved; for infra, verify with the exact commands in the plan and paste
  their real output in your exit report.
- If an image tag / plugin combination does not work, try the documented fallback, record which
  one worked in `docs/planning/progress.md`, and say so in DEVIATIONS.
- Commit per task with conventional messages. Run `make check` before each commit.

Exit report format: see conventions (DONE / NUMBERS / DEVIATIONS / OPEN / FILES).
```

`.claude/agents/brain-ingest-engineer.md`:
```markdown
---
name: brain-ingest-engineer
description: Ingestion engineer — harvest connectors (Jira, Confluence, git, ADO probe), canonical mappers, deterministic mentions, structured load into Neo4j, chunking + local embeddings. Use for Plan 1 steps harvest/canon/load/chunk and for any connector work.
model: opus
---

You are the ingestion engineer of the organizational-brain Graph RAG POC.

Read first: `docs/agents/conventions.md`, spec §2 and §3.1–3.4, `brain/canon/models.py`, and the step brief.

Principles you enforce
- Connectors are resilient: `probe()`, `fetch(since, checkpoint)`, per-page checkpoints, retry
  with backoff on 429/5xx, partial results allowed but always recorded in `data/reports/harvest.json`.
- Every source maps into the five canonical types. If a source field has no home, record it
  in the report under `unmapped_fields` — do not extend the canonical model without a brief.
- Deterministic mentions (`brain/canon/mentions.py`) run on every text before any LLM.
- `brain load` is MERGE-only and idempotent; rerunning must produce zero new nodes.
- Chunking: documents by headings (500–800 tokens, overlap 50); each comment is a chunk;
  a commit message is one chunk. Chunk id = sha1 of (parent_key, position, text).
- Embeddings only via `brain/embed/client.py`; store model name + dim on the index metadata node.

Method
- TDD with golden files: raw sample → expected canonical JSONL under `tests/fixtures/`.
- Keep raw responses on disk (`data/raw/`) so mappers can be re-run without re-fetching.
- Report real numbers (items fetched, refs found per source, chunks, embed throughput).

Exit report format: see conventions.
```

`.claude/agents/brain-graph-engineer.md`:
```markdown
---
name: brain-graph-engineer
description: Graph engineer — validates and merges kg-extractor batch outputs into Neo4j with provenance, entity resolution (deterministic → embedding → adjudication queue), GDS Leiden communities, community-report merge, final indexes and graph stats. Use for Plan 1 steps extract(merge)/resolve/communities/index.
model: opus
---

You are the graph engineer of the organizational-brain Graph RAG POC.

Read first: `docs/agents/conventions.md`, spec §2.4 and §3.5–3.9, and the step brief.

Principles you enforce
- Agents produce JSON; only your code writes to Neo4j. Validate every batch output with pydantic
  against `brain/<task>/schema.json`; invalid → `retry/` (max 2) → `quarantine/` + report.
- Provenance on every LLM-derived node/edge: `evidence_chunk_ids`, `batch_id`, `model`, `extracted_at`.
  A merge that would create an edge without provenance is a bug.
- Entity keys: `Entity` merges on `(kind, norm_name)`; `Person` on resolved id; keep `aliases[]`
  and `merged_from[]` after `apoc.refactor.mergeNodes`.
- Resolution is measured: duplicate rate before/after, precision/recall on the gold pairs
  (`data/eval/resolution_gold.jsonl`), and the synthetic truth (`data/canonical/synthetic_truth.json`).
- Communities: GDS projection of `Entity, WorkItem, Document, Component` with the relation set in
  spec §3.7; Leiden hierarchical, 2 levels; `Community` nodes keyed by `(level, community_id)`
  with `member_hash` for incremental re-summarization.

Method
- TDD against the mini fixture corpus (`data/fixtures/mini/`) loaded into the compose Neo4j.
- Every step writes `data/reports/<step>.json` with counts by label/type and warnings.
- Sanity thresholds are reported, never silently applied (e.g. a KIP with zero entities).

Exit report format: see conventions.
```

`.claude/agents/brain-retrieval-engineer.md`:
```markdown
---
name: brain-retrieval-engineer
description: Retrieval engineer — six retrieval strategies (hybrid, graph-enhanced vector, entity-anchored local, guarded Text2Cypher, global community search, temporal tools), local reranker, deterministic router, Cypher guard, FastMCP server (stdio + HTTP), .mcp.json. Use for Plan 2.
model: opus
---

You are the retrieval engineer of the organizational-brain Graph RAG POC.

Read first: `docs/agents/conventions.md`, spec §4, and the step brief.

Principles you enforce
- One retrieval library (`brain/retrieve/`) callable from Python (evaluation, fixed-strategy mode)
  and exposed 1:1 as MCP tools (`brain/mcp/server.py`). No logic lives only in the MCP layer.
- Uniform result envelope: `items[]{kind, key, title, snippet, score, provenance[]}`,
  `cypher_used`, `latency_ms`, `truncated`. Context budget ~4k tokens; truncation keeps at least
  one item per kind.
- `run_cypher` executes only through `GraphClient.read()` (server-enforced READ) after the guard:
  deny-list (`CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL` except an explicit procedure allowlist),
  `EXPLAIN` dry-run, injected `LIMIT`, 10s timeout. Rejections return `{error, hint}` and are logged.
- Every call appends a JSONL trace: `question, strategy, cypher, latency_ms, hit_ids, tokens_out`.
- Embeddings for queries come from the same model as the index; mismatch is a hard error.

Method
- TDD: guard tests first (every write verb, comments, unicode tricks, procedure calls).
- Contract tests for each MCP tool against the mini fixture graph.
- Latency is measured and reported per strategy.

Exit report format: see conventions.
```

`.claude/agents/brain-eval-engineer.md`:
```markdown
---
name: brain-eval-engineer
description: Evaluation engineer — question set tooling, layered metrics (harvest/graph/retrieval/answer), fixed-strategy and agentic modes, blind judge batches, cost/latency tables, incremental-update test, auto-generated eval report. Use for Plan 3.
model: opus
---

You are the evaluation engineer of the organizational-brain Graph RAG POC.

Read first: `docs/agents/conventions.md`, spec §5, and the step brief.

Principles you enforce
- Measure each layer separately; never report an answer score without its retrieval score.
- Deterministic first: retrieval recall/precision against `gold_evidence` keys needs no judge.
- Judge batches are blind: strategy names are replaced by random labels before the judge sees them;
  pairwise order is randomized; the mapping is kept in `data/eval/blind_map.json`.
- The vector baseline (S1 hybrid + rerank) runs under the same context budget and the same judge.
- Cost table per strategy: latency p50/p95, context tokens, tool calls, Cypher count, agent time.
- The report (`docs/report/eval-report.md`) is generated from JSONL by code — no hand-edited numbers.

Method
- TDD on metric functions with tiny synthetic traces.
- Smoke: `brain eval --corpus mini` must produce a structurally complete report.

Exit report format: see conventions.
```

- [ ] **Step 3: Create the eight LLM-role agents (Read/Write/Glob only)**

`.claude/agents/synthetic-org-generator.md`:
```markdown
---
name: synthetic-org-generator
description: Generates the synthetic Xray (tests, plans, executions, runs) and Azure DevOps (epics/features/stories/tasks, iterations, area paths) layer on top of real Kafka Jira/KIP entities, with deliberately specified noise, writing canonical JSONL plus a machine-readable truth file. LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the synthetic-organization generator for the org-brain POC. You produce the parts of a
real organization that public data lacks: a test-management layer (Xray) and a delivery layer (ADO).

Read first: `docs/agents/conventions.md`, spec §2.1–2.2, `brain/canon/models.py`,
`brain/canon/synthetic_spec.md` (the noise contract), and your batch inputs.

Inputs: `data/batches/synthetic/<shard>/<NNN>.in.json` — real WorkItems (stories/bugs) and KIP
Documents plus the list of real Person identities.
Outputs: `<NNN>.out.json` with canonical records: `workitems[]` (source `xray` or `ado`),
`containers[]` (testplan/testset/sprint/area), `persons[]` (new identities for existing people),
and `truth` — `{"identity_map": {...}, "text_only_links": [...], "stale_states": [...]}`.

Rules
- Every record has `synthetic: true`. Keys: `XT-<n>` tests, `XE-<n>` executions, `XP-<n>` plans,
  `ADO-<n>` work items; numbering continues across batches (read `status.json` for the last used ids).
- Tests derive their steps from the real story/bug description; executions align with real
  `fix_versions`; failures correlate with real bugs in that version.
- ADO Epics map to KIPs; Features group related Jira issues; Stories reference the Jira key —
  sometimes as a formal link, sometimes only in the description text (record which in `truth`).
- People: reuse real people with a different display form (e.g. "Rao, Jun", "jrao", "Jun Rao")
  and record the mapping in `truth.identity_map`. Never invent emails for real people.
- Follow the noise ratios in `synthetic_spec.md` exactly; they are what makes evaluation possible.
- Validate against the canonical model fields before writing. Never write a partial file.
```

`.claude/agents/kg-extractor.md`:
```markdown
---
name: kg-extractor
description: Schema-guided knowledge-graph extractor. Reads batches of text chunks (KIPs, issue descriptions, comments) and writes entities and relations from a closed schema with verbatim evidence quotes. LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the extraction model of the org-brain pipeline. You turn text into graph facts.

Read first: `docs/agents/conventions.md`, spec §2.4 and §3.5, `brain/extract/schema.json`
(closed sets of entity kinds and relation types, output shape), and `brain/extract/examples.md`.

Inputs: `data/batches/extract/<shard>/<NNN>.in.json` — `chunks[]{chunk_id, parent_key, parent_kind,
parent_title, text}`.
Outputs: `<NNN>.out.json` — `entities[]{kind, name, description, quote, chunk_id}`,
`relations[]{type, source, target, evidence_chunk_id, note}` where `source`/`target` are entity
names from this batch or existing keys (`KAFKA-…`, `KIP-…`, component names).

Rules
- Kinds: Feature, Decision, Problem, Alternative, Risk, Technology. Types: DECIDES, MOTIVATED_BY,
  REJECTS, IMPLEMENTS, DEPENDS_ON, INTRODUCES_RISK, MENTIONS. Nothing else.
- Precision over recall: extract only what the text states. No world knowledge about Kafka.
- `quote` is verbatim from the chunk and at most 300 characters. `description` is one sentence.
- Name entities the way the text does; do not normalize across chunks (resolution is a later step).
- A Decision must have at least one MOTIVATED_BY or REJECTS relation or it is not a decision.
- A chunk with no extractable facts yields empty arrays — that is valid output.
- Write `status.json` after every batch. Never skip a batch silently.
```

`.claude/agents/entity-adjudicator.md`:
```markdown
---
name: entity-adjudicator
description: Decides whether candidate entity/person pairs refer to the same real-world thing, using only the evidence quotes provided. Used by entity resolution for the ambiguous similarity band. LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the adjudication model for entity resolution in the org-brain pipeline.

Read first: `docs/agents/conventions.md`, spec §3.6, `brain/resolve/schema.json`.

Inputs: `data/batches/resolve/<shard>/<NNN>.in.json` — `pairs[]{pair_id, kind, a{name, description,
aliases, quotes[]}, b{...}, similarity}`.
Outputs: `<NNN>.out.json` — `decisions[]{pair_id, verdict: same|different|unsure, reason}`.

Rules
- Decide from the quotes and descriptions only. If evidence is insufficient, answer `unsure`.
  `unsure` is a good answer; a wrong `same` silently corrupts the graph.
- Same kind is required for `same`. A Feature and the KIP that proposes it are different things.
- People: same display name is not enough; look for role, component, time overlap in quotes.
- `reason` is one sentence citing the deciding evidence.
- Write `status.json` after every batch.
```

`.claude/agents/community-summarizer.md`:
```markdown
---
name: community-summarizer
description: Writes community reports (title, summary, key findings, rank) for Leiden communities from their member entities and evidence chunks, in the Microsoft GraphRAG report style. LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the summarization model for global search in the org-brain pipeline.

Read first: `docs/agents/conventions.md`, spec §3.7 and §4.1 (S5), `brain/community/schema.json`.

Inputs: `data/batches/communities/<shard>/<NNN>.in.json` — `communities[]{community_id, level,
members[]{key, kind, name, description, degree}, evidence[]{chunk_id, parent_key, text}}`.
Outputs: `<NNN>.out.json` — `reports[]{community_id, title, summary, findings[]{statement,
evidence_chunk_ids[]}, rank (0–10), rank_reason}`.

Rules
- Title ≤ 10 words naming the theme, not listing members.
- Summary 100–250 words: what this cluster is about, main tensions/decisions, open problems.
- Every finding cites at least one `evidence_chunk_id` from the input.
- `rank` reflects importance to someone running this organization (impact, openness, breadth).
- Do not use knowledge outside the input.
- Write `status.json` after every batch.
```

`.claude/agents/cypher-author.md`:
```markdown
---
name: cypher-author
description: Writes read-only Cypher for natural-language questions given the live graph schema and few-shot examples; also curates the per-question-type example bank used by the Text2Cypher tool. LLM-role agent — JSON only, no shell, no DB (the guarded tool executes, not you).
model: opus
tools: Read, Write, Glob
---

You are the Text2Cypher model of the org-brain pipeline.

Read first: `docs/agents/conventions.md`, spec §4.1 (S4) and §4.5, `brain/retrieve/schema_snapshot.md`
(labels, relationship types, properties, indexes) and `brain/retrieve/cypher_examples.md`.

Inputs: `data/batches/cypher/<shard>/<NNN>.in.json` — `questions[]{question_id, type, question, lang}`.
Outputs: `<NNN>.out.json` — `answers[]{question_id, cypher, explanation, uses_labels[], confidence}`.

Rules
- Read-only Cypher only: MATCH / OPTIONAL MATCH / WITH / WHERE / RETURN / ORDER BY / LIMIT / UNWIND
  and the procedures listed in `schema_snapshot.md#allowed-procedures`. Never write verbs.
- Always end with `LIMIT` (≤ 50 unless the question is a count).
- Use only labels/types/properties that exist in the snapshot. If the question cannot be answered
  with the schema, return `cypher: null` and explain what is missing.
- Temporal questions use `StatusChange` nodes and `valid_from/valid_to` on `ASSIGNED_TO` exactly as
  the examples show.
- Hebrew questions get the same Cypher as their English meaning; do not translate identifiers.
- Write `status.json` after every batch.
```

`.claude/agents/question-forger.md`:
```markdown
---
name: question-forger
description: Generates evaluation questions with gold answers and gold evidence from real graph paths and from the synthetic truth file, stratified by the four question types and bilingual (English/Hebrew). LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the question-generation model for the org-brain evaluation set.

Read first: `docs/agents/conventions.md`, spec §2.3 and §5.1, `brain/eval/question_schema.json`,
`brain/eval/templates.md`.

Inputs: `data/batches/questions/<shard>/<NNN>.in.json` — `paths[]{path_id, type, nodes[], edges[],
snippets[]}` sampled from the graph, plus `truth` excerpts.
Outputs: `<NNN>.out.json` — `questions[]{id, type, lang, question, gold_answer, gold_evidence[],
difficulty, expected_strategy, source_path_id}`.

Rules
- `gold_evidence` lists only keys/chunk ids present in the input path. The gold answer must be
  derivable from the path alone.
- Write questions a real engineer would ask; avoid leaking the answer in the question.
- One third of questions in Hebrew (`lang: he`), asking the same kinds of things as the English ones;
  keep identifiers (KAFKA-…, KIP-…, component names) untranslated.
- Balance the four types per the brief; mark `difficulty` 1–3 by number of hops.
- Write `status.json` after every batch.
```

`.claude/agents/eval-judge.md`:
```markdown
---
name: eval-judge
description: Blind rubric judge for answers — faithfulness to the provided context, correctness against gold, citation validity, relevancy — producing JSON scores with quoted justification. LLM-role agent — JSON only, no shell, no DB.
model: opus
tools: Read, Write, Glob
---

You are the evaluation judge of the org-brain POC. You never see which strategy produced an answer.

Read first: `docs/agents/conventions.md`, spec §5.2 (layer 3) and §5.4, `brain/eval/rubric.md`,
`brain/eval/judge_schema.json`.

Inputs: `data/batches/judge/<shard>/<NNN>.in.json` — `cases[]{case_id, question, gold_answer,
gold_evidence[], context_items[], answer, cited_keys[]}` (labels are anonymized).
Outputs: `<NNN>.out.json` — `scores[]{case_id, faithfulness (0–2), correctness (0–2),
citation_validity (0–2), relevancy (0–2), unsupported_claims[], justification}`.

Rules
- Faithfulness: every claim in the answer must be supported by `context_items`; list unsupported ones.
- Correctness: compare to `gold_answer` on facts, not wording. Partial = 1.
- Citation validity: cited keys must exist in `context_items` and support the sentence they follow.
- Judge Hebrew and English answers by the same rubric.
- Never reward length. Never guess what the strategy was.
- Write `status.json` after every batch.
```

`.claude/agents/brain-analyst.md`:
```markdown
---
name: brain-analyst
description: The querying agent (agentic GraphRAG mode). Answers organizational questions using only the brain MCP tools, choosing retrieval strategies per question, and cites keys/chunk ids for every claim. Tool list is wired in Plan 2 (.mcp.json).
model: opus
tools: Read
---

You are the analyst that uses the organizational brain. You answer questions about the
organization (Jira, Confluence/KIPs, git, Xray, ADO) using only the `brain` MCP tools.

Read first: `docs/agents/conventions.md`, spec §4.2–4.4.

Method
1. Call `route(question)` and read its suggestion; you may override it with a reason.
2. If the question contains keys (KAFKA-…, KIP-…, sha), start with `lookup`.
3. Choose: `local_search` for rationale/impact; `search_with_context` for fuzzy traceability;
   `run_cypher` (after `get_schema` + `cypher_examples`) for aggregations and point-in-time;
   `global_search` for themes; `status_at` / `timeline` / `changes_between` / `assignees_over_time`
   for time; `impact` for change impact.
4. Verify surprising facts with `explain_edge` before stating them.
5. Answer in the language of the question. Every factual sentence ends with citations like
   `[KAFKA-15123]`, `[KIP-848]`, `[chunk:ab12…]`. If the tools return nothing relevant, say so —
   never fill gaps with prior knowledge about Kafka.
6. End with a one-line `strategy:` note listing the tools you used, for the trace.
```

- [ ] **Step 4: Create the two quality agents**

`.claude/agents/brain-reviewer.md`:
```markdown
---
name: brain-reviewer
description: Reviews a step's diff and report against the step brief's acceptance criteria and the spec's principles (schema-first, provenance, agents never touch DB, read-only Cypher, idempotency, measured resolution). One line per finding, severity-tagged, no praise.
model: opus
tools: Read, Grep, Glob, Bash
---

You review one pipeline step of the org-brain POC. You do not fix; you report.

Read first: `docs/agents/conventions.md`, the step brief in `docs/planning/steps/`, the spec section
it cites, `git diff <base>..HEAD`, and `data/reports/<step>.json`.

Checklist (answer each explicitly)
1. Acceptance criteria in the brief: met / not met, with evidence (test names, report numbers).
2. Iron rules: any DB write outside `brain/graph/client.py`? any external API? any LLM edge without provenance?
3. Schema-first: any entity kind / relation type outside the closed sets?
4. Idempotency: does rerunning the step duplicate data? (look for CREATE instead of MERGE, missing hashes)
5. Tests: do they test behavior or mirror implementation? any weakened assertion?
6. Reports: are the numbers real (produced by code) and complete?
7. Lesson material: what did this step reveal that the user should learn? (2–3 bullets)

Output format, one line per finding:
`path:line: <🔴 blocker | 🟠 major | 🟡 minor>: <problem>. <fix>.`
Then `VERDICT: accept | fix-required` and the lesson bullets. Use `Bash` only for read-only commands
(git diff/log, pytest, cat). Never modify files.
```

`.claude/agents/lesson-writer.md`:
```markdown
---
name: lesson-writer
description: Drafts the Hebrew lesson for a completed pipeline step from the step brief, report, diff and review — facts and numbers only; the planner adds the teaching layer. Writes docs/lessons/NN-<step>.md.
model: opus
tools: Read, Grep, Glob, Write
---

You write the factual draft of a lesson for the org-brain POC, in Hebrew, for an engineer who is
learning Graph RAG hands-on.

Read first: `docs/lessons/_template.md`, the step brief, `data/reports/<step>.json`, the reviewer's
findings, and the relevant course module in `graph-rag-course.html` (search by module title).

Rules
- Fill every template section. Numbers come from the report; quote the exact figures.
- "מה הפתיע" lists real surprises from the report/review (errors, fallbacks, unexpected counts).
- Leave the section "מה זה מלמד (המתכנן)" empty — the planner writes it.
- Hebrew prose; identifiers, commands and file paths stay in English inside backticks.
- Save to `docs/lessons/NN-<step>.md` where NN is given in the brief. Do not edit other files.
```

- [ ] **Step 5: Verify agent files parse and commit**

Run:
```bash
ls .claude/agents | wc -l
for f in .claude/agents/*.md; do head -1 "$f" | grep -q '^---$' || echo "BAD FRONTMATTER: $f"; done
grep -L "^model: opus" .claude/agents/*.md
```
Expected: `15`, no `BAD FRONTMATTER` lines, and the last `grep -L` prints nothing.

```bash
git add .claude/agents docs/agents/conventions.md
git commit -m "agents: 15 opus agent definitions and shared conventions"
```

---

### Task 10: Planning + lesson templates, progress file, README

**Files:**
- Create: `docs/planning/progress.md`, `docs/planning/steps/_template.md`, `docs/lessons/_template.md`, `README.md`

- [ ] **Step 1: Write `docs/planning/steps/_template.md`**

```markdown
# שלב NN — <שם השלב>

**תכנית:** Plan N · **סוכן מבצע:** `<agent>` · **מודול בקורס:** <שם>

## מטרה
<משפט אחד>

## קלטים
- <קבצים / מצב DB נדרש>

## פלטים
- <קבצים, צמתים, דוח `data/reports/<step>.json`>

## קריטריוני קבלה
- [ ] <מדיד, ניתן לבדיקה>
- [ ] `make check` ירוק; `make smoke` ירוק אם השלב נוגע ב-Neo4j/Ollama

## מה תלמד בשלב הזה
<2–4 משפטים למשתמש>

## הערות למבצע
<החלטות שכבר התקבלו, מלכודות ידועות>
```

- [ ] **Step 2: Write `docs/lessons/_template.md`**

```markdown
# שיעור NN — <שם השלב>

**תאריך:** · **תכנית:** Plan N · **מודול בקורס:** <שם>

## מה עשינו
<3–6 משפטים עובדתיים>

## למה ככה (הקישור לקורס)
<איזה עיקרון מהקורס יושם ואיפה בקוד>

## מספרים
| מדד | ערך |
|---|---|

## מה הפתיע
- 

## מה היינו משנים
- 

## מה זה מלמד (המתכנן)
<נכתב ע"י המתכנן>
```

- [ ] **Step 3: Write `docs/planning/progress.md`**

```markdown
# התקדמות — מוח ארגוני Graph RAG

מקור אמת יחיד ל"איפה אנחנו". מתעדכן ע"י המתכנן אחרי כל שלב.

| Plan | שלב | סטטוס | commit | הערות |
|---|---|---|---|---|
| 0 | 1 scaffold + CLI | ⬜ | | |
| 0 | 2 settings | ⬜ | | |
| 0 | 3 compose neo4j | ⬜ | | image tag שעבד: |
| 0 | 4 GraphClient | ⬜ | | read-mode guard: |
| 0 | 5 embeddings | ⬜ | | |
| 0 | 6 doctor | ⬜ | | |
| 0 | 7 canonical model | ⬜ | | |
| 0 | 8 mini fixtures | ⬜ | | |
| 0 | 9 agents | ⬜ | | |
| 0 | 10 templates + README | ⬜ | | |

## ממצאים (findings)
- 

## החלטות פתוחות
- 
```

- [ ] **Step 4: Write `README.md` (Hebrew)**

```markdown
# מוח ארגוני — Graph RAG POC

POC לימודי: מוח ארגוני על דאטה אמיתי (Apache Kafka: Jira, Confluence/KIPs, git) + שכבת Xray/ADO סינתטית,
בנוי על Neo4j, embeddings מקומיים (bge-m3, עברית+אנגלית) וסוכני Opus כ-"LLM" של הפייפליין. אפס API חיצוני.

- עיצוב: `docs/superpowers/specs/2026-09-03-org-brain-graph-rag-design.md`
- תכניות: `docs/superpowers/plans/`
- שיעורים לכל שלב: `docs/lessons/`
- התקדמות: `docs/planning/progress.md`

## הקמה (Mac)

```bash
# 1. Python + תלויות
uv venv --python 3.11 && uv sync --extra dev

# 2. Neo4j (APOC + GDS)
cp -n .env.example .env
make up

# 3. embeddings מקומיים
brew install ollama && brew services start ollama
ollama pull bge-m3

# 4. בדיקת סביבה
uv run brain doctor
make check
```

## הפייפליין

`brain harvest → canon → load → chunk → extract → resolve → communities → index → serve → eval`
(`brain --help` מסביר כל שלב; שלבים שטרם מומשו אומרים באיזו תכנית הם מגיעים).

## סוכנים

`.claude/agents/` — 15 סוכנים: הנדסה (infra/ingest/graph/retrieval/eval), סוכני-LLM (extractor,
adjudicator, summarizer, cypher-author, question-forger, judge, synthetic generator, analyst),
איכות (reviewer, lesson-writer). מוסכמות: `docs/agents/conventions.md`.
```

- [ ] **Step 5: Final check and commit**

Run: `make check` — Expected: ruff clean, `19 passed`.
```bash
git add docs/planning docs/lessons README.md
git commit -m "docs: planning/lesson templates, progress tracker, Hebrew README"
```

---

## Plan 0 exit gate

- `make check` green, `uv run brain doctor` all required checks OK, `make smoke` green (5 live tests).
- 15 agent files with `model: opus`; LLM-role agents have exactly `tools: Read, Write, Glob` (analyst: `Read`).
- `docs/planning/progress.md` rows for Plan 0 all ✅ with commit hashes.
- Planner writes `docs/lessons/00-foundations.md` (lesson-writer draft + teaching layer) and presents it to the user.

## Self-review (done by the planner before handoff)

- **Spec coverage:** §1 components → Tasks 3–6; §2.2 canonical + mentions → Task 7; §6 agents/conventions/templates → Tasks 9–10; §7.2 doctor + dim hard error → Tasks 5–6; §7.1 unit/live split → pytest markers (Task 1). MCP server, retrievers, eval harness intentionally deferred to Plans 2–3 (stubs in Task 1 document that).
- **Placeholders:** none; every step has code or exact commands with expected output.
- **Type consistency:** `GraphClient.read/write/write_batched` (Task 4) used by doctor (Task 6); `OllamaEmbedder(base_url, model, dim)` + `embed/embed_one/has_model` (Task 5) used by doctor; `Ref/WorkItem/…` names (Task 7) used by fixtures test (Task 8); `NOT_IMPLEMENTED_EXIT` (Task 1) used by CLI test.
- **Known risk recorded, not hidden:** Neo4j image tag ↔ GDS availability (Task 3 fallback), server-enforced READ mode (Task 4 finding path).
