"""`brain eval judge build|merge|sample` — the blind judge (Plan 3 Task 3, spec §5.4).

A typer sub-app mounted from `brain/cli.py` in one line, import-light at module level for
the same reason the other eval sub-apps are.

`sample` is the only command here that writes into `docs/`: the planner's 10% spot-check
list, generated from `data/reports/eval_answers.json` so that no number in it was typed.
"""

from __future__ import annotations

from pathlib import Path

import typer

#: Mirrors `brain/eval/judge_batches.py`; asserted equal in `tests/test_eval_judge_cli.py`.
DEFAULT_SHARDS = 2
DEFAULT_BATCH_SIZE = 10
DEFAULT_OVERLAP = 0.20
DEFAULT_SEED = 2026
DEFAULT_BASELINE = "s1r"
DEFAULT_PAIR_WITH = "s2,s3,s4,s5,s6"
DEFAULT_SAMPLE = "10%"
DOCS_REPORT_DIR = Path("docs/report")

judge_app = typer.Typer(
    help="Blind rubric judging of the answers: build batches, merge scores, sample for review.",
    no_args_is_help=True,
)


def _rows(questions: str | None, settings) -> list[dict]:
    from brain.eval.gate import load_questions

    path = Path(questions) if questions else settings.eval_dir / "questions.jsonl"
    if not path.is_file():
        raise typer.BadParameter(f"{path} does not exist", param_hint="--questions")
    return load_questions(path)


