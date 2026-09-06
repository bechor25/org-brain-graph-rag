"""Utilities more than one pipeline step needs.

Nothing task-specific lives here: a module earns a place only once a second step imports
it. `jsonschema_mini` moved here when `brain extract` became its second caller.
"""
