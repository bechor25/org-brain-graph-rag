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
    from brain.graph.provenance import ProvenanceError
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
    except ProvenanceError as exc:
        # A synthetic layer loaded without provenance would break conventions rule 3 and
        # nothing downstream could tell. Refuse loudly instead of loading it bare.
        typer.echo(f"load: synthetic provenance ledger unusable: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"load: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def chunk(
    kinds: str = typer.Option(
        "all",
        "--kinds",
        help="Which texts to chunk: doc,issue,comment,commit (comma separated) or all.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Cap the parent records (documents + work items + commits), taken "
        "proportionally from each kind. Use it to time a slice before the full run.",
    ),
    measure: bool = typer.Option(
        False,
        "--measure",
        help="Only measure embedding throughput on 200 real chunks at batch 8/16/32/64, "
        "write the table to data/reports/chunk.json and exit. Writes nothing to the graph.",
    ),
    all_docs: bool = typer.Option(
        False,
        "--all-docs",
        help="Chunk every Document, not only the KIPs the harvested slice references "
        "(Phase A default is the referenced ones).",
    ),
    measure_sample: int | None = typer.Option(
        None,
        "--measure-sample",
        min=1,
        help="How many chunks --measure times (default 200). Pass a number larger than "
        "the corpus to time a full embedding pass without writing anything.",
    ),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        min=1,
        help="Override the batch size --measure chose. With --measure, times only this one.",
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", min=1, help="Override the HTTP timeout --measure chose, in seconds."
    ),
    canonical_dir: str | None = typer.Option(
        None,
        "--canonical-dir",
        metavar="PATH",
        help="Read the canonical JSONL from here instead of data/canonical.",
    ),
) -> None:
    """Chunk texts, embed with local bge-m3, create :Chunk nodes + vector index [Plan 1]."""
    from brain.chunk.runner import chunk_from_settings, resolve_kinds
    from brain.config import get_settings

    try:
        selected = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc

    settings = get_settings()
    source = Path(canonical_dir) if canonical_dir else settings.canonical_dir
    if not source.is_dir():
        raise typer.BadParameter(f"{source} is not a directory", param_hint="--canonical-dir")
    try:
        _, code = chunk_from_settings(
            source,
            settings.reports_dir,
            kinds=selected,
            limit=limit,
            all_docs=all_docs,
            measure_only=measure,
            measure_sample=measure_sample,
            batch_size=batch_size,
            timeout=timeout,
            echo=typer.echo,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"chunk: {exc}", err=True)
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


extract_app = typer.Typer(
    help="Schema-guided extraction: build batches for the kg-extractor agents, merge what "
    "they wrote into the graph, sample it for a human precision check [Plan 1]",
    no_args_is_help=True,
)
app.add_typer(extract_app, name="extract")


