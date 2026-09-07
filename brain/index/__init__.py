"""`brain index` — the final index set and the Plan 1 graph census.

Nothing in this package writes data. It creates indexes (idempotently), reads the graph
back, and turns what it read into `data/reports/index.json`, the Hebrew census under
`docs/report/` and a pass/fail table for the Plan 1 exit gate.
"""
