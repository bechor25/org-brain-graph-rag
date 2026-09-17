"""The attack table and the read table, in one place so nothing can drift apart.

Three things need the same list: the unit test (`make check`, no database), the live test
(the same queries through `run_cypher` with a real server behind it) and the report
(`data/reports/retrieve.json`, which claims a blocked percentage). If each kept its own
copy, the report would eventually claim coverage the tests do not have — which is the one
lie a security number must never tell.

Each blocked case names the reason it must be refused *for*, not merely that it is
refused. `CALL { CREATE … }` blocked as "write-verb" would be the right outcome for the
wrong reason, and the wrong reason is what breaks when the deny-list is next edited.
"""

from __future__ import annotations

#: (id, cypher, expected reason) — every one of these must be refused.
BLOCKED: tuple[tuple[str, str, str], ...] = (
    ("create", "CREATE (n:Foo) RETURN n", "write-verb"),
    ("detach-delete", "MATCH (n:Chunk) DETACH DELETE n", "write-verb"),
    ("delete", "MATCH (n:Chunk) WHERE n.id = 'x' DELETE n", "write-verb"),
    ("set", "MATCH (n:Chunk) SET n.text = 'x' RETURN n.id AS id", "write-verb"),
    ("set-label", "MATCH (n:Chunk) SET n:Poisoned RETURN n.id AS id", "write-verb"),
    ("merge", "MERGE (p:Person {id: 'evil'}) RETURN p.id AS id", "write-verb"),
    ("remove", "MATCH (n:Chunk) REMOVE n.text RETURN n.id AS id", "write-verb"),
    ("drop-index", "DROP INDEX chunk_text IF EXISTS", "write-verb"),
    (
        "create-index",
        "CREATE INDEX evil_idx IF NOT EXISTS FOR (n:Chunk) ON (n.id)",
        "write-verb",
    ),
    (
        "create-constraint",
        "CREATE CONSTRAINT evil IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "write-verb",
    ),
    ("call-subquery", "CALL { CREATE (n:Foo) } RETURN 1 AS x", "call-subquery"),
    ("call-subquery-scoped", "CALL () { CREATE (n:Foo) } RETURN 1 AS x", "call-subquery"),
    (
        "call-subquery-read",  # decision: every CALL {} subquery is refused, not just writing ones
        "CALL { MATCH (n:Chunk) RETURN n LIMIT 1 } RETURN n.id AS id",
        "call-subquery",
    ),
    (
        "foreach",
        "MATCH (n:Chunk) WITH n LIMIT 1 FOREACH (i IN [1] | SET n.x = 1) RETURN n.id AS id",
        "write-verb",
    ),
    ("load-csv", "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row LIMIT 1", "write-verb"),
    (
        "periodic-commit",
        "USING PERIODIC COMMIT 500 LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "write-verb",
    ),
    (
        "apoc-cypher-run",
        "CALL apoc.cypher.run('CREATE (n:Foo) RETURN 1 AS x', {}) YIELD value RETURN value",
        "procedure-not-allowed",
    ),
    (
        "apoc-cypher-doit",
        "CALL apoc.cypher.doIt('CREATE (n:Foo) RETURN 1 AS x', {}) YIELD value RETURN value",
        "procedure-not-allowed",
    ),
    (
        "apoc-cypher-function",  # a function, not a procedure: the same bypass without CALL
        "RETURN apoc.cypher.runFirstColumn('CREATE (n:Foo) RETURN 1', {}) AS x",
        "procedure-not-allowed",
    ),
    (
        "apoc-periodic-iterate",
        "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DETACH DELETE n', {batchSize: 100}) "
        "YIELD batches RETURN batches",
        "procedure-not-allowed",
    ),
    (
        "apoc-periodic-submit",
        "CALL apoc.periodic.submit('evil', 'CREATE (n:Foo)') YIELD name RETURN name",
        "procedure-not-allowed",
    ),
    (
        "apoc-create-node",
        "CALL apoc.create.node(['Foo'], {a: 1}) YIELD node RETURN node",
        "procedure-not-allowed",
    ),
    (
        "apoc-merge-node",
        "CALL apoc.merge.node(['Foo'], {a: 1}) YIELD node RETURN node",
        "procedure-not-allowed",
    ),
    (
        "apoc-refactor",
        "CALL apoc.refactor.mergeNodes([]) YIELD node RETURN node",
        "procedure-not-allowed",
    ),
    (
        "apoc-trigger",
        "CALL apoc.trigger.add('evil', 'CREATE (n:Foo)', {}) YIELD name RETURN name",
        "procedure-not-allowed",
    ),
    (
        "apoc-load-json",  # SSRF, not a write
        "CALL apoc.load.json('http://169.254.169.254/latest/meta-data/') YIELD value RETURN value",
        "procedure-not-allowed",
    ),
    (
        "apoc-export",
        "CALL apoc.export.csv.all('/tmp/dump.csv', {}) YIELD file RETURN file",
        "procedure-not-allowed",
    ),
    (
        "apoc-util-sleep",  # a denial-of-service vector, not a write
        "MATCH (n:Chunk) CALL apoc.util.sleep(60000) RETURN n.id AS id",
        "procedure-not-allowed",
    ),
    (
        "dbms-list-users",
        "CALL dbms.security.listUsers() YIELD username RETURN username",
        "procedure-not-allowed",
    ),
    (
        "dbms-kill",
        "CALL dbms.killQueries(['q-1']) YIELD queryId RETURN queryId",
        "procedure-not-allowed",
    ),
    ("db-await-indexes", "CALL db.awaitIndexes(300)", "procedure-not-allowed"),
    (
        "gds-project",
        "CALL gds.graph.project('evil', 'Chunk', 'MENTIONS') YIELD graphName RETURN graphName",
        "procedure-not-allowed",
    ),
    (
        "gds-stream",
        "CALL gds.pageRank.stream('g') YIELD nodeId, score RETURN nodeId, score",
        "procedure-not-allowed",
    ),
    (
        "second-statement",
        "MATCH (n:Chunk) RETURN n.id AS id LIMIT 1; CREATE (m:Foo)",
        "multiple-statements",
    ),
    (
        "line-comment-trick",
        "MATCH (n:Chunk) RETURN n.id AS id // harmless\nCREATE (m:Foo)",
        "write-verb",
    ),
    (
        "block-comment-trick",
        "MATCH (n:Chunk) /* RETURN n */ SET n.text = '' RETURN n.id AS id",
        "write-verb",
    ),
    (
        "unicode-escape",
        "MATCH (n:Chunk) WHERE n.id = '\\u0027 DETACH DELETE n //' RETURN n.id AS id",
        "escape-sequence",
    ),
    ("unicode-fullwidth", "ＣＲＥＡＴＥ (n:Foo) RETURN n", "write-verb"),
    (
        "union-write",
        "MATCH (n:Chunk) RETURN n.id AS id UNION CREATE (m:Foo) RETURN m.id AS id",
        "write-verb",
    ),
    ("use-clause", "USE system SHOW DATABASES YIELD name RETURN name", "write-verb"),
    (
        "unbalanced-quote",
        "MATCH (n:Chunk) WHERE n.id = 'abc RETURN n.id AS id",
        "unbalanced-quote",
    ),
    ("empty", "   \n  ", "empty"),
    # --- GQL. 2026.06 speaks it, and its write verb is not CREATE. Until this line the
    # deny-list did not know the word and the `EXPLAIN` plan was the only layer that
    # refused it; `PLAN_BLOCKED` below keeps that proof running.
    ("insert-gql", "INSERT (n:_GuardTmp {a: 1}) RETURN n.a AS a", "write-verb"),
    (
        "insert-gql-after-match",
        "MATCH (c:Chunk) WITH c LIMIT 1 INSERT (n:_GuardTmp {id: c.id}) RETURN n.id AS id",
        "write-verb",
    ),
    # --- a namespace nobody allowlisted, because nobody had heard of it
    (
        "unknown-namespace-procedure",
        "CALL n10s.rdf.import.fetch('http://evil.example/x.ttl', 'Turtle') "
        "YIELD terminationStatus RETURN terminationStatus",
        "procedure-not-allowed",
    ),
    (
        "unknown-namespace-function",  # an outbound HTTP call from inside a read query
        "RETURN genai.vector.encode('secret', 'OpenAI', {token: 'sk-x'}) AS v",
        "procedure-not-allowed",
    ),
    (
        "bare-procedure",
        "CALL sleep(60000) YIELD value RETURN value",
        "procedure-not-allowed",
    ),
    # --- backticks quote an identifier; they do not rename it
    (
        "backticked-procedure",
        "CALL `apoc`.`periodic`.`iterate`('MATCH (n) RETURN n', 'DETACH DELETE n', {}) "
        "YIELD batches RETURN batches",
        "procedure-not-allowed",
    ),
    (
        "backticked-function",
        "RETURN `apoc`.`cypher`.`runFirstColumn`('CREATE (n:Foo) RETURN 1', {}) AS x",
        "procedure-not-allowed",
    ),
    # --- SHOW: the server and its people are not the graph
    ("show-settings", "SHOW SETTINGS YIELD name, value RETURN name, value", "show-not-allowed"),
    (
        "show-transactions",
        "SHOW TRANSACTIONS YIELD transactionId, currentQuery RETURN transactionId, currentQuery",
        "show-not-allowed",
    ),
    ("show-users", "SHOW USERS YIELD user RETURN user", "show-not-allowed"),
    (
        "show-privileges",
        "SHOW PRIVILEGES YIELD access, action RETURN access, action",
        "show-not-allowed",
    ),
    (
        "show-procedures",  # reconnaissance: which of the allowlist's neighbours are installed
        "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc' RETURN name",
        "show-not-allowed",
    ),
    ("show-functions", "SHOW FUNCTIONS YIELD name RETURN name", "show-not-allowed"),
    ("show-databases", "SHOW DATABASES YIELD name RETURN name", "show-not-allowed"),
)

