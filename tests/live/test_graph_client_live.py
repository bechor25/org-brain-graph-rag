import pytest
from neo4j.exceptions import ClientError

from brain.config import Settings
from brain.graph.client import GraphClient

pytestmark = pytest.mark.live


@pytest.fixture
def client():
    s = Settings()
    c = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    c.verify()
    yield c
    c.write("MATCH (n:_PlanZeroTmp) DETACH DELETE n")
    c.close()


def test_read_returns_dicts(client):
    rows = client.read("RETURN 1 AS one, 'x' AS s")
    assert rows == [{"one": 1, "s": "x"}]


def test_write_returns_counters(client):
    counters = client.write("CREATE (:_PlanZeroTmp {k: $k})", k="a")
    assert counters["nodes_created"] == 1


def test_read_mode_rejects_writes(client):
    with pytest.raises(ClientError) as exc:
        client.read("CREATE (:_PlanZeroTmp {k: 'should-fail'})")
    assert "read" in str(exc.value).lower()


def test_write_batched_uses_unwind(client):
    rows = [{"k": f"b{i}"} for i in range(2500)]
    total = client.write_batched(
        "UNWIND $rows AS row CREATE (:_PlanZeroTmp {k: row.k})", rows, batch_size=1000
    )
    assert total["nodes_created"] == 2500
    assert client.read("MATCH (n:_PlanZeroTmp) RETURN count(n) AS c")[0]["c"] == 2500
