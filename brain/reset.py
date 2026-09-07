"""`brain reset` — take the POC's test data out and leave the installation standing.

ADR-0005 §3: the last step of the POC is deleting it. Three scopes, composable:

``--graph``
    Every node this pipeline created, in the current label namespace. **Constraints and
    indexes stay.** They are schema, not data: dropping them would make the next
    `brain load` slow (a label scan per `MERGE`) and unsafe (two transactions can create
    two nodes with the same key), and re-creating them is the one part of a reload that
    cannot be done concurrently.

``--data``
    The contents of `data/raw|canonical|batches|reports|eval`. Never `data/fixtures` —
    that is the committed mini corpus the tests and `make smoke` run on, and a reset that
    ate it would leave the repo unable to prove it still works.

``--synthetic``
    Only what `brain synth` invented: canonical records with ``synthetic: true``, the
    nodes they became, the merge ledger, the truth file and the synthetic batches. What
    survives is the real Kafka corpus, still loaded, still queryable.

One rule the code enforces rather than documents: **a shared node is not synthetic.**
Containers merge on `name`, so `Component {name: "clients"}` is one node that both Jira
and the synthetic ADO layer claim, and the last writer stamps `synthetic` on it. Deleting
every ``synthetic = true`` node would therefore delete a real component and every edge into
it. So the synthetic sweep first reads the canonical files for the keys a *real* record
still claims, keeps those nodes, and resets their flag — exactly what the next
`brain load` would write.

Nothing here runs without ``--yes``. Without it the command prints the manifest it would
have applied and exits non-zero, so a script cannot mistake a refusal for a wipe.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.canon.io import read_jsonl
from brain.canon.models import Container
from brain.graph.context import GraphContext
from brain.graph.mapping import CONTAINER_KEY, container_label
from brain.graph.provenance import LEDGER_FILENAME as SYNTHETIC_LEDGER
from brain.graph.report import PRIMARY_LABELS, existing_labels
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.resolve.ledger import LEDGER_NAME as RESOLUTION_LEDGER
from brain.resolve.ledger import SECTIONS as LEDGER_SECTIONS

REPORT_NAME = "reset.json"

#: Every label the pipeline writes. `PRIMARY_LABELS` is `brain load`'s; the rest arrive in
#: chunk (06), extract (07) and communities (09). Listed here rather than discovered, so a
#: reset deletes what this build owns and never a label somebody else put in the database.
EXTRA_LABELS: tuple[str, ...] = ("Chunk", "IndexMeta", "Entity", "Community")
GRAPH_LABELS: tuple[str, ...] = (*PRIMARY_LABELS, *EXTRA_LABELS)

#: Cleared by `--data`, in this order. `fixtures` is deliberately absent.
DATA_DIRS: tuple[str, ...] = ("raw", "canonical", "batches", "reports", "eval")
PROTECTED_DIRS: frozenset[str] = frozenset({"fixtures"})

#: Canonical files a `synthetic: true` record can live in.
CANONICAL_FILES: tuple[str, ...] = (
    "workitems",
    "documents",
    "persons",
    "changes",
    "containers",
)
#: Files that exist only because the synthetic layer does.
SYNTHETIC_FILES: tuple[str, ...] = (SYNTHETIC_LEDGER, "synthetic_truth.json")
SYNTHETIC_BATCH_DIR = "synthetic"

#: Rows per `DETACH DELETE` transaction — the same budget `brain resolve reset` uses.
BATCH_ROWS = 1000


LOCK_NAME = "reset.lock"


class ResetError(RuntimeError):
    """The reset cannot be done safely, or did not finish."""


@contextmanager
def reset_lock(data_dir: Path):
    """One reset at a time, via `O_EXCL` on `<data_dir>/reset.lock`.

    Honest about its scope: this stops a *second reset*, which is the case that produces
    the worst state (two sweeps interleaving a node deletion with a canonical rewrite).
    It does **not** stop a concurrent `brain load` or `brain chunk` — no other step takes
    the lock yet — which is why the manifest opens by saying the pipeline must be idle.
    A stale file after a crash is removed by hand; the message says so.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ResetError(
            f"{path} exists: another `brain reset` is running, or one crashed. Delete the "
            "file to continue."
        ) from exc
    try:
        os.write(fd, f"pid {os.getpid()} at {utc_now_iso()}\n".encode())
        os.close(fd)
        yield path
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- manifest


