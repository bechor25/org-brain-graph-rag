"""`brain index` — create the final index set, take the census, evaluate the gate.

The order is deliberate. Indexes first, because the census reports their state and a
census taken before the wait would report `POPULATING` for indexes this very run
created. Then every read, then the two artefacts (`data/reports/index.json` and the Hebrew
census), then the gate — which is evaluated from the report dict, not from a second pass
over the graph, so the table and the document can never disagree.

This step writes no data. It creates schema and it reads; `brain load` run straight after
it still reports `0` created, which is one of the acceptance criteria.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.index import census as cen
from brain.index import gate as gate_mod
from brain.index import render as render_mod
from brain.index import schema as index_schema

REPORT_NAME = "index.json"
CENSUS_NAME = "plan1-graph-census.md"

#: Set while `--smoke` shells out, so a live test that runs `brain index` cannot recurse.
SMOKE_GUARD = "BRAIN_INDEX_SMOKE_RUNNING"

#: How much of `make smoke`'s output to keep in the report.
SMOKE_TAIL_LINES = 40

#: Things a reader of `data/reports/index.json` should know that are not counts.
NOTES = [
    "This step creates indexes and reads. It writes no nodes, no edges and no properties, "
    "so `brain load` run after it still reports 0 created / 0 deleted.",
    "Every count under `nodes`, `edges`, `provenance`, `orphans`, `chunks`, `communities` "
    "and `corpus` is read back from Neo4j. `resolution` and `dangling_refs` are the two "
    "exceptions and name their source file: precision/recall is measured against a gold "
    "file and a merge ledger, and a dangling ref left no node to count.",
    "The empty label prefix means 'every label that does not start with an underscore': "
    "the live tests build their own label spaces (_Smoke…, _Comm…, _ResetTest…) in the "
    "same database, and counting them as corpus would be a lie about the graph.",
]


def _merge_report(path: Path, section: dict[str, Any]) -> dict[str, Any]:
    """Keep what a previous run recorded; replace only the keys this run produced."""
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(section)
    return existing


def _warn(out: list[dict[str, Any]], severity: str, name: str, detail: str) -> None:
    out.append({"severity": severity, "name": name, "detail": detail})


def sanity_warnings(ctx: GraphContext, report: dict[str, Any], present: set[str]) -> list[dict]:
    """Numbers that look wrong, reported and never acted on.

    A threshold this step applied silently would be a filter — and a filter over a census
    is how a graph comes to disagree with the document that describes it. Every entry here
    is a sentence for a human, not a branch in the code.
    """
    warnings: list[dict[str, Any]] = []
    idx = report.get("indexes") or {}
    if idx.get("not_online"):
        _warn(warnings, "error", "indexes_not_online", str(idx["not_online"]))
    if idx.get("not_populated"):
        _warn(warnings, "warn", "indexes_below_100_percent", str(idx["not_populated"]))

    prov = report.get("provenance") or {}
    if (prov.get("edges") or {}).get("missing"):
        _warn(warnings, "error", "llm_edges_without_provenance", str(prov["edges"]["by_type"]))
    if (prov.get("nodes") or {}).get("missing"):
        _warn(warnings, "error", "llm_nodes_without_provenance", str(prov["nodes"]["by_label"]))

    synth = (report.get("synthetic") or {}).get("by_label") or {}
    unflagged = {k: v["missing_flag"] for k, v in synth.items() if v.get("missing_flag")}
    if unflagged:
        _warn(
            warnings,
            "warn",
            "synthetic_flag_missing",
            f"labels whose nodes carry no `synthetic` property at all: {unflagged}. "
            "`brain reset --synthetic` decides what to delete by that flag.",
        )

    meta = report.get("index_meta") or {}
    if meta.get("missing_meta"):
        _warn(
            warnings,
            "warn",
            "vector_index_without_index_meta",
            f"{meta['missing_meta']} — a vector index with no IndexMeta node cannot say "
            "which model produced its vectors, and a query embedded by another model "
            "returns nonsense rather than an error.",
        )
    for row in meta.get("rows", []):
        live, stored = row.get("live_vectors"), row.get("stored_count")
        if live is not None and stored is not None and live != stored:
            _warn(
                warnings,
                "info",
                "index_meta_count_differs_from_live",
                f"{row['index']}: IndexMeta says {stored}, live count is {live} "
                "(decision 5 — the stored chunk count includes orphaned chunks).",
            )

    com = report.get("communities") or {}
    if not com.get("present"):
        _warn(warnings, "warn", "communities_absent", str(com.get("note")))
    elif not com.get("summarised"):
        _warn(
            warnings,
            "warn",
            "no_community_has_a_report",
            f"none of the {com.get('communities')} communities carries a summary — global "
            "search (S5) has nothing to search.",
        )
    else:
        # Deliberately `info`, and phrased as coverage. `brain communities` summarises a
        # *selected* set (min-size); the rest are `misc` by design, so counting them as
        # "missing a report" would invent a defect out of a design decision. What actually
        # matters for S5 is how much of the graph a summarised community can reach.
        _warn(
            warnings,
            "info",
            "community_report_coverage",
            f"{com['summarised']} of {com['communities']} communities carry a report "
            f"({com.get('pct_summarised')}%), and they cover "
            f"{com.get('pct_members_in_a_summarised_community')}% of members "
            f"({com.get('members_in_a_summarised_community')} of "
            f"{com.get('distinct_members')}). The unsummarised rest are the `misc` "
            "communities `brain communities` left below its size threshold — see "
            "data/reports/communities.json.",
        )

    kips = (report.get("sanity") or {}).get("kip_entity_coverage") or {}
    if kips.get("without_an_entity"):
        _warn(
            warnings,
            "warn",
            "kip_with_zero_entities",
            f"{kips['without_an_entity']} of {kips['chunked_kips']} chunked KIPs produced no "
            f"Entity ({kips['pct_without_an_entity']}%). {kips['rule']}",
        )

    orph = report.get("orphans") or {}
    for label, info in (orph.get("by_label") or {}).items():
        if info["pct"] >= 100.0:
            _warn(
                warnings,
                "info",
                f"every_{label.lower()}_is_an_orphan",
                f"all {info['of']} {label} nodes have no relationship — the loader creates "
                "the container but nothing points at it yet.",
            )
    return warnings


def kip_entity_coverage(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Chunked KIPs that produced no `Entity` — the brief's named sanity threshold.

    Measured and reported whether or not it is zero. A threshold that only appears in the
    report when it trips leaves a reader unable to tell "we checked and it was fine" from
    "nobody checked", and those are not the same document.
    """
    if not {"Document", "Chunk", "Entity"} <= present:
        return {"measured": False, "reason": "needs Document, Chunk and Entity"}
    rows = ctx.read(
        f"MATCH (d:{ctx.label('Document')}) WHERE d.kind = 'KIP' "
        f"AND EXISTS {{ MATCH (d)-[:HAS_CHUNK]->(:{ctx.label('Chunk')}) }} "
        "WITH d, COUNT { MATCH (d)-[:HAS_CHUNK]->"
        f"(:{ctx.label('Chunk')})-[:MENTIONS]->(:{ctx.label('Entity')}) }} AS entities "
        "RETURN count(d) AS chunked, count(CASE WHEN entities = 0 THEN 1 END) AS empty"
    )
    row = rows[0] if rows else {"chunked": 0, "empty": 0}
    return {
        "measured": True,
        "chunked_kips": row["chunked"],
        "without_an_entity": row["empty"],
        "pct_without_an_entity": (
            round(100 * row["empty"] / row["chunked"], 2) if row["chunked"] else 0.0
        ),
        "rule": (
            "a KIP the extractor found nothing in is either a stub page or a gap in the "
            "extraction, and the census cannot tell which — so it is reported, not filtered."
        ),
    }


