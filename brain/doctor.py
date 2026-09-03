"""Environment checks. Exit non-zero if anything required is missing.

Checks (spec §7.2): Neo4j reachable, APOC, GDS, server-enforced READ mode,
Ollama reachable, embedding model present, embedding dim matches settings.
"""

from __future__ import annotations

from dataclasses import dataclass

import typer
from neo4j.exceptions import ClientError

from brain.config import Settings, get_settings
from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _neo4j_checks(s: Settings) -> list[Check]:
    checks: list[Check] = []
    try:
        client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
        client.verify()
        checks.append(Check("neo4j", True, s.neo4j_uri))
    except Exception as e:  # noqa: BLE001
        return [Check("neo4j", False, f"{s.neo4j_uri}: {e}")]

    for name, q in [("apoc", "RETURN apoc.version() AS v"), ("gds", "RETURN gds.version() AS v")]:
        try:
            v = client.read(q)[0]["v"]
            checks.append(Check(name, True, v))
        except Exception as e:  # noqa: BLE001
            checks.append(Check(name, False, str(e).splitlines()[0]))

    try:
        try:
            client.read("CREATE (:_DoctorTmp)")
            checks.append(
                Check(
                    "read_mode_guard",
                    False,
                    "server ACCEPTED a write in READ mode",
                    required=False,
                )
            )
            client.write("MATCH (n:_DoctorTmp) DELETE n")
        except ClientError:
            checks.append(Check("read_mode_guard", True, "server rejects writes in READ mode"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("read_mode_guard", False, str(e).splitlines()[0]))
    finally:
        client.close()
    return checks


def _embed_checks(s: Settings) -> list[Check]:
    with OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim, timeout=30) as emb:
        try:
            present = emb.has_model()
        except Exception as e:  # noqa: BLE001
            return [Check("ollama", False, f"{s.ollama_url}: {e}")]
        checks = [Check("ollama", True, s.ollama_url), Check("embed_model", present, s.embed_model)]
        if not present:
            return checks
        try:
            v = emb.embed_one("doctor")
            checks.append(Check("embed_dim", True, f"{len(v)} == {s.embed_dim}"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("embed_dim", False, str(e).splitlines()[0]))
        return checks


def run_doctor() -> bool:
    s = get_settings()
    checks = _neo4j_checks(s) + _embed_checks(s)
    ok = True
    warnings = 0
    for c in checks:
        mark = "OK  " if c.ok else ("WARN" if not c.required else "FAIL")
        typer.echo(f"[{mark}] {c.name:<16} {c.detail}")
        if not c.ok:
            if c.required:
                ok = False
            else:
                warnings += 1
    summary = "all required checks passed" if ok else "REQUIRED CHECKS FAILED"
    if warnings:
        summary += f" ({warnings} warnings)"
    typer.echo("doctor: " + summary)
    return ok