@dataclass
class Manifest:
    """What a reset did — or, without `--yes`, what it would have done."""

    scopes: list[str] = field(default_factory=list)
    applied: bool = False
    graph: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    synthetic: dict[str, Any] = field(default_factory=dict)
    census_after: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "step": "reset",
            "at": utc_now_iso(),
            "scopes": self.scopes,
            "applied": self.applied,
        }
        for name in ("graph", "data", "synthetic"):
            section = getattr(self, name)
            if section:
                out[name] = section
        if self.census_after:
            out["census_after"] = self.census_after
        return out


def format_manifest(manifest: Manifest) -> str:
    """The human line-by-line the command prints. Counts first; paths second."""
    verb = "deleted" if manifest.applied else "would delete"
    lines = [
        f"reset ({', '.join(manifest.scopes)}) — {'applied' if manifest.applied else 'DRY RUN'}",
        "  the pipeline must be IDLE: a harvest, load, chunk, extract or resolve running "
        "against this graph or data dir will write into a half-deleted state.",
    ]

    graph = manifest.graph
    if graph:
        for label, count in sorted(graph.get("nodes_by_label", {}).items()):
            if count:
                lines.append(f"  graph      {verb} {count:>7} :{label}")
        lines.append(
            f"  graph      {verb} {graph.get('nodes', 0)} nodes and "
            f"{graph.get('edges', 0)} edges; constraints and indexes kept"
        )

    syn = manifest.synthetic
    if syn:
        for name, count in sorted((syn.get("records") or {}).items()):
            if count:
                lines.append(f"  synthetic  {verb} {count:>7} records from {name}.jsonl")
        for label, count in sorted((syn.get("nodes_by_label") or {}).items()):
            if count:
                lines.append(f"  synthetic  {verb} {count:>7} :{label} nodes")
        if syn.get("shared_nodes_kept"):
            lines.append(
                f"  synthetic  kept {syn['shared_nodes_kept']} node(s) a real record also "
                "claims; their synthetic flag was cleared"
            )
        if syn.get("chunks_orphaned_by_parent"):
            lines.append(
                f"  synthetic  {verb} {syn['chunks_orphaned_by_parent']:>7} chunks whose "
                "parent record is gone"
            )
        if syn.get("entities_without_evidence"):
            lines.append(
                f"  synthetic  WARNING {syn['entities_without_evidence']} entities have no "
                "surviving evidence chunk. They are kept (resolution merged real identities "
                "into some of them); re-run `brain extract merge` to rebuild provenance."
            )
        if syn.get("ledger_rows_dropped"):
            lines.append(
                f"  synthetic  {verb} {syn['ledger_rows_dropped']} resolution-ledger row(s) "
                "pointing at a deleted identity"
            )
        for path in syn.get("files", []):
            lines.append(f"  synthetic  {verb} {path}")

    data = manifest.data
    if data:
        for entry in data.get("dirs", {}).values():
            lines.append(
                f"  data       {verb} {entry['entries']:>7} entries "
                f"({entry['bytes'] / 1e6:.1f} MB) from {entry['path']}"
            )
        lines.append(f"  data       kept {', '.join(sorted(PROTECTED_DIRS))} untouched")

    if manifest.census_after:
        nonzero = {k: v for k, v in manifest.census_after.items() if v}
        lines.append(
            "  census     every label is 0"
            if not nonzero
            else f"  census     still present: {nonzero}"
        )
    if not manifest.applied:
        lines.append("reset: nothing was deleted — pass --yes to go ahead.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- graph


def census(ctx: GraphContext, labels: Iterable[str] = GRAPH_LABELS) -> dict[str, int]:
    """`{label: count}` for every label the pipeline owns that the database has seen."""
    present = existing_labels(ctx)
    out: dict[str, int] = {}
    for label in labels:
        if label not in present:
            out[label] = 0
            continue
        rows = ctx.read(f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS c")
        out[label] = int(rows[0]["c"]) if rows else 0
    return out


def _delete_where(ctx: GraphContext, label: str, where: str = "") -> dict[str, int]:
    """`DETACH DELETE` a label in slices, so one transaction never holds the whole layer."""
    clause = f" WHERE {where}" if where else ""
    totals: dict[str, int] = {}
    while True:
        counters = ctx.write(
            f"MATCH (n:{ctx.label(label)}){clause} WITH n LIMIT {BATCH_ROWS} DETACH DELETE n"
        )
        for key, value in counters.items():
            totals[key] = totals.get(key, 0) + value
        if not counters.get("nodes_deleted"):
            return totals


def edge_total(ctx: GraphContext, present: set[str] | None = None) -> int:
    """Distinct edges with at least one endpoint this pipeline owns — counted exactly.

    Halving a per-label sum would be wrong twice over: an edge between two of our labels
    is counted from both ends, an edge to a node we do not own only from one. `DISTINCT r`
    asks the question directly.
    """
    labels = [label for label in GRAPH_LABELS if label in (present or existing_labels(ctx))]
    if not labels:
        return 0
    where = " OR ".join(f"n:{ctx.label(label)}" for label in labels)
    rows = ctx.read(f"MATCH (n)-[r]-() WHERE {where} WITH DISTINCT r RETURN count(r) AS c")
    return int(rows[0]["c"]) if rows else 0


def plan_graph(ctx: GraphContext) -> dict[str, Any]:
    """Counts before anything is deleted — the manifest's `would delete` numbers."""
    counts = census(ctx)
    present = existing_labels(ctx)
    return {
        "labels": list(GRAPH_LABELS),
        "nodes_by_label": counts,
        "edges": edge_total(ctx, present),
        "nodes": sum(counts.values()),
        "schema": "constraints and indexes are kept",
    }


def wipe_graph(ctx: GraphContext) -> dict[str, Any]:
    """Delete every node the pipeline owns. Constraints and indexes are untouched."""
    plan = plan_graph(ctx)
    present = existing_labels(ctx)
    counters: dict[str, int] = {}
    for label in GRAPH_LABELS:
        if label not in present:
            continue
        for key, value in _delete_where(ctx, label).items():
            counters[key] = counters.get(key, 0) + value
    remaining = {k: v for k, v in census(ctx).items() if v}
    if remaining:
        raise ResetError(f"the graph is half-reset: {remaining} survived the wipe")
    return {**plan, "counters": counters, "remaining": remaining}


# --------------------------------------------------------------------------- synthetic


def real_container_names(canonical_dir: Path) -> dict[str, set[str]]:
    """`{graph label: {name}}` for every container a NON-synthetic record still claims.

    This is the whole reason `--synthetic` is not one `WHERE n.synthetic` delete: the
    container loader merges on `name`, so `clients` is a single `Component` node that Jira
    and the synthetic ADO layer both write, and whichever wrote last set the flag.
    """
    path = canonical_dir / "containers.jsonl"
    protected: dict[str, set[str]] = {}
    if not path.is_file():
        return protected
    for record in read_jsonl(path, Container):
        if record.synthetic:
            continue
        label = container_label(record.kind)
        if label:
            protected.setdefault(label, set()).add(record.name)
    return protected


def strip_synthetic_records(canonical_dir: Path, *, apply: bool) -> dict[str, int]:
    """Drop every `synthetic: true` line from the canonical files. Returns per-file counts.

    Line-oriented on purpose: the files are JSONL and each line is one record, so this
    never has to parse (and re-serialize) 45 MB — which would also risk changing bytes the
    canon step is judged on.
    """
    removed: dict[str, int] = {}
    for name in CANONICAL_FILES:
        path = canonical_dir / f"{name}.jsonl"
        if not path.is_file():
            continue
        kept: list[str] = []
        dropped = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ResetError(
                        f"{path}: line {len(kept) + dropped + 1} is not JSON ({exc}). "
                        "Refusing to rewrite a file it cannot read — every line it failed "
                        "to parse would be deleted."
                    ) from exc
                if record.get("synthetic"):
                    dropped += 1
                else:
                    kept.append(stripped)
        removed[name] = dropped
        if apply and dropped:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
            tmp.replace(path)
    return removed


def synthetic_person_ids(canonical_dir: Path) -> set[str]:
    """Canonical ids of the synthetic Person records — the rows the ledger may point at."""
    path = canonical_dir / "persons.jsonl"
    out: set[str] = set()
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("synthetic") and record.get("id"):
                out.add(str(record["id"]))
    return out


def prune_resolution_ledger(canonical_dir: Path, ids: set[str], *, apply: bool) -> int:
    """Drop ledger rows whose identity or canonical target is a deleted synthetic person.

    Not in ADR-0005's list, and required by it anyway: `brain load` routes every person
    through this ledger *before* it merges. A row `xray:rao.jun -> jira:jrao` left behind
    after the synthetic layer is gone is harmless; a row pointing *at* a deleted canonical
    id would send a real identity to a node that no longer exists.
    """
    path = canonical_dir / RESOLUTION_LEDGER
    if not path.is_file() or not ids:
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResetError(f"{path} is not valid JSON: {exc}") from exc
    dropped = 0
    for section in LEDGER_SECTIONS:
        rows = raw.get(section)
        if not isinstance(rows, dict):
            continue
        keep = {
            key: value
            for key, value in rows.items()
            if key not in ids and str((value or {}).get("canonical")) not in ids
        }
        dropped += len(rows) - len(keep)
        raw[section] = keep
    if apply and dropped:
        write_json_atomic(path, raw)
    return dropped


def wipe_synthetic(
    canonical_dir: Path,
    *,
    ctx: GraphContext | None,
    batches_dir: Path | None = None,
    apply: bool,
) -> dict[str, Any]:
    """Remove the synthetic layer from the canonical files and, if given, from the graph."""
    protected = real_container_names(canonical_dir)
    person_ids = synthetic_person_ids(canonical_dir)
    out: dict[str, Any] = {
        "records": strip_synthetic_records(canonical_dir, apply=apply),
        "files": [],
        "shared_nodes_kept": 0,
        "ledger_rows_dropped": prune_resolution_ledger(canonical_dir, person_ids, apply=apply),
    }

    for name in SYNTHETIC_FILES:
        path = canonical_dir / name
        if path.is_file():
            out["files"].append(str(path))
            if apply:
                path.unlink()
    if batches_dir is not None:
        batch_dir = batches_dir / SYNTHETIC_BATCH_DIR
        if batch_dir.is_dir():
            out["files"].append(str(batch_dir))
            if apply:
                shutil.rmtree(batch_dir)

    if ctx is not None:
        out.update(_wipe_synthetic_graph(ctx, protected, apply=apply))
    return out


def _wipe_synthetic_graph(
    ctx: GraphContext, protected: dict[str, set[str]], *, apply: bool
) -> dict[str, Any]:
    present = existing_labels(ctx)
    by_label: dict[str, int] = {}
    kept = 0
    counters: dict[str, int] = {}

    for label in GRAPH_LABELS:
        if label not in present:
            continue
        names = protected.get(label)
        # A container a real record also claims stays; everything else marked synthetic goes.
        where = "n.synthetic = true"
        params: dict[str, Any] = {}
        if names:
            where += f" AND NOT n.`{CONTAINER_KEY}` IN $keep"
            params["keep"] = sorted(names)
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) WHERE {where} RETURN count(n) AS c", **params
        )
        by_label[label] = int(rows[0]["c"]) if rows else 0
        if names:
            shared = ctx.read(
                f"MATCH (n:{ctx.label(label)}) WHERE n.synthetic = true "
                f"AND n.`{CONTAINER_KEY}` IN $keep RETURN count(n) AS c",
                keep=sorted(names),
            )
            kept += int(shared[0]["c"]) if shared else 0
        if not apply:
            continue
        if by_label[label]:
            for key, value in _delete_where(ctx, label, where).items():
                counters[key] = counters.get(key, 0) + value
        if names:
            # Unconditional, and not folded into the branch above: a label can have nothing
            # to delete *because* every synthetic node in it is a shared one, and that is
            # exactly the case whose flag still has to be cleared.
            # What a reload would write: the node is real, the synthetic claim on it is not.
            ctx.write(
                f"MATCH (n:{ctx.label(label)}) WHERE n.synthetic = true "
                f"AND n.`{CONTAINER_KEY}` IN $keep SET n.synthetic = false",
                keep=sorted(names),
            )

    orphans = _orphaned_chunks(ctx, apply=apply)
    stranded = _entities_without_evidence(ctx)
    return {
        "nodes_by_label": {k: v for k, v in by_label.items() if v},
        "nodes": sum(by_label.values()),
        "shared_nodes_kept": kept,
        "chunks_orphaned_by_parent": orphans,
        "entities_without_evidence": stranded,
        "counters": counters,
    }