@judge_app.command("build")
def build(
    shards: int = typer.Option(DEFAULT_SHARDS, "--shards", help="How many judges."),
    batch_size: int = typer.Option(
        DEFAULT_BATCH_SIZE, "--batch-size", help="Ceiling on cases per batch; 40 KB usually wins."
    ),
    overlap: float = typer.Option(
        DEFAULT_OVERLAP,
        "--overlap",
        help="Share of cases copied into the next shard, so the two judges can be compared.",
    ),
    seed: int = typer.Option(
        DEFAULT_SEED, "--seed", help="Drives the labels and the pairwise A/B order."
    ),
    baseline: str = typer.Option(
        DEFAULT_BASELINE, "--baseline", help="The strategy every pairwise case is built around."
    ),
    pair_with: str = typer.Option(
        DEFAULT_PAIR_WITH, "--pair-with", help="Comma-separated challengers. Empty = no pairs."
    ),
    strategies: str | None = typer.Option(
        None, "--strategies", metavar="LIST", help="Judge only these. Default: everything merged."
    ),
    pair_context: bool = typer.Option(
        False,
        "--pair-context/--no-pair-context",
        help="Carry both retrieved contexts into each pairwise case. Off by default: "
        "faithfulness is scored on the single cases, and two contexts per case would cost "
        "roughly one pairwise case per batch.",
    ),
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    runs: str | None = typer.Option(
        None, "--runs", metavar="DIR", help="Default: data/eval/runs/fixed."
    ),
    force: bool = typer.Option(
        False, "--force", help="Rebuild even though a shard already has judgments against it."
    ),
) -> None:
    """Write the blind judge batches and data/eval/blind_map.json [Plan 3 Task 3].

    Every case is addressed by a random label; the map back to a strategy is written outside
    the batch tree and never copied into it. Pairwise cases put the baseline and one graph
    strategy in an order drawn per case, and 20% of the cases go to both judges so their
    agreement can be measured.

    Exits 1 when a batch came out over the 40 KB budget.
    """
    from brain.config import get_settings
    from brain.eval import judge_batches as judge_mod
    from brain.eval.answers_batches import read_merged
    from brain.eval.blind import BlindError
    from brain.retrieve.report import head_sha

    settings = get_settings()
    rows = _rows(questions, settings)
    runs_dir = Path(runs) if runs else settings.eval_dir / "runs" / "fixed"
    answers = judge_mod.filter_answers(
        read_merged(settings.eval_dir),
        [s.strip() for s in strategies.split(",")] if strategies else None,
    )
    contexts, drift = judge_mod.contexts_for(answers, runs_dir)
    for entry in drift:
        typer.echo(f"skipping {entry['case_id']}: {entry['why']}")
    challengers = tuple(s.strip() for s in pair_with.split(",") if s.strip())

    try:
        manifest = judge_mod.run_build(
            answers=answers,
            rows=rows,
            contexts=contexts,
            batches_dir=settings.batches_dir,
            eval_dir=settings.eval_dir,
            reports_dir=settings.reports_dir,
            shards=shards,
            batch_size=batch_size,
            overlap=overlap,
            seed=seed,
            baseline=baseline,
            pair_with=challengers,
            pair_context=pair_context,
            force=force,
            sha=head_sha(),
            echo=typer.echo,
        )
    except (judge_mod.JudgeError, BlindError, OSError) as exc:
        typer.echo(f"eval judge build: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(judge_mod.summarize_build(manifest))
    typer.echo(f"batches: {settings.batches_dir / 'judge'}")
    raise typer.Exit(code=1 if manifest["sizes"]["over_budget"] else 0)


@judge_app.command("merge")
def merge(
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Skip the graph half of the citation cross-check. The context half still runs; "
        "the report says which checks were performed.",
    ),
    prefix: str = typer.Option("", "--prefix", help="Label namespace to verify citations in."),
    log: str | None = typer.Option(
        None,
        "--log",
        metavar="PATH",
        help="The retrieval trace to read mode B's snapshot from "
        "(default: data/logs/retrieval.jsonl).",
    ),
) -> None:
    """De-blind the judgments and write data/reports/eval_answers.json [Plan 3 Task 3].

    Computes the four rubric metrics per strategy × question type, the pairwise win rates
    against the baseline, inter-judge agreement on the overlapping cases, and a deterministic
    citation check that is compared with the judge's citation score.

    Exits 1 while a case has no judgment, a batch has no output, or a judgment was rejected.
    """
    from brain.config import get_settings
    from brain.eval import judge_report as report_mod
    from brain.eval.judge_batches import JudgeError
    from brain.retrieve.report import head_sha

    settings = get_settings()
    rows = _rows(questions, settings)
    sha = head_sha()
    from brain.retrieve.log import DEFAULT_LOG

    log_path = Path(log) if log else DEFAULT_LOG

    def _run(verify) -> dict:
        return report_mod.run_merge(
            batches_dir=settings.batches_dir,
            eval_dir=settings.eval_dir,
            reports_dir=settings.reports_dir,
            rows=rows,
            verify=verify,
            log_path=log_path,
            sha=sha,
            echo=typer.echo,
        )

    try:
        if offline:
            report = _run(None)
        else:
            from brain.eval.verify import GraphVerifier
            from brain.retrieve.context import RetrieveContext

            with RetrieveContext.open(settings, prefix=prefix) as ctx:
                report = _run(GraphVerifier(ctx))
    except (JudgeError, OSError) as exc:
        typer.echo(f"eval judge merge: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"report: {settings.reports_dir / 'eval_answers.json'}")
    raise typer.Exit(code=report_mod.exit_code(report))


@judge_app.command("sample")
def sample(
    n: str = typer.Option(
        DEFAULT_SAMPLE,
        "--n",
        help="Share of judged cases to list. `10%` = `10` = `0.1`; a bare number of 1 or "
        "more is read as a percentage, below 1 as a fraction.",
    ),
    seed: int = typer.Option(DEFAULT_SEED, "--seed", help="Which cases the stratified pick takes."),
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    out: str | None = typer.Option(
        None,
        "--out",
        metavar="DIR",
        help="Default: docs/report (the file is eval-judge-sample.md).",
    ),
) -> None:
    """Write the planner's spot-check list of judgments [Plan 3 Task 3].

    A stratified sample across the strategies, de-blinded, with the answer, the scores and
    the judge's justification side by side — generated from the report, so every number in
    it can be traced back to a JSON file.
    """
    from brain.config import get_settings
    from brain.eval import judge_report as report_mod
    from brain.eval.judge_batches import JudgeError
    from brain.retrieve.report import head_sha

    settings = get_settings()
    try:
        fraction = report_mod.parse_fraction(n)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--n") from exc
    rows = _rows(questions, settings)
    try:
        path, count = report_mod.run_sample(
            reports_dir=settings.reports_dir,
            eval_dir=settings.eval_dir,
            docs_dir=Path(out) if out else DOCS_REPORT_DIR,
            rows=rows,
            fraction=fraction,
            seed=seed,
            sha=head_sha(),
        )
    except (JudgeError, OSError) as exc:
        typer.echo(f"eval judge sample: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{count} case(s) sampled ({fraction:.0%}) -> {path}")


if __name__ == "__main__":  # pragma: no cover - the pre-mount smoke entry point
    judge_app()