def run_smoke(repo_root: Path, echo: Callable[[str], None] = print) -> dict[str, Any]:
    """Shell out to `make smoke` and record what happened, so the gate has a measured value.

    Guarded against recursion: a live test that invokes `brain index` runs with
    `BRAIN_INDEX_SMOKE_RUNNING` set and gets a refusal instead of a second `make smoke`.
    """
    if os.environ.get(SMOKE_GUARD):
        return {
            "ok": False,
            "status": "SKIPPED",
            "at": utc_now_iso(),
            "note": "already inside a `make smoke` run — refusing to recurse",
        }
    env = {**os.environ, SMOKE_GUARD: "1"}
    started = time.perf_counter()
    echo("smoke: running `make smoke` (this takes a few minutes)…")
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["make", "smoke"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    # Wide enough to hold pytest's whole `FAILED …` block, not just its last few lines:
    # a recorded FAIL is only actionable if the report says which tests failed, and a
    # 15-line tail on a 154-test live suite showed 13 of 22 failures and hid the rest.
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    failures = [ln for ln in lines if ln.startswith(("FAILED ", "ERROR "))]
    tail = "\n".join(lines[-SMOKE_TAIL_LINES:])
    return {
        "ok": proc.returncode == 0,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "exit_code": proc.returncode,
        "command": "make smoke",
        "cwd": str(repo_root),
        "at": utc_now_iso(),
        "duration_s": round(time.perf_counter() - started, 1),
        "failures": failures,
        "failing_files": sorted({ln.split("::")[0].split(" ", 1)[-1] for ln in failures}),
        "tail": tail,
    }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_index(
    *,
    client: GraphClient,
    embed_dim: int,
    ollama_url: str,
    embed_model: str,
    reports_dir: Path,
    canonical_dir: Path,
    docs_dir: Path,
    prefix: str = "",
    check_gate: bool = False,
    smoke: bool = False,
    write_report: bool = True,
    write_census: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    ctx = GraphContext(client, prefix=prefix)
    durations: dict[str, float] = {}

    t0 = time.perf_counter()
    applied = index_schema.apply_indexes(ctx, embed_dim)
    durations["indexes"] = round(time.perf_counter() - t0, 2)
    echo(
        f"indexes: {applied['managed']} managed, {applied['created']} created, "
        f"{applied['existing']} already there; waited {applied['await']['waited_s']}s"
    )

    t0 = time.perf_counter()
    status = index_schema.index_status(ctx)
    offline = index_schema.not_online(status)
    unpopulated = index_schema.not_populated(status)
    managed_rows = [r for r in status if r.get("managed")]
    indexes = {
        "managed": len(managed_rows),
        "online": sum(1 for r in managed_rows if r["state"] == "ONLINE"),
        "not_online": offline,
        "not_populated": unpopulated,
        "applied": applied,
        "status": status,
    }
    durations["index_status"] = round(time.perf_counter() - t0, 2)
    echo(
        f"indexes: {indexes['online']}/{indexes['managed']} ONLINE"
        + (f" — {offline}" if offline else "")
    )

    t0 = time.perf_counter()
    present = set(cen.present_labels(ctx))
    report: dict[str, Any] = {
        "step": "index",
        "generated_at": utc_now_iso(),
        "label_prefix": prefix,
        "report_path": str(reports_dir / REPORT_NAME),
        "indexes": indexes,
        "labels_present": sorted(present),
        "corpus": cen.corpus_census(ctx, present),
        "nodes": cen.node_census(ctx, present),
        "synthetic": cen.synthetic_census(ctx, present),
        "edges": cen.edge_census(ctx),
        "provenance": cen.provenance_census(ctx, present),
        "orphans": cen.orphan_census(ctx, present),
        "chunks": cen.chunk_census(ctx, present),
        "communities": cen.community_census(ctx, present),
        "index_meta": cen.index_meta_census(ctx, present, status),
        "resolution": cen.resolution_census(reports_dir),
        "dangling_refs": cen.dangling_census(reports_dir),
        "canonical": cen.canonical_fingerprint(canonical_dir),
        "versions": cen.versions(ctx, ollama_url, embed_model),
        "notes": NOTES,
    }
    report["sanity"] = {"kip_entity_coverage": kip_entity_coverage(ctx, present)}
    durations["census"] = round(time.perf_counter() - t0, 2)
    echo(
        f"census: {report['nodes']['total']:,} nodes, {report['edges']['total']:,} edges, "
        f"{report['provenance']['edges']['missing']} LLM edges without provenance"
    )

    previous = _merge_report(reports_dir / REPORT_NAME, {})
    smoke_result = run_smoke(repo_root(), echo) if smoke else previous.get("smoke")
    if smoke_result:
        report["smoke"] = smoke_result
        echo(f"smoke: {smoke_result.get('status')} ({smoke_result.get('at')})")

    report["warnings"] = sanity_warnings(ctx, report, present)
    report["gate"] = gate_mod.evaluate(
        corpus=report["corpus"],
        resolution=report["resolution"],
        provenance=report["provenance"],
        indexes=indexes,
        docs_dir=docs_dir,
        smoke=smoke_result,
    )
    durations["total"] = round(time.perf_counter() - started, 2)
    report["last_run"] = {
        "at": report["generated_at"],
        "check_gate": check_gate,
        "smoke": smoke,
        "durations_s": durations,
        "counters": dict(ctx.counters),
    }

    census_path = docs_dir / "report" / CENSUS_NAME
    report["census_document"] = str(census_path)
    if write_report:
        write_json_atomic(
            reports_dir / REPORT_NAME, _merge_report(reports_dir / REPORT_NAME, report)
        )
        echo(f"report: {reports_dir / REPORT_NAME}")
    if write_census:
        census_path.parent.mkdir(parents=True, exist_ok=True)
        census_path.write_text(render_mod.render(report), encoding="utf-8")
        echo(f"census: {census_path}")

    for w in report["warnings"]:
        echo(f"  [{w['severity']}] {w['name']}: {w['detail']}")

    code = 0
    if check_gate:
        echo("")
        echo(gate_mod.render_table(report["gate"]))
        code = 0 if report["gate"]["ok"] else 1
    return report, code


def index_from_settings(
    settings: Any,
    *,
    docs_dir: Path = Path("docs"),
    check_gate: bool = False,
    smoke: bool = False,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    with GraphClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    ) as client:
        client.verify()
        return run_index(
            client=client,
            embed_dim=settings.embed_dim,
            ollama_url=settings.ollama_url,
            embed_model=settings.embed_model,
            reports_dir=settings.reports_dir,
            canonical_dir=settings.canonical_dir,
            docs_dir=docs_dir,
            check_gate=check_gate,
            smoke=smoke,
            echo=echo,
        )
