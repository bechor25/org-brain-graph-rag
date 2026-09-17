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
        "all",
        "--source",
        help="Which source to run: a `name` from sources.yaml, or `all` for every enabled one.",
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
    from brain.harvest.auth import AuthError
    from brain.harvest.registry import RegistryError
    from brain.harvest.runner import resolve_sources, run_harvest

    try:
        sources = resolve_sources(source)
    except RegistryError as exc:
        raise typer.BadParameter(str(exc), param_hint="--source") from exc

    since_date: _date | None = None
    if since:
        try:
            since_date = _date.fromisoformat(since)
        except ValueError as exc:
            raise typer.BadParameter(f"{since!r} is not YYYY-MM-DD", param_hint="--since") from exc

    settings = get_settings()
    try:
        _, code = run_harvest(
            sources,
            raw_dir=settings.raw_dir,
            reports_dir=settings.reports_dir,
            since=since_date,
            echo=typer.echo,
        )
    except (RegistryError, AuthError) as exc:
        # A misconfigured source or a broken token: say what to fix, not a traceback.
        typer.echo(f"harvest: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def canon(
    source: str = typer.Option(
        "all",
        "--source",
        help="Which source to map: a `name` from sources.yaml, or `all` for every enabled one.",
    ),
) -> None:
    """Normalize raw data into the canonical model (data/canonical/*.jsonl) [Plan 1]."""
    from brain.canon.runner import CanonError, resolve_sources, run_canon
    from brain.config import get_settings
    from brain.harvest.registry import RegistryError

    try:
        sources = resolve_sources(source)
    except RegistryError as exc:
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
    stamp_synthetic: bool = typer.Option(
        False,
        "--stamp-synthetic",
        help="Backfill only: re-derive Chunk.synthetic from each chunk's parent and "
        "Entity.synthetic from its evidence chunks, then exit. Writes one boolean per "
        "node, deletes nothing, needs no embedder, and a rerun stamps 0 (ADR-0005 §5).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --stamp-synthetic: report what would be stamped and write nothing. "
        "Exits 1 while anything is still unstamped.",
    ),
) -> None:
    """Chunk texts, embed with local bge-m3, create :Chunk nodes + vector index [Plan 1]."""
    from brain.chunk.runner import chunk_from_settings, resolve_kinds
    from brain.config import get_settings

    if dry_run and not stamp_synthetic:
        raise typer.BadParameter(
            "--dry-run only applies to --stamp-synthetic", param_hint="--dry-run"
        )
    if stamp_synthetic:
        from brain.chunk.synthetic import stamp_from_settings

        try:
            _, code = stamp_from_settings(
                get_settings().reports_dir, apply=not dry_run, echo=typer.echo
            )
        except (OSError, ValueError, RuntimeError) as exc:
            typer.echo(f"chunk: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        raise typer.Exit(code=code)

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
        _, code = merge_from_settings(
            settings.batches_dir,
            settings.reports_dir,
            # A re-merge must land on the entities `brain resolve` kept, not recreate the
            # ones it merged away — the ledger is what says which is which.
            canonical_dir=settings.canonical_dir,
            echo=typer.echo,
        )
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


@resolve_app.command("reset")
def resolve_reset(
    kinds: str = typer.Option("person", "--kinds", help="person | entity (one at a time)."),
    yes: bool = typer.Option(
        False, "--yes", help="Actually do it. Without this the command only says what it would do."
    ),
) -> None:
    """Drop the resolved layer so `brain load` can rebuild it — there is no unmerge."""
    from brain.config import get_settings
    from brain.graph.client import GraphClient
    from brain.graph.context import GraphContext
    from brain.resolve.reset import ResetError, run_reset
    from brain.resolve.runner import resolve_kinds

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc
    if len(chosen) != 1:
        raise typer.BadParameter("pick one kind at a time", param_hint="--kinds")

    s = get_settings()
    try:
        with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
            _, code = run_reset(
                GraphContext(client),
                canonical_dir=s.canonical_dir,
                kind=chosen[0],
                confirmed=yes,
                echo=typer.echo,
            )
    except (ResetError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"resolve reset: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@resolve_app.command("sample")
def resolve_sample(
    n: int = typer.Option(40, "--n", min=1, help="How many merged groups to print."),
    seed: int = typer.Option(7, "--seed", help="Same seed, same sample — so it can be re-read."),
    kinds: str = typer.Option("person", "--kinds", help="person | entity (one at a time)."),
) -> None:
    """Print merged groups the gold cannot grade, for a human precision check."""
    from brain.config import get_settings
    from brain.graph.client import GraphClient
    from brain.graph.context import GraphContext
    from brain.resolve.runner import resolve_kinds
    from brain.resolve.sample import run_sample

    try:
        chosen = resolve_kinds(kinds)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--kinds") from exc
    if len(chosen) != 1:
        raise typer.BadParameter("pick one kind at a time", param_hint="--kinds")

    s = get_settings()
    try:
        with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
            _, code = run_sample(
                GraphContext(client),
                canonical_dir=s.canonical_dir,
                eval_dir=s.eval_dir,
                kind=chosen[0],
                n=n,
                seed=seed,
                echo=typer.echo,
            )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"resolve sample: {exc}", err=True)
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


communities_app = typer.Typer(
    help="GDS Leiden communities: build the partition, pack batches for the "
    "community-summarizer agents, merge the reports they wrote [Plan 1]",
    no_args_is_help=True,
)
app.add_typer(communities_app, name="communities")


def _level_indices(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        return [int(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            f"{value!r} is not a comma-separated list of integers", param_hint="--level-indices"
        ) from exc


@communities_app.command("build")
def communities_build(
    levels: int = typer.Option(2, "--levels", min=1, help="How many Leiden levels to keep."),
    level_indices: str | None = typer.Option(
        None,
        "--level-indices",
        metavar="FINE,COARSE",
        help="Name the GDS levels explicitly, fine first (e.g. `1,4`). Overrides --levels.",
    ),
    min_size: int = typer.Option(
        5, "--min-size", min=1, help="Below this a community is `misc`: placed, never summarised."
    ),
    seed: int = typer.Option(42, "--seed", help="Leiden randomSeed; runs at concurrency 1."),
    max_levels: int = typer.Option(10, "--max-levels", min=1, help="Leiden maxLevels."),
    gamma: float = typer.Option(1.0, "--gamma", help="Leiden resolution; below 1 = coarser."),
    include_synthetic: bool = typer.Option(
        False, "--include-synthetic", help="Project synthetic=true nodes too (off by default)."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Project, run Leiden, report, drop the projection — and write nothing to Neo4j.",
    ),
) -> None:
    """Project the graph, run hierarchical Leiden, write Community + IN_COMMUNITY."""
    from brain.community.build import BuildError
    from brain.community.projection import ProjectionError
    from brain.community.runner import build_from_settings
    from brain.config import get_settings

    settings = get_settings()
    try:
        _, code = build_from_settings(
            settings.reports_dir,
            levels=levels,
            level_indices=_level_indices(level_indices),
            min_size=min_size,
            seed=seed,
            max_levels=max_levels,
            gamma=gamma,
            include_synthetic=include_synthetic,
            dry_run=dry_run,
            echo=typer.echo,
        )
    except (BuildError, ProjectionError, OSError, ValueError) as exc:
        typer.echo(f"communities build: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@communities_app.command("batches")
def communities_batches(
    shards: int = typer.Option(2, "--shards", min=1, help="How many agents work in parallel."),
    top_members: int = typer.Option(
        25, "--top-members", min=1, help="Members per community, by projected degree."
    ),
    evidence: int = typer.Option(
        8, "--evidence", min=0, help="Evidence chunks per community, most connected first."
    ),
    evidence_chars: int = typer.Option(
        600, "--evidence-chars", min=100, help="Characters kept from each evidence chunk."
    ),
    force: bool = typer.Option(
        False, "--force", help="Repack even though a shard reports work against the old inputs."
    ),
    resummarize: bool = typer.Option(
        False,
        "--resummarize",
        help="Include communities that already carry a report (by default they are skipped: "
        "a community whose members did not change is not summarised twice).",
    ),
) -> None:
    """Write data/batches/communities/<shard>/NNN.in.json for the summarizer agents."""
    from brain.community.build import BuildError
    from brain.community.runner import batches_from_settings
    from brain.config import get_settings

    settings = get_settings()
    try:
        _, code = batches_from_settings(
            settings.batches_dir,
            settings.reports_dir,
            shards=shards,
            top_members=top_members,
            evidence=evidence,
            evidence_chars=evidence_chars,
            force=force,
            resummarize=resummarize,
            echo=typer.echo,
        )
    except (BuildError, OSError, ValueError) as exc:
        typer.echo(f"communities batches: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@communities_app.command("merge")
def communities_merge() -> None:
    """Validate every NNN.out.json, write the reports with provenance, embed them."""
    from brain.community.merge import MergeError
    from brain.community.runner import merge_from_settings
    from brain.config import get_settings

    settings = get_settings()
    try:
        _, code = merge_from_settings(settings.batches_dir, settings.reports_dir, echo=typer.echo)
    except (MergeError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"communities merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def reset(
    graph: bool = typer.Option(
        False, "--graph", help="Delete every node; keep constraints/indexes."
    ),
    data: bool = typer.Option(
        False,
        "--data",
        help="Empty data/raw, canonical, batches, reports, eval. Never data/fixtures.",
    ),
    synthetic: bool = typer.Option(
        False,
        "--synthetic",
        help="Remove only the synthetic Xray/ADO layer: synthetic=true records and nodes, "
        "the merge ledger, the truth file and the synthetic batches. The real corpus stays.",
    ),
    all_: bool = typer.Option(False, "--all", help="--graph and --data together."),
    yes: bool = typer.Option(
        False, "--yes", help="Actually delete. Without it the command only prints the manifest."
    ),
) -> None:
    """Delete the POC's data so real systems can be connected (ADR-0005) [Plan 1]."""
    from brain.config import get_settings
    from brain.reset import ResetError, run_reset

    if all_:
        graph = data = True
    if not (graph or data or synthetic):
        raise typer.BadParameter(
            "pick a scope: --graph, --data, --synthetic or --all", param_hint="brain reset"
        )

    s = get_settings()
    needs_graph = graph or synthetic
    client = None
    try:
        if needs_graph:
            from brain.graph.client import GraphClient
            from brain.graph.context import GraphContext

            client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
            ctx = GraphContext(client)
        else:
            ctx = None
        _, code = run_reset(
            data_dir=s.data_dir,
            canonical_dir=s.canonical_dir,
            batches_dir=s.batches_dir,
            reports_dir=s.reports_dir,
            ctx=ctx,
            graph=graph,
            data=data,
            synthetic=synthetic,
            confirmed=yes,
            echo=typer.echo,
        )
    except (ResetError, OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"reset: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        if client is not None:
            client.close()
    raise typer.Exit(code=code)


@app.command()
def index(
    check_gate: bool = typer.Option(
        False,
        "--check-gate",
        help="Also print every Plan 1 exit criterion with its measured value and "
        "PASS/FAIL, and exit 1 if any of them fails.",
    ),
    smoke: bool = typer.Option(
        False,
        "--smoke",
        help="Run `make smoke` and record the result in the report, so the gate's "
        "smoke criterion has a measured value instead of an assertion. Takes minutes.",
    ),
    docs_dir: str = typer.Option(
        "docs",
        "--docs-dir",
        metavar="PATH",
        help="Where the lessons, progress.md and the generated census document live.",
    ),
) -> None:
    """Create the final indexes, write the graph census and check the Plan 1 gate [Plan 1]."""
    from brain.config import get_settings
    from brain.index.runner import index_from_settings

    settings = get_settings()
    try:
        _, code = index_from_settings(
            settings,
            docs_dir=Path(docs_dir),
            check_gate=check_gate,
            smoke=smoke,
            echo=typer.echo,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"index: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


# ----------------------------------------------------------------- retrieval (Plan 2)


@app.command()
def ask(
    question: str = typer.Argument(..., help="The question, in English or Hebrew."),
    strategy: str = typer.Option(
        "auto",
        "--strategy",
        help="auto (deterministic router) | s1 hybrid | s2 graph-vector | s3 entity-local "
        "| s6 temporal | lookup | impact.",
    ),
    k: int = typer.Option(10, "--k", help="How many items to return before packing."),
    hops: int = typer.Option(1, "--hops", help="S2 only: neighbourhood depth (1-2)."),
    depth: int = typer.Option(2, "--depth", help="S3/impact only: traversal depth (1-2)."),
    mode: str = typer.Option("hybrid", "--mode", help="S1 only: hybrid | vector | fulltext."),
    rerank: bool = typer.Option(
        False, "--rerank", help="Cross-encoder rerank if the local model is installed."
    ),
    include_synthetic: bool = typer.Option(
        True, "--synthetic/--no-synthetic", help="Include the synthetic Xray/ADO layer."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the raw Result envelope."),
) -> None:
    """Ask the graph a question and print the cited answer [Plan 2]."""
    import json as _json

    from brain.config import get_settings
    from brain.retrieve.context import RetrieveContext
    from brain.retrieve.runner import ask as run_ask
    from brain.retrieve.runner import render
    from brain.retrieve.types import RetrieveError

    settings = get_settings()
    try:
        with RetrieveContext.open(settings, include_synthetic=include_synthetic) as ctx:
            result = run_ask(
                ctx,
                question,
                strategy=strategy,
                k=k,
                hops=hops,
                depth=depth,
                rerank=rerank,
                mode=mode,
                log_mode="cli",
            )
    except RetrieveError as exc:
        typer.echo(f"ask: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(_json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        typer.echo(render(result, question))
    raise typer.Exit(code=0 if result.items else 3)


@app.command()
def competency(
    rebuild: bool = typer.Option(
        True,
        "--rebuild/--reuse",
        help="Re-select the anchors from the graph, or reuse data/eval/competency.jsonl.",
    ),
    repeats: int = typer.Option(3, "--repeats", help="Latency samples per question."),
) -> None:
    """Build the competency questions from the graph and measure retrieval on them [Plan 2]."""
    from brain.config import get_settings
    from brain.retrieve.context import RetrieveContext
    from brain.retrieve.report import run as run_report

    settings = get_settings()
    with RetrieveContext.open(settings) as ctx:
        report, path = run_report(ctx, rebuild_questions=rebuild, repeats=repeats)
    summary = report["summary"]
    typer.echo(f"questions: {summary['questions']} ({summary['evidence_questions']} with evidence)")
    typer.echo(
        f"valid provenance: {summary['with_valid_provenance']}/{summary['evidence_questions']}"
        f" · invalid chunk ids: {summary['invalid_chunk_ids']}"
    )
    for name, stats in report["latency"].items():
        typer.echo(f"  {name:<12} p50 {stats['p50_ms']:>5} ms   p90 {stats['p90_ms']:>5} ms")
    for check in report["checks"]:
        typer.echo(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    typer.echo(f"report: {path}")
    raise typer.Exit(code=0 if all(c["ok"] for c in report["checks"]) else 1)


# ------------------------------------------------------------------- MCP server (Plan 2)


@app.command()
def serve(
    stdio: bool = typer.Option(
        False, "--stdio", help="Speak MCP over stdin/stdout. What `.mcp.json` launches."
    ),
    http: bool = typer.Option(
        False, "--http", help="Serve streamable HTTP at /mcp (and /healthz)."
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind address."),
    port: int = typer.Option(8765, "--port", help="HTTP port."),
    check: bool = typer.Option(
        False,
        "--check",
        help="Round-trip every tool over stdio, then write the `mcp` and `global` sections "
        "of data/reports/retrieve.json.",
    ),
) -> None:
    """Run the MCP server that exposes the retrieval library to an asking agent [Plan 2].

    `--stdio` is the transport Claude Code uses; `--http` is the one `docker compose` runs.
    Exactly one of them, unless `--check`, which starts its own server and measures it.
    """
    from brain.mcp.server import serve_http, serve_stdio

    if check:
        from brain.mcp.report import run as run_mcp_report

        report, path = run_mcp_report(echo=typer.echo)
        typer.echo(f"report: {path}")
        raise typer.Exit(code=0 if all(c["ok"] for c in report["mcp"]["checks"]) else 1)
    if stdio == http:
        typer.echo("serve: choose exactly one of --stdio or --http (or use --check)", err=True)
        raise typer.Exit(code=2)
    if stdio:
        # Nothing may be printed on stdout: it is the protocol channel.
        serve_stdio()
    else:
        typer.echo(f"brain MCP on http://{host}:{port}/mcp  (health: /healthz)", err=True)
        serve_http(host=host, port=port)


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
