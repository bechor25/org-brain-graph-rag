"""`data/reports/modularity.json` — the evidence that Task 10 changed nothing it touched.

ADR-0005 moved every URL, query, project key and title pattern out of Python and into
`sources.yaml`. The claim that has to survive review is not "the tests pass", it is
**"the canonical corpus is byte-for-byte what it was"** — a registry that builds an
equivalent-but-different query produces a corpus that is *almost* the same, and almost is
invisible until a retrieval answer cites a key that moved.

So this records what the step claims, measured rather than asserted, in the same place
every other step reports:

* ``canonical`` — sha256 and record count of each of the five canonical files, next to
  the digests recorded before the refactor. Any difference is a regression, and the
  report says which file.
* ``rerun`` — the same five files **produced again, now**, by the refactored code from
  the real raw on disk, into a scratch directory. `canonical` compares digests of files
  that were written before the refactor and have not been touched since; only this
  section proves that today's code still writes them.
* ``synthetic`` — how much of the corpus `brain reset --synthetic` would remove, per
  file, plus the resolution-ledger rows that name a synthetic identity.
* ``synthetic_stamp`` — the `Chunk.synthetic` / `Entity.synthetic` state of the live
  graph: how many are null, how many the derivation says should be true, how many are,
  and what a rerun of the backfill would change. All four have to agree.
* ``reset_dry_run`` — what `brain reset --synthetic` would delete, read-only. The number
  that failed review is `chunks_orphaned_by_parent`.
* ``redaction`` — a real harvest of a connector that quotes its own credential in an
  exception, into a temp directory, with the written report read back and searched.
* ``same_type_sources`` — two registries loaded in-process: one with a duplicate id
  (refused) and one with two ids of the same type (accepted, separate raw directories).
* ``registry`` — what the config actually resolved to: the sources, the allowlist, the
  document pattern, and the query signatures the connectors would use.

`data/` is not committed, so the digests of the real corpus live *here*, in a report, and
the digests of the committed mini fixture are pinned in `tests/test_modularity.py`. The
fixture is what a fresh clone can check; this file is what this machine's corpus is.

Written by ``python -m brain.modularity`` (``--rerun`` adds the canon re-run, ``--no-graph``
skips the two sections that need Neo4j). Every graph read here is read-only, and the reset
section is a dry run that can never be handed ``apply=True``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.harvest.registry import Registry, get_registry
from brain.reset import CANONICAL_FILES, prune_resolution_ledger, synthetic_person_ids

REPORT_NAME = "modularity.json"

#: The corpus as it stood before the registry refactor (2026-09-06 canon run, 1,416 real
#: work items + the synthetic layer). `brain canon` through `sources.yaml` reproduces
#: every one of these; a mismatch is the regression this report exists to catch.
BASELINE_SHA1: dict[str, str] = {
    "changes.jsonl": "c4870ae497cc240196997a8e5d29c52eeef0fad1",
    "containers.jsonl": "4bb4dc6bd9f6b779f2ecc909ae8faa1e8974101e",
    "documents.jsonl": "4e74e1a0df814bfd7487580cba7f278c10d5276d",
    "persons.jsonl": "73ae016f6c5f7695c8051bb71fdd0d4ac1430bae",
    "workitems.jsonl": "3472864688c1f933bea89786489ea02ccac04100",
}


def digest(path: Path) -> dict[str, Any]:
    """sha1, sha256 and the record count of one canonical file.

    sha1 because that is what `shasum` prints and what the step reports quoted; sha256
    because `brain load` already records canonical inputs that way and the two reports
    should be comparable without re-reading 45 MB.
    """
    data = path.read_bytes()
    return {
        "sha1": hashlib.sha1(data).hexdigest(),  # noqa: S324 - a file identity, not a MAC
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "records": data.count(b"\n"),
    }


def canonical_digests(canonical_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in CANONICAL_FILES:
        path = canonical_dir / f"{name}.jsonl"
        if path.is_file():
            out[path.name] = digest(path)
    return out


def synthetic_counts(canonical_dir: Path) -> dict[str, Any]:
    """What `brain reset --synthetic` would remove. Counts only — nothing is written."""
    per_file: dict[str, int] = {}
    for name in CANONICAL_FILES:
        path = canonical_dir / f"{name}.jsonl"
        if not path.is_file():
            continue
        # Iterating the handle, never `splitlines()`: the canonical files are written
        # with `ensure_ascii=False`, so a description containing U+2028 (LINE SEPARATOR)
        # or U+0085 lands in the file raw — and `str.splitlines()` breaks on both, cutting
        # a JSON record in half. Real records in this corpus do exactly that.
        with path.open(encoding="utf-8") as handle:
            per_file[name] = sum(
                1 for line in handle if line.strip() and json.loads(line).get("synthetic")
            )
    return {
        "records": per_file,
        "records_total": sum(per_file.values()),
        "resolution_ledger_rows": prune_resolution_ledger(
            canonical_dir, synthetic_person_ids(canonical_dir), apply=False
        ),
    }


def registry_facts(registry: Registry | None = None) -> dict[str, Any]:
    """What the config resolved to, including the signatures the connectors would send."""
    from brain.harvest.runner import CONNECTORS, build_connector

    reg = registry or get_registry()
    spec = reg.document_spec_default()
    signatures: dict[str, str] = {}
    for source in reg.enabled():
        if source.type in CONNECTORS:
            signatures[source.id] = build_connector(source, Path("data/raw")).signature(None)
    return {
        "path": str(reg.path),
        "sources": [
            {
                "id": s.id,
                "type": s.type,
                "display_name": s.display_name,
                "enabled": s.enabled,
                "project_keys": list(s.project_keys),
                "auth_env": s.auth_env,
                "auth_scheme": s.auth_scheme,
            }
            for s in reg.sources
        ],
        #: Types more than one enabled source claims. Empty here, and the one line a
        #: reviewer of a two-instance setup needs: the ids are separate, the canonical
        #: *keys* are not (docs/guides/adding-a-connector.md §5).
        "same_type_ids": reg.same_type_ids(),
        "issue_project_allowlist": sorted(reg.issue_project_allowlist()),
        "issue_key_blacklist": sorted(reg.issue_key_blacklist),
        "allowlist_warnings": reg.allowlist_warnings(),
        "synthetic_key_prefixes": dict(sorted(reg.synthetic_prefixes.items())),
        "document": {
            "kind": spec.kind,
            "title_pattern": spec.pattern.pattern,
            "title_pattern_loose": spec.loose.pattern if spec.loose else None,
            "key_format": spec.key_format,
        },
        "query_signatures": signatures,
    }


#: The label namespace the `redaction` self-check harvests into, and the fake credential
#: it plants. Neither ever reaches the real graph or `data/reports/harvest.json`: the
#: check runs a connector into a temporary directory and reads the file back from there.
PROBE_TOKEN = "modularity-probe-not-a-real-token"  # noqa: S105 - a fixture, by construction


def redaction_check() -> dict[str, Any]:
    """Harvest a connector that quotes its own credential, then search the written report.

    Not a restatement of `tests/test_harvest_auth.py` — the same scenario, run while the
    report is generated, so the evidence in the file is a measurement of this build rather
    than a claim that a test passed somewhere. The connector raises a bare `RuntimeError`
    holding its own clone URL, which is what a third-party library does; nothing between
    it and the file knows about that string except the redaction at the report boundary.
    """
    import tempfile

    from brain.harvest.auth import Credentials
    from brain.harvest.git import GitConnector
    from brain.harvest.runner import run_harvest

    reg = get_registry()
    git = next((s for s in reg.sources if s.type == "git"), None)
    if git is None:
        return {"ran": False, "reason": "no git source in the registry"}

    creds = Credentials(scheme="url_token", token=PROBE_TOKEN, env=git.auth_env)

    def leaky(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError(f"remote https://x-access-token:{PROBE_TOKEN}@github.com/x/y")

    printed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="modularity-redaction-") as tmp:
        root = Path(tmp)
        reports = root / "reports"
        reports.mkdir()
        report, code = run_harvest(
            [git.id],
            raw_dir=root / "raw",
            reports_dir=reports,
            factories={
                git.id: lambda d: GitConnector(d, source=git, runner=leaky, credentials=creds)
            },
            echo=printed.append,
        )
        written = (reports / "harvest.json").read_text(encoding="utf-8")

    # The basic scheme encodes the credential, so `redact(token)` alone would miss it.
    basic = Credentials(scheme="basic", token=PROBE_TOKEN, user="u", env=git.auth_env)
    return {
        "ran": True,
        "scenario": "a connector raises RuntimeError quoting its own clone URL",
        "token_in_report_bytes": PROBE_TOKEN in written,
        "token_in_report_object": PROBE_TOKEN in json.dumps(report),
        "token_in_echoed_lines": any(PROBE_TOKEN in line for line in printed),
        "redaction_marker_in_report": "***" in written,
        "exit_code": code,
        "basic_scheme_secrets": len(basic.secrets),
        "basic_encoded_is_redacted": PROBE_TOKEN
        not in basic.redact(f"Authorization: Basic {basic.basic_blob}")
        and "***" in basic.redact(f"Authorization: Basic {basic.basic_blob}"),
    }


_DUPLICATE_IDS = """
version: 1
sources:
  - id: jira-eu
    type: jira
    base_url: https://eu.example.test/jira
    query: 'project = EU'
  - id: jira-eu
    type: jira
    base_url: https://us.example.test/jira
    query: 'project = US'
