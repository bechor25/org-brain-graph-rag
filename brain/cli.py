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
    "load": ("Load canonical data into Neo4j (structured nodes/edges, no LLM)", "Plan 1"),
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