@extract_app.command("build")
def extract_build(
    shards: int = typer.Option(4, "--shards", min=1, help="How many agents work in parallel"),
    batch_size: int = typer.Option(
        20, "--batch-size", min=1, help="Chunks per batch, before the 40 KB size split"
    ),
    min_chars: int | None = typer.Option(
        None,
        "--min-chars",
        min=1,
        help="Override the 300-character floor on issue descriptions (Phase A rule).",
    ),
    canonical_dir: str | None = typer.Option(
        None,
        "--canonical-dir",
        metavar="PATH",
        help="Read the canonical JSONL from here instead of data/canonical. The KIP set "
        "comes from it via brain.chunk.scope, so it must be the one brain chunk used.",
    ),
) -> None:
    """Write data/batches/extract/<shard>/NNN.in.json from the Phase A chunks in the graph."""
    from brain.config import get_settings
    from brain.extract.build import BuildError
    from brain.extract.runner import build_from_settings

    settings = get_settings()
    source = Path(canonical_dir) if canonical_dir else settings.canonical_dir
    if not source.is_dir():
        raise typer.BadParameter(f"{source} is not a directory", param_hint="--canonical-dir")
    try:
        _, code = build_from_settings(
            source,
            settings.batches_dir,
            settings.reports_dir,
            shards=shards,
            batch_size=batch_size,
            min_chars=min_chars,
            echo=typer.echo,
        )
    except (BuildError, OSError, ValueError) as exc:
        typer.echo(f"extract build: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@extract_app.command("merge")
def extract_merge() -> None:
    """Validate every NNN.out.json and merge entities/relations into Neo4j with provenance."""
    from brain.config import get_settings
    from brain.extract.merge import MergeError
    from brain.extract.runner import merge_from_settings

    settings = get_settings()
    try:
        _, code = merge_from_settings(settings.batches_dir, settings.reports_dir, echo=typer.echo)
    except (MergeError, OSError, ValueError) as exc:
        typer.echo(f"extract merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@extract_app.command("sample")
def extract_sample(
    n: int = typer.Option(50, "--n", min=1, help="How many MENTIONS to draw"),
    seed: int = typer.Option(7, "--seed", help="Same seed, same sample — so it can be re-read"),
    targets: str = typer.Option(
        "entity",
        "--targets",
        help="Which mentions to draw: entity (what the extractor decided) or all "
        "(also the deterministic key matches to Document/WorkItem/Component).",
    ),
) -> None:
    """Print random MENTIONS with quote and surrounding text for a human precision check."""
    from brain.config import get_settings
    from brain.extract.runner import sample_from_settings
    from brain.extract.sample import TARGETS

    if targets not in TARGETS:
        raise typer.BadParameter(f"must be one of {sorted(TARGETS)}", param_hint="--targets")
    settings = get_settings()
    try:
        _, code = sample_from_settings(
            settings.reports_dir, n=n, seed=seed, targets=targets, echo=typer.echo
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"extract sample: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


resolve_app = typer.Typer(
    help="Entity resolution: deterministic → embedding → agent adjudication [Plan 1]",
    invoke_without_command=True,
)
app.add_typer(resolve_app, name="resolve")


@resolve_app.callback(invoke_without_command=True)
def resolve(
    ctx: typer.Context,
    kinds: str = typer.Option(
        "all", "--kinds", help="What to resolve: person | entity | all (comma separated)."
    ),
    tier: str = typer.Option(
        "all",
        "--tier",
        help="Which tiers to run: 1 (deterministic), 2 (embedding), 3 (agent decisions), "
        "or all. Tiers run in order, each against the graph the previous one left.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Propose and report merges without writing SAME_AS, merging a node or "
        "touching the ledger. Tier 2 still writes embeddings — it cannot score without "
        "them, and a vector is derived data, not a decision.",
    ),
    k: int = typer.Option(
        25, "--k", min=1, help="Neighbours per candidate the tier-2 vector index returns."
    ),
    vector_evidence: int = typer.Option(
        0,
        "--vector-evidence",
        min=0,
        help="How many touched-item titles go INTO the tier-2 vector. 0 (default) embeds "
        "the display name alone; 5 reproduces brief 08 decision 3. Measured on this "
        "corpus, titles in the vector drop the median true pair to cosine 0.63 — under "
        "the 0.80 floor — and pull different people who share a backlog together. Either "
        "way the titles reach the adjudicator as evidence.",
    ),
) -> None:
    """Merge duplicate people and entities, and measure it [Plan 1]."""
    if ctx.invoked_subcommand is not None:
        return
    from brain.resolve.runner import (
        ResolveError,
        resolve_from_settings,
        resolve_kinds,
        resolve_tiers,
    )

    try:
        chosen_kinds = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc
    try:
        chosen_tiers = resolve_tiers(tier)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--tier") from exc

    try:
        _, code = resolve_from_settings(
            kinds=chosen_kinds,
            tiers=chosen_tiers,
            dry_run=dry_run,
            k=k,
            vector_evidence=vector_evidence,
            echo=typer.echo,
        )
    except (ResolveError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"resolve: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@resolve_app.command("build-batches")
def resolve_build_batches(
    kinds: str = typer.Option("all", "--kinds", help="person | entity | all."),
    shards: int = typer.Option(2, "--shards", min=1, help="How many agents work in parallel."),
    k: int = typer.Option(25, "--k", min=1, help="Neighbours per candidate from the index."),
    vector_evidence: int = typer.Option(
        0,
        "--vector-evidence",
        min=0,
        help="Touched titles inside the vector; see `resolve --help`.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Rebuild even though a shard reports finished batches."
    ),
) -> None:
    """Write the tier-2 grey band to data/batches/resolve/<shard>/NNN.in.json."""
    from brain.config import get_settings
    from brain.embed.client import OllamaEmbedder
    from brain.graph.client import GraphClient
    from brain.graph.context import GraphContext
    from brain.resolve.build import BuildError, run_build
    from brain.resolve.runner import resolve_kinds

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc

    s = get_settings()
    try:
        with (
            GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client,
            OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim) as embedder,
        ):
            _, code = run_build(
                ctx=GraphContext(client),
                batches_dir=s.batches_dir,
                kinds=chosen,
                embedder=embedder,
                k=k,
                vector_evidence=vector_evidence,
                shards=shards,
                force=force,
                echo=typer.echo,
            )
    except (BuildError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"resolve build-batches: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@resolve_app.command("merge-decisions")
def resolve_merge_decisions(
    kinds: str = typer.Option("all", "--kinds", help="person | entity | all."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and report, merge nothing."),
) -> None:
    """Validate every NNN.out.json and apply only the `same` verdicts."""
    from brain.resolve.runner import ResolveError, resolve_from_settings, resolve_kinds

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc
    try:
        _, code = resolve_from_settings(kinds=chosen, tiers=[3], dry_run=dry_run, echo=typer.echo)
    except (ResolveError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"resolve merge-decisions: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@resolve_app.command("gold")
def resolve_gold(
    kinds: str = typer.Option("all", "--kinds", help="person | entity | all."),
) -> None:
    """Build data/eval/resolution_gold.jsonl — evaluation only, never a pipeline input."""
    from brain.config import get_settings
    from brain.resolve.gold import GoldError, run_gold
    from brain.resolve.runner import resolve_kinds

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc

    s = get_settings()
    try:
        _, code = run_gold(
            canonical_dir=s.canonical_dir,
            eval_dir=s.eval_dir,
            batches_dir=s.batches_dir,
            kinds=chosen,
            echo=typer.echo,
        )
    except (GoldError, OSError, ValueError) as exc:
        typer.echo(f"resolve gold: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@resolve_app.command("eval")
def resolve_eval(
    kinds: str = typer.Option("all", "--kinds", help="person | entity | all."),
) -> None:
    """Score the ledger against the gold: P/R/F1 per kind, per tier."""
    from brain.config import get_settings
    from brain.resolve.evaluate import run_eval
    from brain.resolve.runner import resolve_kinds

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc

    s = get_settings()
    try:
        _, code = run_eval(
            canonical_dir=s.canonical_dir,
            eval_dir=s.eval_dir,
            reports_dir=s.reports_dir,
            kinds=chosen,
            echo=typer.echo,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"resolve eval: {exc}", err=True)
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
