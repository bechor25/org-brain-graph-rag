"""`brain eval answers build|merge` — the two halves of mode A's answers (Plan 3 Task 3).

A typer sub-app for the same reason `cli_questions.py` is one: it is mounted from
`brain/cli.py` in a single line, so two agents growing `brain eval` touch one line each
instead of the same block. Module-level imports stay at typer only — `brain --help` must not
pay for neo4j and the retrieval stack to print an option's default.

Runnable directly (`python -m brain.eval.cli_answers build …`), which is how it was smoke
tested before the mount landed.
"""

from __future__ import annotations

from pathlib import Path

import typer

#: Mirrors `brain/eval/runs.py`; `tests/test_eval_answers_cli.py` asserts they are equal, so
#: the help text cannot drift from the sweep whose output it consumes.
DEFAULT_STRATEGIES = "s1,s1r,s2,s3,s4,s5,s6"
DEFAULT_SHARDS = 4
DEFAULT_BATCH_SIZE = 10

answers_app = typer.Typer(
    help="Mode-A answers: build the batches the answering agent reads, then merge them.",
    no_args_is_help=True,
)


def _rows(questions: str | None, settings) -> list[dict]:
    from brain.eval.gate import load_questions

    path = Path(questions) if questions else settings.eval_dir / "questions.jsonl"
    if not path.is_file():
        raise typer.BadParameter(
            f"{path} does not exist — run `brain eval questions merge` first.",
            param_hint="--questions",
        )
    rows = load_questions(path)
    if not rows:
        raise typer.BadParameter(f"{path} holds no questions", param_hint="--questions")
    return rows


@answers_app.command("build")
def build(
    strategies: str | None = typer.Option(
        None,
        "--strategies",
        metavar="LIST",
        help=f"Comma-separated. Default: {DEFAULT_STRATEGIES}.",
    ),
    shards: int = typer.Option(DEFAULT_SHARDS, "--shards", help="How many answering agents."),
    batch_size: int = typer.Option(
        DEFAULT_BATCH_SIZE,
        "--batch-size",
        help="Ceiling on cases per batch. The 40 KB budget usually closes a batch first: a "
        "packed 4k-token context is ~13 KB, so three cases is the common size.",
    ),
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    runs: str | None = typer.Option(
        None, "--runs", metavar="DIR", help="Default: data/eval/runs/fixed."
    ),
    force: bool = typer.Option(
        False, "--force", help="Rebuild even though a shard already has answers against it."
    ),
) -> None:
    """Turn every `ok` run file into a case for the answering agent [Plan 3 Task 3].

    One case = one question plus the context a strategy retrieved for it, and nothing else:
    mode A measures retrieval, so the agent gets no tools and no second context for the same
    question. Writes data/batches/answers/shard-NN/NNN.in.json.
    """
    from brain.config import get_settings
    from brain.eval import runs as runs_mod
    from brain.eval.answers_batches import AnswersError, run_build, summarize_build
    from brain.retrieve.report import head_sha

    settings = get_settings()
    rows = _rows(questions, settings)
    try:
        wanted = runs_mod.parse_strategies(strategies)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--strategies") from exc
    runs_dir = Path(runs) if runs else settings.eval_dir / "runs" / runs_mod.RUNS_DIRNAME

    try:
        manifest = run_build(
            rows=rows,
            strategies=wanted,
            runs_dir=runs_dir,
            batches_dir=settings.batches_dir,
            reports_dir=settings.reports_dir,
            shards=shards,
            batch_size=batch_size,
            force=force,
            sha=head_sha(),
            echo=typer.echo,
        )
    except (AnswersError, OSError) as exc:
        typer.echo(f"eval answers build: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(summarize_build(manifest))
    typer.echo(f"batches: {settings.batches_dir / 'answers'}")
    raise typer.Exit(code=1 if manifest["sizes"]["over_budget"] else 0)


@answers_app.command("merge")
def merge(
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    runs: str | None = typer.Option(
        None,
        "--runs",
        metavar="DIR",
        help="Default: data/eval/runs/fixed. Used only to re-check that each answer's "
        "context still matches the run file it was written from.",
    ),
    plan2: str | None = typer.Option(
        None,
        "--plan2",
        metavar="DIR",
        help="Mode-B answers to carry in as <qid>.agentic.json. Default: data/eval/plan2_answers.",
    ),
) -> None:
    """Validate the answer batches and write data/eval/answers/fixed/ [Plan 3 Task 3].

    A citation that names nothing in its own case's context is marked invalid and kept with
    the answer: "this retrieval's answers cite keys it never returned" is a measurement, not
    a parse error. Mode-B answers from Plan 2 Task 4 are carried in beside them.

    Exits 1 while a planned batch has no output, a batch failed validation, or a case was
    left unanswered.
    """
    from brain.config import get_settings
    from brain.eval.answers_batches import AnswersError, run_merge
    from brain.retrieve.report import head_sha

    settings = get_settings()
    _rows(questions, settings)  # the set must exist; the merge itself reads the batches
    runs_dir = Path(runs) if runs else settings.eval_dir / "runs" / "fixed"
    try:
        report = run_merge(
            batches_dir=settings.batches_dir,
            eval_dir=settings.eval_dir,
            reports_dir=settings.reports_dir,
            runs_dir=runs_dir,
            plan2_dir=Path(plan2) if plan2 else None,
            sha=head_sha(),
            echo=typer.echo,
        )
    except (AnswersError, OSError) as exc:
        typer.echo(f"eval answers merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"answers: {report['answers_dir']} · report: {settings.reports_dir}")
    raise typer.Exit(code=0 if report["complete"] else 1)


if __name__ == "__main__":  # pragma: no cover - the pre-mount smoke entry point
    answers_app()
