"""Developer profile import from integration sidecar files (Spec Section 8).

``import_from_integration`` reads ``mesa_profile.json`` from an integration
directory. Profiles that omit ``metadata_origin`` are stamped
``source: developer``, the location-based provenance default of Spec 5.3: the
file ships inside the integration directory and is under the developer's
control. An explicit ``metadata_origin`` in the file always wins.

Requires filesystem access to the integration directories; hosts running on a
separate machine from HA cannot use this import path.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.atm.mesa_core.profile import MetadataOrigin, SemanticProfile

SIDECAR_FILENAME = "mesa_profile.json"


def import_from_integration(integration_path: str | Path) -> SemanticProfile | None:
    """Load the developer profile shipped with one integration.

    Returns a SemanticProfile with ``inheritance_scope: domain`` keyed by the
    integration directory name, or None when no sidecar file exists. Raises
    MesaValidationError for malformed sidecar content.

    Host servers call this at startup for each installed integration and write
    the result to the ProfileStore::

        profile = import_from_integration(path)
        if profile is not None:
            store.set_domain_profile(profile.entity_id, profile)
    """
    path = Path(integration_path)
    sidecar = path / SIDECAR_FILENAME
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text())
    profile = SemanticProfile.from_dict(
        path.name, data, default_origin=MetadataOrigin.DEVELOPER
    )
    profile.inheritance_scope = "domain"
    return profile
