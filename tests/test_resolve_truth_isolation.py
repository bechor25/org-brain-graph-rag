"""The rule that makes the P/R in `data/reports/resolve.json` worth reading.

`data/canonical/synthetic_truth.json` says which invented identity belongs to which real
person. A resolver that read it would score 1.0 and mean nothing. Only
`brain/resolve/gold.py` — which builds the evaluation set — may name that file, and the
run itself must never open it. This test is the enforcement, not a convention.

Docstrings are exempt on purpose: the check is on string *values the code could open*, so
explaining the rule in prose does not break it, and `Path(TRUTH)` in any other module does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from brain.resolve.gold import TRUTH_NAME

PACKAGE = Path("brain/resolve")
#: The only module allowed to read ground truth.
GOLD_MODULE = "gold.py"
#: Modules on the measurement side. They may reach the gold *set*; only `gold.py` may
#: reach the truth the gold set was built from.
EVALUATION = {GOLD_MODULE, "evaluate.py"}
#: Everything `brain resolve` runs through on the way to a merge.
PIPELINE_MODULES = sorted(p.name for p in PACKAGE.glob("*.py") if p.name != GOLD_MODULE)


def string_constants(path: Path) -> list[str]:
    """Every string literal in the module that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
    return found


def test_the_package_has_modules_to_check():
    assert PIPELINE_MODULES, "brain/resolve/ has no modules — this guard would pass vacuously"
    assert (PACKAGE / GOLD_MODULE).is_file()


@pytest.mark.parametrize("name", PIPELINE_MODULES)
def test_no_module_but_gold_can_name_the_truth_file(name):
    offending = [s for s in string_constants(PACKAGE / name) if TRUTH_NAME in s]
    assert not offending, (
        f"brain/resolve/{name} holds the literal {TRUTH_NAME}: {offending}. Ground truth is "
        "for evaluation only (brief 08, inputs) — resolution that reads it grades itself."
    )


@pytest.mark.parametrize("name", sorted(set(PIPELINE_MODULES) - EVALUATION))
def test_no_pipeline_module_imports_the_gold_builder(name):
    """Importing `gold` would be a back door to the truth file one refactor away."""
    assert "brain.resolve.gold" not in imports(PACKAGE / name), (
        f"brain/resolve/{name} imports the gold builder, which reads {TRUTH_NAME}."
    )


def test_gold_is_the_module_that_does_read_it():
    """The guard is only meaningful while somebody still builds the gold from truth."""
    assert any(TRUTH_NAME in s for s in string_constants(PACKAGE / GOLD_MODULE))


def test_the_evaluation_side_reads_the_gold_file_and_not_the_truth():
    source = PACKAGE / "evaluate.py"
    assert "read_gold" in imports_names(source)
    assert not any(TRUTH_NAME in s for s in string_constants(source))


def imports_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
