"""`brain resolve` — three tiers of entity resolution, and the numbers that grade them.

The module boundary that matters most here is the one against `data/canonical/
synthetic_truth.json`: it is ground truth by construction, and only `brain.resolve.gold`
is allowed to open it. Every other module in this package resolves from what the graph
and the canonical files say, which is what makes the P/R in `data/reports/resolve.json`
mean anything at all. `tests/test_resolve_truth_isolation.py` is the guard.
"""
