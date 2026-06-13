"""Explicit schema version migration (Spec Section 23).

Profiles are never silently migrated or rewritten: this utility runs only when
the operator requests it, returns a migrated copy, and logs every
transformation applied. In v1 only schema version 1.0 exists, so the only
transformation is stamping a missing ``schema_version``.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from custom_components.atm.mesa_core.exceptions import MesaError

logger = logging.getLogger("mesa_core.migration")

CURRENT_SCHEMA_VERSION = "1.0"


def _parse_version(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as err:
        raise MesaError(f"unparseable schema_version: {version!r}") from err


def migrate_profile(
    profile: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION
) -> dict[str, Any]:
    """Migrate a profile document to ``target_version``, returning a copy.

    The original document is never modified. Raises MesaError when no
    migration path exists.
    """
    migrated = copy.deepcopy(profile)
    sp = migrated.get("semantic_profile")
    if not isinstance(sp, dict):
        raise MesaError("document has no semantic_profile object to migrate")

    source_version = sp.get("schema_version", CURRENT_SCHEMA_VERSION)
    source = _parse_version(str(source_version))
    target = _parse_version(target_version)

    if source == target:
        if "schema_version" not in sp:
            sp["schema_version"] = target_version
            logger.info(
                "migration: stamped missing schema_version as %s", target_version
            )
        return migrated

    # Future minor/major migrations register their transformation steps here.
    raise MesaError(
        f"no migration path from schema version {source_version} to {target_version}"
    )