#: Cases the static scanner is *not* asked to catch on its own: they are planned by the
#: server and refused because the plan contains a write operator. `INSERT` is the reason
#: this table exists — on 2026.06 it was a write verb the deny-list had never heard of, and
#: layer 3 is what refused it. Deleting the case after adding the word to `WRITE_TOKENS`
#: would delete the evidence that the third layer does any work; the live test runs these
#: with the deny-list switched off, which is the only way to see it.
PLAN_BLOCKED: tuple[tuple[str, str, str], ...] = (
    ("plan-insert", "INSERT (n:_GuardTmp {a: 1}) RETURN n.a AS a", "write-plan"),
    ("plan-create", "CREATE (n:_GuardTmp {a: 1}) RETURN n.a AS a", "write-plan"),
    (
        "plan-set",
        "MATCH (n:Chunk) WITH n LIMIT 1 SET n.poisoned = true RETURN n.id AS id",
        "write-plan",
    ),
)

#: Ordinary read queries. All of these must pass the static guard untouched (except for a
#: possibly injected `LIMIT`), or S4 is useless. They are written against the *live* schema
#: (`StatusChange.to`, `(:WorkItem)-[:ASSIGNED_TO]->(:Person)`) rather than a plausible one,
#: because `brain cypher-examples check` runs them and reports how many returned rows: a
#: read case that passes the guard and answers nothing proves only half of what it should.
ALLOWED: tuple[tuple[str, str], ...] = (
    ("plain-match", "MATCH (w:WorkItem) RETURN w.key AS key LIMIT 5"),
    (
        "params",
        "MATCH (t:Test)-[:TESTS]->(w:WorkItem {key: $key}) RETURN t.key AS test, t.name AS name",
    ),
    (
        "literal-holds-a-write-word",
        "MATCH (c:Chunk) WHERE c.text CONTAINS 'CREATE TABLE' RETURN c.id AS id LIMIT 3",
    ),
    (
        "comment-holds-a-write-word",
        "// this query used to CREATE nodes; it does not any more\n"
        "MATCH (d:Document) RETURN d.key AS key LIMIT 3",
    ),
    (
        "regex",
        "MATCH (d:Document) WHERE d.title =~ '(?i).*rebalance.*' RETURN d.key AS key LIMIT 10",
    ),
    (
        "vector-index",
        "CALL db.index.vector.queryNodes('chunk_embedding', 5, $vector) YIELD node, score "
        "RETURN node.id AS id, score",
    ),
    (
        "fulltext-index",
        "CALL db.index.fulltext.queryNodes('chunk_text', $q) YIELD node, score "
        "RETURN node.id AS id, score LIMIT 5",
    ),
    ("apoc-meta", "CALL apoc.meta.schema() YIELD value RETURN keys(value) AS labels"),
    ("apoc-text-function", "RETURN apoc.text.clean('KIP-848') AS cleaned"),
    (
        "aggregation",
        "MATCH (c:Commit)-[:RESOLVES]->(w:WorkItem)-[:IN_COMPONENT]->(k:Component) "
        "WITH k, count(DISTINCT c) AS n RETURN k.name AS component, n ORDER BY n DESC LIMIT 5",
    ),
    (
        "temporal",
        "MATCH (w:WorkItem {key: $key})-[:HAS_CHANGE]->(s:StatusChange) "
        "WHERE s.field = 'status' AND s.at <= datetime($date) "
        "RETURN s.to AS status ORDER BY s.at DESC LIMIT 1",
    ),
    ("unwind", "UNWIND [1, 2, 3] AS x RETURN x AS n"),
    (
        "assignees",
        "MATCH (w:WorkItem {key: $key})-[a:ASSIGNED_TO]->(p:Person) "
        "RETURN p.display AS person, a.valid_from AS valid_from ORDER BY a.valid_from",
    ),
    ("hebrew-literal", "MATCH (c:Chunk) WHERE c.text CONTAINS 'רכיב' RETURN c.id AS id LIMIT 3"),
    ("property-named-like-a-verb", "MATCH (n:Chunk) RETURN n.text AS text, n.at AS at LIMIT 1"),
    ("count", "MATCH (n:Chunk) RETURN count(n) AS chunks"),
    # Filtered by name on the server, per the Plan 1 review lesson about database-wide
    # reads: a reader may look up the index behind their own query, not survey the server.
    (
        "show-indexes",
        "SHOW INDEXES YIELD name, state WHERE name STARTS WITH 'chunk' RETURN name, state",
    ),
    (
        "show-constraints",
        "SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'chunk' RETURN name",
    ),
    # `LIMIT` cannot be appended to either of these; `inject_limit` leaves them alone and
    # `run_cypher` bounds the row list instead.
    (
        "union-read",
        "MATCH (d:Document) RETURN d.key AS key LIMIT 3 "
        "UNION MATCH (w:WorkItem) RETURN w.key AS key LIMIT 3",
    ),
    ("finish", "MATCH (c:Chunk) WHERE c.id IS NOT NULL FINISH"),
    # Cypher's own namespaced value functions: dotted, allowlisted by root, side-effect free.
    (
        "builtin-duration-function",
        "MATCH (w:WorkItem {key: $key})-[:HAS_CHANGE]->(s:StatusChange) "
        "RETURN duration.inDays(w.created, s.at).days AS days ORDER BY days LIMIT 3",
    ),
)
