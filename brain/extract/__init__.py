"""`brain extract` — schema-guided extraction (spec 3.5, step brief 07).

`build` turns Phase A chunks into batch inputs the `kg-extractor` agents read; `merge` is
the only writer of what they produce into Neo4j. The agents never touch the database.
"""
