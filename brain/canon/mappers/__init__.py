"""One mapper per source into the five canonical types (spec §2.2, §3.2).

Each module exports a single `map_*(raw_records) -> Bundle`; `brain.canon.runner` is what
knows which sources exist. Nothing is imported here, so a mapper can be read, tested and
changed without dragging the other two (and their dependencies) along.
"""
