"""The projection query, read as text.

The projection is the one place where a wrong decision is invisible: a missing branch does
not fail, it just quietly makes a thinner graph and slightly worse communities. So the
brief's relation set, the two collapses and the synthetic filter are asserted here against
the generated Cypher, and the live test then asserts the same rules against real numbers.
"""

from __future__ import annotations

from brain.community import projection as proj
from brain.graph.context import GraphContext


class _NoClient:
    """`GraphContext` only needs a client to *run* a query; these tests only build one."""

    prefix = ""


def ctx(prefix: str = "") -> GraphContext:
    return GraphContext(_NoClient(), prefix=prefix)  # type: ignore[arg-type]


def query(prefix: str = "", *, include_synthetic: bool = False) -> str:
    return proj.projection_query(ctx(prefix), include_synthetic=include_synthetic)


def test_the_relation_set_is_exactly_the_briefs():
    assert proj.DIRECT_TYPES == (
        "REFERENCES",
        "LINKS_TO",
        "IMPLEMENTS",
        "DEPENDS_ON",
        "IN_COMPONENT",
        "DECIDES",
        "MOTIVATED_BY",
        "REJECTS",
    )
    assert proj.COLLAPSED_TYPES == ("MENTIONS_PARENT", "DELIVERS_KIP")


def test_every_projected_type_appears_in_the_query_once():
    text = query()
    for rel_type in proj.DIRECT_TYPES:
        assert f"-[:`{rel_type}`]->" in text, rel_type
        assert text.count(f"'{rel_type}' AS relType") == 1


def test_introduces_risk_is_left_out_and_named_so_the_report_can_count_it():
    assert "INTRODUCES_RISK" not in query()
    assert proj.NOT_PROJECTED_TYPES == ("INTRODUCES_RISK",)


def test_chunk_person_and_commit_are_not_projected_nodes():
    text = query()
    for label in ("Person", "PullRequest"):
        assert f"n:`{label}`" not in text
    # Chunk and Commit appear only inside a collapse, never as a projected endpoint
    assert "(c:`Chunk`)" in text and "(x:`Commit`)" in text
    # the collapse projects the chunk's *parent* against the entity, never the chunk
    assert "elementId(p) <= elementId(t)" in text


def test_an_edge_is_projected_once_however_many_relationships_carry_it():
    """Parallel duplicates and reciprocal pairs must not weight one edge twice."""
    text = proj.direct_branch(ctx(), "REFERENCES", include_synthetic=False).cypher()
    assert "CASE WHEN elementId(a) <= elementId(b) THEN a ELSE b END AS source" in text
    assert "CASE WHEN elementId(a) <= elementId(b) THEN b ELSE a END AS target" in text
    assert text.rstrip().endswith("RETURN DISTINCT source, target, 'REFERENCES' AS relType")


def test_every_edge_branch_canonicalises_its_pair_and_not_only_the_direct_ones():
    for branch in proj.branches(ctx(), include_synthetic=False):
        if branch.target is None:
            continue
        text = branch.cypher()
        assert f"elementId({branch.source}) <= elementId({branch.target})" in text, branch.name
        assert "RETURN DISTINCT source, target" in text, branch.name


def test_the_raw_receipt_counts_relationships_and_the_projected_one_counts_pairs():
    """The report subtracts the two to say how many duplicates the projection collapsed."""
    branch = proj.direct_branch(ctx(), "DECIDES", include_synthetic=False)
    assert branch.raw_count_cypher().endswith("RETURN count(*) AS n")
    assert branch.count_cypher().endswith("RETURN count(DISTINCT [source, target]) AS n")


def test_the_mentions_collapse_goes_through_the_chunks_parent():
    text = proj.mentions_branch(ctx(), include_synthetic=False).cypher()
    assert "(p)-[:HAS_CHUNK]->(c:`Chunk`)-[:MENTIONS]->(t)" in text
    assert text.rstrip().endswith("'MENTIONS_PARENT' AS relType")
    # one edge per pair, not one per chunk that mentions it
    assert "RETURN DISTINCT" in text


def test_the_kip_collapse_goes_through_the_commit_that_resolved_the_item():
    text = proj.delivers_kip_branch(ctx(), include_synthetic=False).cypher()
    assert "<-[:RESOLVES]-(x:`Commit`)-[:IMPLEMENTS_KIP]->" in text
    # 6,107 commits between the two: many produce the same work-item/document pair, and
    # DISTINCT over the canonical pair is what keeps that one edge rather than dozens
    assert "elementId(w) <= elementId(d)" in text


def test_synthetic_nodes_are_excluded_by_default_on_both_endpoints():
    text = query()
    assert text.count("coalesce(a.synthetic, false) = false") == len(proj.DIRECT_TYPES)
    assert text.count("coalesce(b.synthetic, false) = false") == len(proj.DIRECT_TYPES)
    assert "coalesce(n.synthetic, false) = false" in text
    assert "coalesce(w.synthetic, false) = false" in text


def test_include_synthetic_drops_every_synthetic_filter():
    assert "synthetic" not in query(include_synthetic=True)


def test_isolated_nodes_get_their_own_branch_so_they_can_be_counted():
    text = query()
    assert "RETURN n AS source, null AS target, null AS relType" in text
    assert text.startswith("CALL () {")


def test_the_projection_is_undirected_and_named_by_the_caller():
    text = query()
    assert "undirectedRelationshipTypes: ['*']" in text
    assert "gds.graph.project(\n  $name, source, target," in text


def test_the_label_namespace_reaches_every_branch():
    """A smoke run must not read the production graph's nodes through an unprefixed label."""
    text = query("_Comm")
    assert "`Entity`" not in text.replace("`_CommEntity`", "")
    assert "n:`_CommEntity`" in text and "(c:`_CommChunk`)" in text
    assert proj.graph_name(ctx("_Comm")) == "_Commbrain_communities"


def test_a_count_query_is_generated_from_the_same_branch_as_the_projection():
    branch = proj.direct_branch(ctx(), "LINKS_TO", include_synthetic=False)
    count = branch.count_cypher()
    assert count.startswith(branch.match)
    assert count.endswith("RETURN count(DISTINCT [source, target]) AS n")
    nodes = proj.node_branch(ctx(), include_synthetic=False).count_cypher()
    assert nodes.endswith("RETURN count(DISTINCT n) AS n")


def test_density_is_computed_here_because_the_projection_call_returns_none():
    """`gds.graph.project` yields graphName/nodeCount/relationshipCount/projectMillis only."""
    assert proj.density(0, 0) == 0.0
    assert proj.density(1, 0) == 0.0
    assert proj.density(4, 6) == 1.0  # every pair joined
    assert 0.0 < proj.density(11875, 19291) < 0.001


def test_leiden_runs_at_concurrency_one_so_the_seed_actually_fixes_the_run():
    config = proj.leiden_config(seed=7)
    assert config["concurrency"] == 1
    assert config["randomSeed"] == 7
    assert config["includeIntermediateCommunities"] is True
