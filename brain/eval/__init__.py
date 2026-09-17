"""Evaluation tooling. Plan 2 Task 4 is the gate; Plan 3 grows the rest of the harness here.

Plan 2 Task 4 (`citations`, `answers`, `verify`, `gate`, `render`, `runner`) is the citation
gate: it reads the answer files the analyst agents wrote, pulls the citations out with a
regex, asks the graph whether each one exists, and writes the numbers down. Plan 3's
question set, layered metrics and blind judging live alongside it in this package.

What the gate does is deterministic, and what it refuses to do is the point: it never judges
whether an answer is *right*. That is the planner's call in Plan 2 (`planner_verdicts`) and a
blind rubric judge's in Plan 3. A citation that resolves is a fact code can establish, and
everything code cannot establish is left visibly empty rather than guessed.
"""
