"""`brain eval questions build|merge` — the two halves of the question set (Plan 3 Task 1).

A typer app of its own rather than two more commands on `eval_app`, for the same reason
`brain/community/` and `brain/extract/` keep theirs: the sub-app is mounted in one line from
`brain/cli.py`, so two agents adding commands to the same `eval` group touch one line each
instead of the same block.

Runnable directly (`python -m brain.eval.cli_questions build …`), which is how it was
smoke-tested before the mount landed.
"""

from __future__ import annotations

from pathlib import Path

import typer

# Repeated here rather than imported: `brain --help` should not pay for neo4j, httpx and the
# whole retrieval stack to print an option's default. `test_eval_questions_cli.py` asserts
# these are the same numbers `brain/eval/questions.py` defines.
DEFAULT_NEW_TOTAL = 13
DEFAULT_SHARDS = 2
DEFAULT_HEBREW_MIN = 11
DEFAULT_SLACK = 0.30
DEFAULT_SPARES = 1
DEFAULT_SEED = 2026

questions_app = typer.Typer(
    help="Build the evaluation question set: sample paths, then merge what the forger wrote.",
    no_args_is_help=True,
)


def _competency_rows(path: Path | None) -> list[dict]:
    from brain.retrieve.competency import read as read_competency

    rows = read_competency(path)
    if not rows:
        raise typer.BadParameter(
            f"no competency questions at {path} — run `brain competency` first (Plan 2 built "
            "the 19 that the 13 new ones are stratified against)."
        )
    return rows


@questions_app.command("build")
def build(
    n: int = typer.Option(
        DEFAULT_NEW_TOTAL, "--n", help="How many NEW questions to plan for (the 19 are counted)."
    ),
    shards: int = typer.Option(DEFAULT_SHARDS, "--shards", help="How many forger agents."),
    hebrew_min: int = typer.Option(
        DEFAULT_HEBREW_MIN, "--hebrew-min", help="Hebrew questions in the FINAL set."
    ),
    slack: float = typer.Option(
        DEFAULT_SLACK, "--slack", help="Extra questions to ask for, as a fraction of --n."
    ),
    spares: int = typer.Option(
        DEFAULT_SPARES, "--spares", help="Extra paths per type the forger may substitute in."
    ),
    seed: int = typer.Option(
        DEFAULT_SEED, "--seed", help="Which paths the seeded pick takes from each pool."
    ),
    competency: str | None = typer.Option(
        None, "--competency", metavar="PATH", help="Default: data/eval/competency.jsonl."
    ),
    prefix: str = typer.Option("", "--prefix", help="Label namespace to sample from."),
    force: bool = typer.Option(
        False, "--force", help="Rebuild even if a shard already has answers against it."
    ),
) -> None:
    """Sample real graph paths and write question batches for `question-forger` [Plan 3]."""
    from brain.config import get_settings
    from brain.eval import paths as paths_mod
    from brain.eval.questions import run_build, summarize_build
    from brain.extract.build import BuildError
    from brain.retrieve.context import RetrieveContext

    settings = get_settings()
    rows = _competency_rows(Path(competency) if competency else None)
    try:
        with RetrieveContext.open(settings, prefix=prefix) as ctx:
            manifest = run_build(
                ctx=ctx,
                batches_dir=settings.batches_dir,
                reports_dir=settings.reports_dir,
                existing_rows=rows,
                new_total=n,
                shards=shards,
                hebrew_min=hebrew_min,
                slack=slack,
                spares=spares,
                seed=seed,
                force=force,
            )
    except (BuildError, paths_mod.PathError) as exc:
        typer.echo(f"eval questions build: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(summarize_build(manifest))
    typer.echo(f"batches: {settings.batches_dir / 'questions'}")
    raise typer.Exit(code=1 if manifest["sizes"]["over_budget"] else 0)


@questions_app.command("merge")
def merge(
    competency: str | None = typer.Option(
        None, "--competency", metavar="PATH", help="Default: data/eval/competency.jsonl."
    ),
    n: int = typer.Option(
        DEFAULT_NEW_TOTAL, "--n", help="The same --n the build used; it defines the quota."
    ),
    hebrew_min: int = typer.Option(DEFAULT_HEBREW_MIN, "--hebrew-min"),
    slack: float = typer.Option(DEFAULT_SLACK, "--slack"),
    prefix: str = typer.Option("", "--prefix", help="Label namespace to check evidence against."),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Skip the graph existence check. Every cited key is then reported as missing — "
        "for inspecting a merge without a database, never for a set you will measure on.",
    ),
) -> None:
    """Validate the forger's batches and write data/eval/questions.jsonl [Plan 3]."""
    from brain.config import get_settings
    from brain.eval import paths as paths_mod
    from brain.eval.questions import plan_demand
    from brain.eval.questions_report import MergeError, run_merge, summarize_merge
    from brain.retrieve.context import RetrieveContext

    settings = get_settings()
    rows = _competency_rows(Path(competency) if competency else None)
    demand = plan_demand(rows, new_total=n, hebrew_min=hebrew_min, slack=slack)
    try:
        truth = paths_mod.load_truth()
    except paths_mod.PathError as exc:
        typer.echo(f"eval questions merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    def _run(ctx: object | None) -> dict:
        return run_merge(
            ctx=ctx,  # type: ignore[arg-type]
            batches_dir=settings.batches_dir,
            reports_dir=settings.reports_dir,
            eval_dir=settings.eval_dir,
            competency_rows=rows,
            demand=demand,
            truth=truth,
        )

    try:
        if offline:
            report = _run(None)
        else:
            with RetrieveContext.open(settings, prefix=prefix) as ctx:
                report = _run(ctx)
    except MergeError as exc:
        typer.echo(f"eval questions merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(summarize_merge(report))
    raise typer.Exit(code=0 if report["complete"] else 1)


@questions_app.command("gold-competency")
def gold_competency(
    competency: str | None = typer.Option(
        None, "--competency", metavar="PATH", help="Default: data/eval/competency.jsonl."
    ),
    prefix: str = typer.Option("", "--prefix", help="Label namespace to derive from."),
) -> None:
    """Derive gold answers for the 19 competency questions from the graph [Plan 3].

    Deterministic and auditable: one fixed query per question, recorded as `gold_query`, run
    under READ routing. Writes data/eval/competency_gold.jsonl, which
    `brain eval questions merge` folds into data/eval/questions.jsonl. A question whose
    derivation comes back empty stays pending with the reason attached.
    """
    from brain.config import get_settings
    from brain.eval.gold_competency import run_gold, summarize_gold
    from brain.retrieve.context import RetrieveContext
    from brain.retrieve.types import RetrieveError

    settings = get_settings()
    rows = _competency_rows(Path(competency) if competency else None)
    try:
        with RetrieveContext.open(settings, prefix=prefix) as ctx:
            report = run_gold(
                ctx=ctx,
                competency_rows=rows,
                eval_dir=settings.eval_dir,
                reports_dir=settings.reports_dir,
            )
    except RetrieveError as exc:
        typer.echo(f"eval questions gold-competency: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(summarize_gold(report))
    # Exit 1 while any row is still pending: the planner owes it an answer, and a silent 0
    # would let a half-filled gold set through the Task 2 gate.
    raise typer.Exit(code=0 if report["totals"]["pending"] == 0 else 1)


if __name__ == "__main__":  # pragma: no cover - the pre-mount smoke entry point
    questions_app()