def _orphaned_chunks(ctx: GraphContext, *, apply: bool) -> int:
    """Chunks left parentless by the sweep and **not** themselves marked synthetic.

    `HAS_CHUNK` is the authoritative statement of parentage, so a chunk with no incoming
    one has no record behind it: leaving it would leave text in the vector index that
    retrieval can return and nothing can cite. This is the *fallback*, though, not the
    mechanism — the chunks of a synthetic record are deleted by the `Chunk` pass of the
    label sweep, on their own `synthetic = true`, and appear in `nodes_by_label` where a
    reader can see them. Anything this function finds is a chunk whose flag disagreed with
    its parent, which is what `brain chunk --stamp-synthetic` exists to prevent.

    The two modes ask *different queries on purpose*. Applying, the parents are already
    gone, so "no incoming HAS_CHUNK" is the answer. On a dry run they are all still there
    and that query would return 0 — a manifest promising to delete nothing and then
    deleting thousands. So a dry run counts the chunks whose parent is *about to* go
    instead, plus the ones already orphaned, minus the ones the label sweep already
    claimed — counting those twice is what made the manifest report 1,939 phantom orphans
    against an unstamped graph.
    """
    if "Chunk" not in existing_labels(ctx):
        return 0
    label = ctx.label("Chunk")
    if not apply:
        rows = ctx.read(
            f"MATCH (c:{label}) WHERE NOT coalesce(c.synthetic, false)\n"
            "OPTIONAL MATCH (p)-[:HAS_CHUNK]->(c)\n"
            "WITH c, collect(p) AS parents\n"
            "WHERE size(parents) = 0 OR all(p IN parents WHERE p.synthetic = true)\n"
            "RETURN count(c) AS c"
        )
        return int(rows[0]["c"]) if rows else 0
    rows = ctx.read(f"MATCH (c:{label}) WHERE NOT ()-[:HAS_CHUNK]->(c) RETURN count(c) AS c")
    total = int(rows[0]["c"]) if rows else 0
    if apply and total:
        while True:
            counters = ctx.write(
                f"MATCH (c:{label}) WHERE NOT ()-[:HAS_CHUNK]->(c) "
                f"WITH c LIMIT {BATCH_ROWS} DETACH DELETE c"
            )
            if not counters.get("nodes_deleted"):
                break
    return total


