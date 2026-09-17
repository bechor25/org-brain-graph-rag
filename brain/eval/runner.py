"""The two `brain eval` commands, as functions — so the CLI stays argument parsing only.

`run_cite_check` takes its `verify` callable as an argument. That is what lets `make check`
run the whole pipeline — parser, regex, report writer — against a fake graph, and it is also
what keeps the default honest: with nothing to check, `graph_verify` never opens a driver,
so "no answers yet" is a report and an exit code rather than a connection error.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.eval import gate as gate_mod
from brain.eval import render as render_mod
from brain.eval.answers import read_answers, write_template
from brain.eval.citations import Citation
from brain.eval.verify import Verdict, graph_verify

REPORT_NAME = "plan2_gate.json"
ANSWERS_NAME = "plan2_answers"
DOCUMENT_NAME = "plan2-first-questions.md"


def run_cite_check(
    *,
    answers_dir: Path,
    questions_path: Path,
    report_path: Path,
    verify: Callable[[list[Citation]], dict[str, Verdict]] | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Check every citation in every answer file against the graph and write the report."""
    answers_dir, questions_path, report_path = map(Path, (answers_dir, questions_path, report_path))
    questions = gate_mod.load_questions(questions_path)
    # The layout contract lives with the parser that enforces it: refreshing it here means
    # the analyst's directory can never hold a README describing an older format.
    write_template(answers_dir)
    answers = read_answers(answers_dir)

    report = gate_mod.build(
        questions,
        answers,
        verify or graph_verify,
        answers_dir=answers_dir,
        questions_path=questions_path,
        report_path=report_path,
        previous=gate_mod.read_previous(report_path),
    )
    gate_mod.write(report, report_path)
    for line in gate_mod.summary_lines(report):
        echo(line)
    echo(f"report: {report_path}")
    return report, gate_mod.exit_code(report)


def run_gate_report(*, report_path: Path, out_path: Path) -> tuple[Path, dict[str, Any]]:
    """Render the Hebrew page from the report, keeping the planner's paragraph."""
    report_path, out_path = Path(report_path), Path(out_path)
    report = gate_mod.read_previous(report_path)
    if not report.get("questions"):
        raise FileNotFoundError(
            f"{report_path} holds no gate report — run `brain eval cite-check` first"
        )
    previous = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_mod.render(report, previous=previous), encoding="utf-8")
    return out_path, report
