"""Smoke tests for the vendored mesa-core package.

These guard the vendoring mechanics (scripts/sync_mesa_core.py): the copy must
import cleanly under its rewritten path and must NOT pull in a top-level
``mesa_core`` module (which would indicate an unrewritten absolute import that
could collide with a future PyPI install).
"""

from __future__ import annotations

import importlib
import sys


def test_vendored_package_imports_with_pinned_version():
    from custom_components.atm import mesa_core

    assert mesa_core.__version__ == "1.0.0"


def test_no_top_level_mesa_core_leak():
    # Importing the vendored package must not register a bare ``mesa_core``
    # module: every internal import is rewritten to the vendored prefix.
    importlib.import_module("custom_components.atm.mesa_core")

    assert "mesa_core" not in sys.modules


def test_core_public_api_round_trips():
    from custom_components.atm.mesa_core import ControlMode, SemanticProfile

    profile = SemanticProfile.from_dict(
        "light.kitchen",
        {
            "semantic_profile": {
                "semantic_tags": ["lighting.ambient"],
                "operational_boundaries": {"control_mode": "autonomous"},
            },
            "privacy_classification": {"level": "normal"},
        },
    )
    assert profile.entity_id == "light.kitchen"
    assert profile.operational_boundaries.control_mode is ControlMode.AUTONOMOUS

    restored = SemanticProfile.from_dict("light.kitchen", profile.to_dict())
    assert restored.operational_boundaries.control_mode is ControlMode.AUTONOMOUS


def test_mcp_tool_registry_available():
    # The four retrieval tools register through the dict adapter that ATM reuses.
    from custom_components.atm.mesa_core.backends import MemoryBackend
    from custom_components.atm.mesa_core.mcp.adapters import DictToolRegistry
    from custom_components.atm.mesa_core.mcp.tools import register_mesa_tools
    from custom_components.atm.mesa_core.store import ProfileStore

    registry = DictToolRegistry()
    register_mesa_tools(ProfileStore(backend=MemoryBackend()), adapter=registry)
    assert "mesa_query_profiles" in registry.tools
    assert "mesa_get_profile" in registry.tools
