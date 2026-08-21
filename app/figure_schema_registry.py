"""Backward-compatible figure schema API.

New code should import from :mod:`app.capabilities.catalog`. Keeping this facade
prevents stored plans and external callers from breaking during the migration.
"""

from .capabilities.builtin.materials import (
    ADDITIONAL_SCHEMA_KINDS,
    FIRST_BATCH_SCHEMA_KINDS,
    MATERIAL_SCHEMA_KINDS,
)
from .capabilities.catalog import get_schema, registry_snapshot, schema_prompt_catalog

__all__ = [
    "ADDITIONAL_SCHEMA_KINDS",
    "FIRST_BATCH_SCHEMA_KINDS",
    "MATERIAL_SCHEMA_KINDS",
    "get_schema",
    "registry_snapshot",
    "schema_prompt_catalog",
]