def _entities_without_evidence(ctx: GraphContext) -> int:
    """Entities whose every evidence chunk is gone. Counted, never deleted here.

    An entity is LLM-derived and conventions rule 3 says it names its evidence, so one
    with none left is a defect. It is still not this command's call to delete it: tier-2
    and tier-3 resolution may have merged real identities into that node, and a survivor
    carries edges no `MERGE` can put back. The manifest says the number and names the fix.

    Unlike the chunk sweep this is *not* predicted on a dry run — it counts the graph as
    it stands. That is safe in a way the chunk count was not: this number never causes a
    deletion, so under-reporting it warns less rather than destroying more.
    """
    if "Entity" not in existing_labels(ctx) or "Chunk" not in existing_labels(ctx):
        return 0
    rows = ctx.read(
        f"MATCH (e:{ctx.label('Entity')}) WHERE e.evidence_chunk_ids IS NOT NULL\n"
        f"OPTIONAL MATCH (c:{ctx.label('Chunk')}) WHERE c.id IN e.evidence_chunk_ids\n"
        "WITH e, count(c) AS alive WHERE alive = 0 RETURN count(e) AS c"
    )
    return int(rows[0]["c"]) if rows else 0


# --------------------------------------------------------------------------- data


def _dir_stats(path: Path) -> dict[str, Any]:
    entries = 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            entries += 1
            total += item.stat().st_size
    return {"path": str(path), "entries": entries, "bytes": total}


