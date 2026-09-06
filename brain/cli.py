"""`brain` CLI. Every pipeline step is one idempotent subcommand.

Steps not yet implemented exit with code 2 and say which plan implements them,
so `brain --help` already documents the whole pipeline.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    help="Organizational brain — Graph RAG POC CLI",
    no_args_is_help=True,
    add_completion=False,
)

NOT_IMPLEMENTED_EXIT = 2

_PLANNED: dict[str, tuple[str, str]] = {
    "chunk": ("Chunk texts, embed with local bge-m3, create :Chunk nodes + vector index", "Plan 1"),
    "extract": (
        "Prepare/merge schema-guided extraction batches produced by kg-extractor agents",
        "Plan 1",
    ),
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
def harvest(
    source: str = typer.Option(
        "all", "--source", help="Which connector to run: jira | confluence | git | all"
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        metavar="YYYY-MM-DD",
        help="Incremental pull: Jira/Confluence by updated/lastmodified, git by commit date. "
        "Writes into data/raw/<source>/since-<date>/ so the full pull stays intact.",
    ),
) -> None:
    """Fetch raw data from Jira / Confluence / git into data/raw/ [Plan 1]."""
    from datetime import date as _date

    from brain.config import get_settings
    from brain.harvest.runner import resolve_sources, run_harvest

    try:
        sources = resolve_sources(source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--source") from exc

    since_date: _date | None = None
    if since:
        try:
            since_date = _date.fromisoformat(since)
        except ValueError as exc:
            raise typer.BadParameter(f"{since!r} is not YYYY-MM-DD", param_hint="--since") from exc

    settings = get_settings()
    _, code = run_harvest(
        sources,
        raw_dir=settings.raw_dir,
        reports_dir=settings.reports_dir,
        since=since_date,
        echo=typer.echo,
    )
    raise typer.Exit(code=code)


@app.command()
def canon(
    source: str = typer.Option(
        "all", "--source", help="Which mapper to run: jira | confluence | git | all"
    ),
) -> None:
    """Normalize raw data into the canonical model (data/canonical/*.jsonl) [Plan 1]."""
    from brain.canon.runner import CanonError, resolve_sources, run_canon
    from brain.config import get_settings

    try:
        sources = resolve_sources(source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--source") from exc

    settings = get_settings()
    try:
        _, code = run_canon(
            sources,
            raw_dir=settings.raw_dir,
            canonical_dir=settings.canonical_dir,
            reports_dir=settings.reports_dir,
            echo=typer.echo,
        )
    except CanonError as exc:
        # A refusal to write, not a crash: the message says what to do about it.
        # On stderr, so a caller piping stdout still sees why nothing was written.
        typer.echo(f"canon: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def load(
    schema_only: bool = typer.Option(
        False,
        "--schema-only",
        help="Only create constraints and indexes, load nothing.",
    ),
    canonical_dir: str | None = typer.Option(
        None,
        "--canonical-dir",
        metavar="PATH",
        help="Read the canonical JSONL from here instead of data/canonical "
        "(e.g. data/fixtures/mini).",
    ),
) -> None:
    """Load canonical data into Neo4j (structured nodes/edges, no LLM) [Plan 1]."""
    from brain.config import get_settings
    from brain.graph.runner import load_from_settings

    settings = get_settings()
    source = Path(canonical_dir) if canonical_dir else settings.canonical_dir
    if not source.is_dir():
        raise typer.BadParameter(f"{source} is not a directory", param_hint="--canonical-dir")
    try:
        _, code = load_from_settings(
            source,
            settings.reports_dir,
            schema_only=schema_only,
            echo=typer.echo,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"load: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


synth_app = typer.Typer(
    help="Build and merge the synthetic Xray/ADO layer written by synthetic-org-generator "
    "agents [Plan 1]",
    no_args_is_help=True,
)
app.add_typer(synth_app, name="synth")


@synth_app.command("build")
def synth_build(
    shards: int = typer.Option(3, "--shards", min=1, help="How many agents will work in parallel"),
    batch_size: int = typer.Option(
        40, "--batch-size", min=1, help="Real work items per batch (keep each .in.json <150 KB)"
    ),
) -> None:
    """Write data/batches/synthetic/<shard>/NNN.in.json from the real canonical slice."""
    from brain.config import get_settings
    from brain.synth.build import run_build

    settings = get_settings()
    try:
        _, code = run_build(
            canonical_dir=settings.canonical_dir,
            batches_dir=settings.batches_dir,
            shards=shards,
            batch_size=batch_size,
            echo=typer.echo,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"synth build: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@synth_app.command("merge")
def synth_merge() -> None:
    """Validate every NNN.out.json and rewrite the synthetic slice of data/canonical/."""
    from brain.config import get_settings
    from brain.synth.merge import MergeError, run_merge

    settings = get_settings()
    try:
        _, code = run_merge(
            canonical_dir=settings.canonical_dir,
            batches_dir=settings.batches_dir,
            reports_dir=settings.reports_dir,
            echo=typer.echo,
        )
    except (MergeError, OSError, ValueError) as exc:
        typer.echo(f"synth merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def doctor() -> None:
    """Check Neo4j, plugins, read-mode guard, Ollama and the embedding model."""
    # lazy: keeps --help and other commands importable without neo4j/httpx at import time
    from brain.doctor import run_doctor

    ok = run_doctor()
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def version() -> None:
    """Print the brain package version."""
    from brain import __version__

    typer.echo(__version__)
