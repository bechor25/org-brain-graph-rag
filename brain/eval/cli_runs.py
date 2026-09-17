"""`brain eval run` — the fixed-strategy sweep and the layer-2 report (Plan 3 Task 2).

One command rather than a sub-app, because there is one verb: run the question set. `--mode`
is where mode B (Task 3, the agentic run) will attach, and it refuses by name today rather
than pretending `fixed` is the only mode that exists.

Registered from `brain/cli.py` in one line, and import-light at module level for the same
reason `cli_questions.py` is: `brain --help` must not pay for neo4j, httpx and the retrieval
stack to print an option's default.
"""

from __future__ import annotations

from pathlib import Path

import typer

#: Mirrors `brain/eval/runs.py`; asserted equal in `tests/test_eval_runs_cli.py` so the help
#: text can never drift from the sweep it describes.
DEFAULT_STRATEGIES = "s1,s1r,s2,s3,s4,s5,s6"
DEFAULT_K = 10
MODES = ("fixed", "agentic")


def eval_run(
    mode: str = typer.Option(
        "fixed", "--mode", help="`fixed` = every strategy on every question (Plan 3 Task 2)."
    ),
    strategies: str | None = typer.Option(
        None,
        "--strategies",
        metavar="LIST",
        help=f"Comma-separated. Default: {DEFAULT_STRATEGIES}.",
    ),
    questions: str | None = typer.Option(
        None, "--questions", metavar="PATH", help="Default: data/eval/questions.jsonl."
    ),
    only_complete: bool = typer.Option(
        False,
        "--only-complete",
        help="Refuse to run until the question set is complete (32 merged rows). For the "
        "measurement that goes in the report; leave it off to rehearse on what exists.",
    ),
    k: int = typer.Option(DEFAULT_K, "--k", help="Items each strategy is asked for."),
    force: bool = typer.Option(
        False, "--force", help="Overwrite a run file whose result changed. Off: report and keep."
    ),
    s5_scope: str = typer.Option(
        "thematic",
        "--s5-scope",
        help="`thematic` = S5 runs on thematic/`global` questions only and is n/a elsewhere; "
        "`all` = run it everywhere and let the matrix show what it is worth.",
    ),
    limit: int = typer.Option(0, "--limit", help="First N questions only, for a rehearsal."),
    out: str | None = typer.Option(
        None, "--out", metavar="DIR", help="Default: data/eval/runs (files land in <DIR>/fixed)."
    ),
    report: str | None = typer.Option(
        None, "--report", metavar="PATH", help="Default: data/reports/eval_retrieval.json."
    ),
    prefix: str = typer.Option("", "--prefix", help="Label namespace to retrieve from."),
) -> None:
    """Run every strategy on every question under one context budget, and measure [Plan 3].

    Writes one file per (question, strategy) to data/eval/runs/fixed/, then computes layer 2
    — recall of `gold_evidence`, context precision, latency, tokens, Cypher counts — into
    data/reports/eval_retrieval.json and prints the strategy × type matrix.

    Exits 1 when a pair has no run file, or when a rerun produced a different result that
    `--force` was not asked to accept.
    """
    from brain.config import get_settings
    from brain.eval import runs as runs_mod
    from brain.eval.gate import load_questions
    from brain.retrieve.context import RetrieveContext
    from brain.retrieve.report import head_sha
    from brain.retrieve.runner import ask
    from brain.retrieve.text2cypher import select_example
    from brain.retrieve.types import RetrieveError

    if mode != "fixed":
        hint = "mode B is Plan 3 Task 3" if mode in MODES else f"expected one of {MODES}"
        typer.echo(f"eval run: --mode {mode} is not implemented here ({hint})", err=True)
        raise typer.Exit(code=2)
    if s5_scope not in ("thematic", "all"):
        raise typer.BadParameter("must be `thematic` or `all`", param_hint="--s5-scope")

    settings = get_settings()
    questions_path = Path(questions) if questions else settings.eval_dir / "questions.jsonl"
    out_dir = Path(out) if out else settings.eval_dir / "runs"
    report_path = Path(report) if report else settings.reports_dir / runs_mod.REPORT_NAME
    if not questions_path.is_file():
        raise typer.BadParameter(f"{questions_path} does not exist", param_hint="--questions")

    try:
        wanted = runs_mod.parse_strategies(strategies)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--strategies") from exc

    rows = load_questions(questions_path)
    complete = _questions_complete(settings.reports_dir / "eval_questions.json")
    if only_complete and not complete:
        typer.echo(
            "eval run: the question set is not complete — `eval_questions.json` -> "
            "merge.complete is false. Run `brain eval questions merge` first, or drop "
            "--only-complete to rehearse on the rows that exist.",
            err=True,
        )
        raise typer.Exit(code=2)
    if limit > 0:
        rows = rows[:limit]
    if not rows:
        typer.echo(f"eval run: {questions_path} holds no questions", err=True)
        raise typer.Exit(code=2)

    truth = _truth(typer.echo)
    sha = head_sha()
    typer.echo(
        f"{len(rows)} question(s) × {len(wanted)} strategies = {len(rows) * len(wanted)} runs "
        f"· budget 4k tokens · synthetic included · baseline {runs_mod.BASELINE}"
    )

    try:
        with RetrieveContext.open(settings, prefix=prefix, include_synthetic=True) as ctx:
            summary = runs_mod.run_all(
                ctx,
                rows,
                strategies=wanted,
                out_dir=out_dir,
                ask=ask,
                select_example=select_example,
                k=k,
                s5_scope=s5_scope,
                force=force,
                sha=sha,
                echo=typer.echo,
            )
    except (RetrieveError, runs_mod.RunError, OSError) as exc:
        typer.echo(f"eval run: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    built = runs_mod.build_report(
        summary["records"],
        rows,
        strategies=wanted,
        questions_path=questions_path,
        questions_complete=complete,
        questions_total=len(rows),
        truth=truth,
        summary=summary,
        out_dir=out_dir,
        sha=sha,
    )
    runs_mod.write_report(built, report_path)
    typer.echo("")
    for line in runs_mod.summary_lines(built):
        typer.echo(line)
    typer.echo(f"runs: {out_dir / runs_mod.RUNS_DIRNAME} · report: {report_path}")
    raise typer.Exit(code=runs_mod.exit_code(built))


def _questions_complete(path: Path) -> bool:
    """`merge.complete` from the Task 1 report — absent reads as "not complete"."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    merge = data.get("merge") if isinstance(data, dict) else None
    return bool(isinstance(merge, dict) and merge.get("complete"))


def _truth(echo) -> dict:
    """`synthetic_truth.json`, or an empty map with a warning — `truth:` gold needs it."""
    from brain.eval import paths as paths_mod

    try:
        return paths_mod.load_truth()
    except paths_mod.PathError as exc:
        echo(f"eval run: {exc} — `truth:` gold references will count as unresolved")
        return {}