def wipe_data(data_dir: Path, *, apply: bool) -> dict[str, Any]:
    """Empty `data/raw|canonical|batches|reports|eval`, keeping the directories themselves.

    `data/fixtures` is never touched: it is committed, it is what `make check` and
    `make smoke` run on, and a reset that removed it would leave the repo unable to prove
    itself. The directories survive so the next step writes into a layout that exists.
    """
    dirs: dict[str, dict[str, Any]] = {}
    for name in DATA_DIRS:
        if name in PROTECTED_DIRS:  # pragma: no cover - defensive, DATA_DIRS excludes them
            continue
        path = data_dir / name
        if not path.is_dir():
            continue
        dirs[name] = _dir_stats(path)
        if not apply:
            continue
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    return {"dirs": dirs, "kept": sorted(PROTECTED_DIRS)}


# --------------------------------------------------------------------------- runner


def run_reset(
    *,
    data_dir: Path,
    canonical_dir: Path,
    batches_dir: Path,
    reports_dir: Path,
    ctx: GraphContext | None = None,
    graph: bool = False,
    data: bool = False,
    synthetic: bool = False,
    confirmed: bool = False,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Apply the requested scopes and print the manifest. Returns (manifest, exit code).

    Order matters: the synthetic sweep reads the canonical files, so it must run before
    `--data` deletes them, and it writes to the graph, so it must run before `--graph`
    empties it. Doing both is not wasted work — it is the difference between a manifest
    that says what the synthetic layer was and one that only says "everything".
    """
    started = time.perf_counter()
    scopes = [n for n, on in (("graph", graph), ("synthetic", synthetic), ("data", data)) if on]
    if not scopes:
        raise ResetError("nothing to reset: pass --graph, --data, --synthetic or --all")
    if (graph or synthetic) and ctx is None:
        raise ResetError("--graph and --synthetic need a graph connection")

    manifest = Manifest(scopes=scopes, applied=confirmed)
    apply = confirmed
    # A dry run reads nothing destructive, so it must NOT take the lock: "what would this
    # delete" is exactly the question you ask while something else is running.
    lock = reset_lock(data_dir) if apply else nullcontext()
    with lock:
        if synthetic:
            manifest.synthetic = wipe_synthetic(
                canonical_dir, ctx=ctx, batches_dir=batches_dir, apply=apply
            )
        if graph:
            manifest.graph = wipe_graph(ctx) if apply else plan_graph(ctx)  # type: ignore[arg-type]
        if data:
            manifest.data = wipe_data(data_dir, apply=apply)
        if ctx is not None:
            manifest.census_after = census(ctx)

        echo(format_manifest(manifest))
        report = manifest.as_dict()
        report["duration_s"] = round(time.perf_counter() - started, 2)
        # `--data` just emptied `data/reports`; writing the manifest back into it is the
        # point — it is the only record that the wipe happened, and the only file in there
        # that describes an empty directory rather than a pipeline run.
        if write_report and apply:
            reports_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(reports_dir / REPORT_NAME, report)
            echo(f"report: {reports_dir / REPORT_NAME}")
    return report, (0 if apply else 1)