"""

#: The same file with the second id fixed: legal, and the case the planner decided on.
_TWO_OF_A_TYPE = _DUPLICATE_IDS.replace(
    "  - id: jira-eu\n    type: jira\n    base_url: https://us",
    "  - id: jira-us\n    type: jira\n    base_url: https://us",
)


def same_type_check() -> dict[str, Any]:
    """Load two registries in-process: a duplicate id must be refused, two ids must not.

    The planner's decision for step 11b, measured. The second half also records the raw
    directories the two connectors resolve to, because "they do not collide" is the whole
    reason a duplicate id is an error.
    """
    import tempfile

    from brain.harvest.registry import RegistryError, load_registry
    from brain.harvest.runner import build_connector

    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="modularity-registry-") as tmp:
        root = Path(tmp)

        dup = root / "duplicate.yaml"
        dup.write_text(_DUPLICATE_IDS, encoding="utf-8")
        try:
            load_registry(dup)
        except RegistryError as exc:
            out["duplicate_id_rejected"] = True
            out["duplicate_id_message"] = str(exc).replace(str(dup), "<tmp>/duplicate.yaml")
        else:  # pragma: no cover - the registry would be broken
            out["duplicate_id_rejected"] = False
            out["duplicate_id_message"] = "accepted, which is the bug"

        two = root / "two.yaml"
        two.write_text(_TWO_OF_A_TYPE, encoding="utf-8")
        reg = load_registry(two)
        dirs = {
            sid: str(build_connector(reg.source(sid), Path("data/raw")).source_dir())
            for sid in reg.ids()
        }
        out["two_of_one_type_accepted"] = True
        out["two_of_one_type_ids"] = list(reg.ids())
        out["same_type_ids"] = reg.same_type_ids()
        out["raw_dirs"] = dirs
        out["raw_dirs_distinct"] = len(set(dirs.values())) == len(dirs)
    out["live_registry_same_type_ids"] = get_registry().same_type_ids()
    return out


def synthetic_stamp_facts(ctx: Any, reports_dir: Path | None = None) -> dict[str, Any]:
    """The live `synthetic` flags on Chunk and Entity, and whether they need touching.

    Four numbers that have to agree: how many chunks the derivation says are synthetic,
    how many carry the flag, how many carry null, and how many a rerun of the backfill
    would write. `null` and `would_stamp` at 0 with `derived == stored` is the blocker
    closed; anything else is it open again.
    """
    from brain.chunk.synthetic import HISTORY_KEY, REPORT_KEY, plan

    drift = plan(ctx)
    facts: dict[str, Any] = {
        "chunks": {
            "total": drift["chunks"]["chunks"],
            "null": drift["chunks"]["null"],
            "wrong": drift["chunks"]["wrong"],
            "derived_synthetic": drift["chunks"]["synthetic_after"],
            "stored_synthetic": _count(ctx, "Chunk", "n.synthetic = true"),
        },
        "entities": {
            "total": drift["entities"]["entities"],
            "null": drift["entities"]["null"],
            "wrong": drift["entities"]["wrong"],
            "derived_synthetic": drift["entities"]["synthetic_after"],
            "stored_synthetic": _count(ctx, "Entity", "n.synthetic = true"),
        },
    }
    for label in ("chunks", "entities"):
        row = facts[label]
        row["would_stamp"] = row["null"] + row["wrong"]
        row["derived_matches_stored"] = row["derived_synthetic"] == row["stored_synthetic"]
    facts["rerun_would_stamp"] = {k: facts[k]["would_stamp"] for k in ("chunks", "entities")}
    facts["settled"] = all(
        facts[k]["would_stamp"] == 0
        and facts[k]["null"] == 0
        and facts[k]["derived_matches_stored"]
        for k in ("chunks", "entities")
    )
    if reports_dir is not None:
        chunk_report = _read_json(reports_dir / "chunk.json")
        facts["backfill_run"] = chunk_report.get(REPORT_KEY)
        facts["backfill_runs"] = len(chunk_report.get(HISTORY_KEY) or [])
    facts["mechanism"] = stamp_mechanism_check(ctx)
    return facts


#: Label namespace for the backfill mechanism check. Prefixed exactly the way
#: `tests/live/test_reset_live.py` and `make smoke` are, so the nodes it creates and
#: deletes can never be the real graph's `Chunk`, `Entity` or `WorkItem`.
MECHANISM_PREFIX = "_ModCheck"


def stamp_mechanism_check(ctx: Any) -> dict[str, Any]:
    """Run the backfill for real, on a graph that is genuinely in the pre-backfill state.

    The invariant above says the live graph is settled; it cannot say what settling it
    *did*, because the run that stamped 13,846 chunks left nothing behind that a later
    read can recover. So this reproduces the transition end to end — chunks with a null
    flag under a synthetic and a real parent, the real `stamp_synthetic`, then a rerun —
    and reports the before/after numbers it measured.

    Entirely inside `_ModCheck`: five nodes created and deleted in a namespace nothing
    else uses. The real labels are read, never written, anywhere in this module.
    """
    from brain.chunk.synthetic import plan, stamp_synthetic

    scratch = GraphContext(ctx.client, prefix=MECHANISM_PREFIX)
    labels = ("WorkItem", "Chunk", "Entity")

    def clean() -> None:
        for label in labels:
            scratch.write(f"MATCH (n:{scratch.label(label)}) DETACH DELETE n")

    clean()
    try:
        for key, synthetic in (("REAL-1", False), ("SYN-1", True)):
            scratch.write(
                f"CREATE (:{scratch.label('WorkItem')} {{key: $key, synthetic: $synthetic}})",
                key=key,
                synthetic=synthetic,
            )
        # No `synthetic` property at all on the chunks: the state the corpus was in.
        for chunk_id, parent in (("c-real", "REAL-1"), ("c-syn-1", "SYN-1"), ("c-syn-2", "SYN-1")):
            scratch.write(
                f"MATCH (w:{scratch.label('WorkItem')} {{key: $parent}})\n"
                f"CREATE (w)-[:HAS_CHUNK]->(:{scratch.label('Chunk')} {{id: $id}})",
                parent=parent,
                id=chunk_id,
            )
        for entity, evidence in (("E|real", ["c-real"]), ("E|syn", ["c-syn-1", "c-syn-2"])):
            scratch.write(
                f"CREATE (:{scratch.label('Entity')} "
                "{id: $id, synthetic: false, evidence_chunk_ids: $ids})",
                id=entity,
                ids=evidence,
            )

        # Measured before anything is stamped: `_true` reads the graph, so asking it
        # after the run would report the run's own result as the starting point.
        before = plan(scratch)
        before_true = {label: _true(scratch, label) for label in ("Chunk", "Entity")}

        run = stamp_synthetic(scratch, apply=True, echo=lambda _m: None)
        rerun = stamp_synthetic(scratch, apply=True, echo=lambda _m: None)
        return {
            "prefix": MECHANISM_PREFIX,
            "nodes": {"workitems": 2, "chunks": 3, "entities": 2},
            "before": {
                "chunks": {"null": before["chunks"]["null"], "true": before_true["Chunk"]},
                "entities": {"null": before["entities"]["null"], "true": before_true["Entity"]},
            },
            "stamped": run["stamped"],
            "after": {
                "chunks": {"null": run["after"]["chunks"]["null"], "true": _true(scratch, "Chunk")},
                "entities": {
                    "null": run["after"]["entities"]["null"],
                    "true": _true(scratch, "Entity"),
                },
            },
            "rerun_stamped": rerun["stamped"],
        }
    finally:
        clean()


def _true(ctx: Any, label: str) -> int:
    return _count(ctx, label, "n.synthetic = true")


def _count(ctx: Any, label: str, where: str) -> int:
    rows = ctx.read(f"MATCH (n:{ctx.label(label)}) WHERE {where} RETURN count(n) AS c")
    return int(rows[0]["c"]) if rows else 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def reset_dry_run(canonical_dir: Path, ctx: Any) -> dict[str, Any]:
    """What `brain reset --synthetic` would delete. `apply=False` is not a parameter here.

    The review's number is `chunks_orphaned_by_parent`: before the backfill it was 1,939
    chunks the manifest could only describe as "the parent is gone", and the label sweep
    reported no `Chunk` at all. After it, the chunks appear under `nodes_by_label` where
    they can be counted and the orphan fallback is empty.
    """
    from brain.reset import real_container_names, wipe_synthetic

    plan = wipe_synthetic(canonical_dir, ctx=ctx, batches_dir=None, apply=False)
    return {
        "applied": False,
        "protected_container_labels": sorted(real_container_names(canonical_dir)),
        "records": plan["records"],
        "nodes": plan["nodes"],
        "nodes_by_label": plan["nodes_by_label"],
        "shared_nodes_kept": plan["shared_nodes_kept"],
        "chunks_orphaned_by_parent": plan["chunks_orphaned_by_parent"],
        "entities_without_evidence": plan["entities_without_evidence"],
        "ledger_rows_dropped": plan["ledger_rows_dropped"],
    }


def rerun_canon(canonical_dir: Path, raw_dir: Path, scratch: Path) -> dict[str, Any]:
    """Run canon again, now, from the real raw — into a copy, and digest what comes out.

    `canonical.matches_baseline` compares digests of files written before the refactor;
    it proves nothing about the code that exists today. This does: the current mappers,
    the current registry and the current model write the five files from scratch, and the
    sha1 either reproduce the pre-refactor digests or they do not.

    The copy is not optional. Canon carries over the records a partial run is not
    responsible for — the whole synthetic layer among them — by reading the files it is
    about to replace, so it has to be pointed at a directory that already holds them. A
    scratch copy gets the real behaviour and costs a scratch directory if it goes wrong.
    """
    import shutil
    import time

    from brain.canon.runner import resolve_sources, run_canon

    started = time.perf_counter()
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / "canonical"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(canonical_dir, target)
    reports = scratch / "reports"
    reports.mkdir(exist_ok=True)

    sources = resolve_sources("all")
    _report, code = run_canon(
        sources,
        raw_dir=raw_dir,
        canonical_dir=target,
        reports_dir=reports,
        echo=lambda _m: None,
    )
    produced = {name: entry["sha1"] for name, entry in canonical_digests(target).items()}
    drift = {
        name: {"expected": BASELINE_SHA1[name], "got": sha}
        for name, sha in produced.items()
        if name in BASELINE_SHA1 and sha != BASELINE_SHA1[name]
    }
    return {
        "at": utc_now_iso(),
        "sources": sources,
        "raw_dir": str(raw_dir),
        "scratch_dir": str(target),
        "exit_code": code,
        "sha1": produced,
        "reproduces_baseline": not drift,
        "drift": drift,
        "duration_s": round(time.perf_counter() - started, 2),
    }


def build_report(
    canonical_dir: Path,
    registry: Registry | None = None,
    *,
    ctx: Any = None,
    reports_dir: Path | None = None,
    raw_dir: Path | None = None,
    rerun_dir: Path | None = None,
) -> dict[str, Any]:
    """The report. Every optional argument adds a section that needs something real.

    `ctx` is a **read-only** use of the graph: the stamp facts are counts and the reset
    section is a dry run. `raw_dir` + `rerun_dir` add the canon re-run, which writes only
    into `rerun_dir`. With none of them the report is what it always was, so the fixture
    tests keep working without a database.
    """
    digests = canonical_digests(canonical_dir)
    drift = {
        name: {"expected": BASELINE_SHA1[name], "got": entry["sha1"]}
        for name, entry in digests.items()
        if name in BASELINE_SHA1 and entry["sha1"] != BASELINE_SHA1[name]
    }
    report: dict[str, Any] = {
        "step": "modularity",
        "generated_at": utc_now_iso(),
        "canonical": {
            "dir": str(canonical_dir),
            "files": digests,
            "baseline_sha1": BASELINE_SHA1,
            "matches_baseline": not drift,
            "drift": drift,
            "note": (
                "digests of files on disk, written before the registry refactor. That "
                "today's code still produces them is the `rerun` section, not this one."
            ),
        },
        "synthetic": synthetic_counts(canonical_dir),
        "redaction": redaction_check(),
        "same_type_sources": same_type_check(),
        "registry": registry_facts(registry),
    }
    if raw_dir is not None and rerun_dir is not None:
        report["rerun"] = rerun_canon(canonical_dir, raw_dir, rerun_dir)
    if ctx is not None:
        report["synthetic_stamp"] = synthetic_stamp_facts(ctx, reports_dir)
        report["reset_dry_run"] = reset_dry_run(canonical_dir, ctx)
    return report


def write_report(
    canonical_dir: Path,
    reports_dir: Path,
    *,
    ctx: Any = None,
    raw_dir: Path | None = None,
    rerun_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    report = build_report(
        canonical_dir,
        ctx=ctx,
        reports_dir=reports_dir,
        raw_dir=raw_dir,
        rerun_dir=rerun_dir,
    )
    path = reports_dir / REPORT_NAME
    write_json_atomic(path, report)
    return path, report


def summarize(report: dict[str, Any]) -> str:
    """The four review numbers on four lines, so the file does not have to be opened."""
    lines = [f"modularity: {report['canonical']['dir']}"]
    canonical = report["canonical"]
    lines.append(
        f"  canonical  matches_baseline={canonical['matches_baseline']}"
        + (f" drift={sorted(canonical['drift'])}" if canonical["drift"] else "")
    )
    if rerun := report.get("rerun"):
        lines.append(
            f"  rerun      reproduces_baseline={rerun['reproduces_baseline']} "
            f"({len(rerun['sha1'])} files, {rerun['duration_s']}s, {rerun['scratch_dir']})"
        )
    if stamp := report.get("synthetic_stamp"):
        for label in ("chunks", "entities"):
            row = stamp[label]
            lines.append(
                f"  {label:<10} {row['total']} total, null {row['null']}, "
                f"synthetic {row['stored_synthetic']} (derived {row['derived_synthetic']}), "
                f"a rerun would stamp {row['would_stamp']}"
            )
        if mech := stamp.get("mechanism"):
            lines.append(
                f"  backfill   in {mech['prefix']}: chunks null "
                f"{mech['before']['chunks']['null']} -> {mech['after']['chunks']['null']}, "
                f"true {mech['before']['chunks']['true']} -> {mech['after']['chunks']['true']}; "
                f"stamped {mech['stamped']}, rerun {mech['rerun_stamped']}"
            )
    if reset := report.get("reset_dry_run"):
        lines.append(
            f"  reset      would delete {reset['nodes']} nodes {reset['nodes_by_label']}; "
            f"orphaned chunks {reset['chunks_orphaned_by_parent']}"
        )
    if red := report.get("redaction"):
        lines.append(
            f"  redaction  token_in_report={red.get('token_in_report_bytes')} "
            f"marker={red.get('redaction_marker_in_report')}"
        )
    same = report["same_type_sources"]
    lines.append(
        f"  ids        duplicate rejected={same['duplicate_id_rejected']}, "
        f"two of one type accepted={same['two_of_one_type_accepted']} "
        f"into {sorted(same['raw_dirs'].values())}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`python -m brain.modularity [--rerun] [--no-graph]`.

    Not a `brain` subcommand: this is the step's own evidence file, not a pipeline stage,
    and `brain/cli.py` is shared with three other agents' commands.
    """
    import argparse

    from brain.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m brain.modularity")
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="re-run canon from the real raw into a scratch copy and digest the output",
    )
    parser.add_argument("--no-graph", action="store_true", help="skip the sections that read Neo4j")
    parser.add_argument(
        "--rerun-dir",
        default=None,
        metavar="PATH",
        help="where the canon re-run writes (default: a temp directory that is kept)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    rerun_dir = None
    if args.rerun:
        import tempfile

        rerun_dir = Path(args.rerun_dir or tempfile.mkdtemp(prefix="modularity-rerun-"))

    client = None
    try:
        ctx = None
        if not args.no_graph:
            from brain.graph.client import GraphClient
            from brain.graph.context import GraphContext

            client = GraphClient(
                settings.neo4j_uri,
                settings.neo4j_user,
                settings.neo4j_password,
                settings.neo4j_database,
            )
            ctx = GraphContext(client)
        path, report = write_report(
            settings.canonical_dir,
            settings.reports_dir,
            ctx=ctx,
            raw_dir=settings.raw_dir if args.rerun else None,
            rerun_dir=rerun_dir,
        )
    finally:
        if client is not None:
            client.close()
    print(summarize(report))
    print(f"report: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
