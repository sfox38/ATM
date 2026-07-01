"""Hardcoded persona presets for ATM tokens.

Personas seed the capability matrix when an admin selects them. After applying,
the admin may override individual caps; the token's persona field records
which preset was applied for display purposes only and is not enforced.

Adding a new capability requires extending every persona in PERSONA_DEFINITIONS
to include a value for it (or to explicitly inherit a default).
"""

from __future__ import annotations

from .const import (
    CAP_ALLOW,
    CAP_CONFIRM,
    CAP_DENY,
    CAPABILITY_NAMES,
    PERSONA_AUTOMATION_BUILDER,
    PERSONA_CUSTOM,
    PERSONA_DASHBOARD_DESIGNER,
    PERSONA_HOME_ADMIN,
    PERSONA_MAINTENANCE,
    PERSONA_NEW_USER,
    PERSONA_POWER_USER,
    PERSONA_READ_ONLY,
    PERSONA_VOICE_ASSISTANT,
)

PERSONA_DEFINITIONS: dict[str, dict[str, str]] = {
    PERSONA_NEW_USER: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_DENY,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_DENY,
        "cap_diagnostics": CAP_DENY,
        "cap_broadcast": CAP_DENY,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_DENY,
        "cap_script_write": CAP_DENY,
        "cap_scene_write": CAP_DENY,
        "cap_helper_write": CAP_DENY,
        "cap_physical_control": CAP_CONFIRM,
        "cap_restart": CAP_DENY,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_DENY,
        "cap_registry_write": CAP_DENY,
        "cap_backup": CAP_DENY,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_READ_ONLY: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_ALLOW,
        "cap_diagnostics": CAP_ALLOW,
        "cap_broadcast": CAP_DENY,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_DENY,
        "cap_script_write": CAP_DENY,
        "cap_scene_write": CAP_DENY,
        "cap_helper_write": CAP_DENY,
        "cap_physical_control": CAP_DENY,
        "cap_restart": CAP_DENY,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_DENY,
        "cap_registry_write": CAP_DENY,
        "cap_backup": CAP_DENY,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_VOICE_ASSISTANT: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_DENY,
        "cap_diagnostics": CAP_DENY,
        "cap_broadcast": CAP_ALLOW,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_DENY,
        "cap_script_write": CAP_DENY,
        "cap_scene_write": CAP_DENY,
        "cap_helper_write": CAP_DENY,
        "cap_physical_control": CAP_CONFIRM,
        "cap_restart": CAP_DENY,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_DENY,
        "cap_registry_write": CAP_DENY,
        "cap_backup": CAP_DENY,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_AUTOMATION_BUILDER: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_ALLOW,
        "cap_diagnostics": CAP_ALLOW,
        "cap_broadcast": CAP_ALLOW,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_ALLOW,
        "cap_script_write": CAP_ALLOW,
        "cap_scene_write": CAP_ALLOW,
        "cap_helper_write": CAP_ALLOW,
        "cap_physical_control": CAP_CONFIRM,
        "cap_restart": CAP_CONFIRM,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_DENY,
        "cap_registry_write": CAP_DENY,
        "cap_backup": CAP_DENY,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_POWER_USER: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_ALLOW,
        "cap_diagnostics": CAP_ALLOW,
        "cap_broadcast": CAP_ALLOW,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_ALLOW,
        "cap_script_write": CAP_ALLOW,
        "cap_scene_write": CAP_ALLOW,
        "cap_helper_write": CAP_ALLOW,
        "cap_physical_control": CAP_CONFIRM,
        "cap_restart": CAP_ALLOW,
        "cap_integration_write": CAP_CONFIRM,
        "cap_lovelace_write": CAP_CONFIRM,
        "cap_registry_write": CAP_CONFIRM,
        "cap_backup": CAP_CONFIRM,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_HOME_ADMIN: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_ALLOW,
        "cap_diagnostics": CAP_ALLOW,
        "cap_broadcast": CAP_ALLOW,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_ALLOW,
        "cap_script_write": CAP_ALLOW,
        "cap_scene_write": CAP_ALLOW,
        "cap_helper_write": CAP_ALLOW,
        "cap_physical_control": CAP_CONFIRM,
        "cap_restart": CAP_CONFIRM,
        "cap_integration_write": CAP_CONFIRM,
        "cap_lovelace_write": CAP_CONFIRM,
        "cap_registry_write": CAP_CONFIRM,
        "cap_backup": CAP_CONFIRM,
        "cap_filesystem": CAP_CONFIRM,
        "cap_yaml_edit": CAP_CONFIRM,
    },
    PERSONA_DASHBOARD_DESIGNER: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_DENY,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_DENY,
        "cap_diagnostics": CAP_DENY,
        "cap_broadcast": CAP_DENY,
        "cap_service_response": CAP_DENY,
        "cap_automation_write": CAP_DENY,
        "cap_script_write": CAP_DENY,
        "cap_scene_write": CAP_DENY,
        "cap_helper_write": CAP_DENY,
        "cap_physical_control": CAP_DENY,
        "cap_restart": CAP_DENY,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_ALLOW,
        "cap_registry_write": CAP_DENY,
        "cap_backup": CAP_DENY,
        "cap_filesystem": CAP_CONFIRM,
        "cap_yaml_edit": CAP_DENY,
    },
    PERSONA_MAINTENANCE: {
        "cap_config_read": CAP_ALLOW,
        "cap_template_render": CAP_ALLOW,
        "cap_log_read": CAP_ALLOW,
        "cap_search": CAP_ALLOW,
        "cap_registry_read": CAP_ALLOW,
        "cap_traces": CAP_ALLOW,
        "cap_diagnostics": CAP_ALLOW,
        "cap_broadcast": CAP_DENY,
        "cap_service_response": CAP_ALLOW,
        "cap_automation_write": CAP_DENY,
        "cap_script_write": CAP_DENY,
        "cap_scene_write": CAP_DENY,
        "cap_helper_write": CAP_DENY,
        "cap_physical_control": CAP_DENY,
        "cap_restart": CAP_CONFIRM,
        "cap_integration_write": CAP_DENY,
        "cap_lovelace_write": CAP_DENY,
        "cap_registry_write": CAP_CONFIRM,
        "cap_backup": CAP_ALLOW,
        "cap_filesystem": CAP_DENY,
        "cap_yaml_edit": CAP_DENY,
    },
}

PERSONA_DESCRIPTIONS: dict[str, str] = {
    PERSONA_NEW_USER: "Gentle starter for first-time setup. Reads state and can control the devices you grant; locks, alarms, and covers require admin confirmation.",
    PERSONA_READ_ONLY: "Observer. Reads state, history, logs, templates. No actions, no broadcast.",
    PERSONA_VOICE_ASSISTANT: "Everyday assistant. Reads + service calls + broadcast. Locks, alarms, and covers require admin confirmation.",
    PERSONA_AUTOMATION_BUILDER: "Editor. Everything voice_assistant has, plus full discovery (traces, diagnostics) and automation, script, scene, and helper CRUD. Restart and physical actions require confirmation.",
    PERSONA_POWER_USER: "Trusted operator. Full reads and config writes, restart allowed. Physical actions, integration toggles, dashboards, backups, and entity-registry edits require confirmation; filesystem and raw YAML stay off.",
    PERSONA_HOME_ADMIN: "Whole-home administrator with a safety net. All reads and config writes allowed; restart, physical actions, integrations, dashboards, backups, entity-registry edits, filesystem, and raw YAML all require admin confirmation.",
    PERSONA_DASHBOARD_DESIGNER: "Dashboard builder. Reads and discovers entities and creates, edits, and deletes Lovelace dashboards; filesystem access (theme and custom-card assets) requires confirmation. No device control, config authoring, or registry edits.",
    PERSONA_MAINTENANCE: "Upkeep and registry hygiene. Full reads and diagnostics, creates backups; restart and entity-registry edits (set name, area, or icon, and delete stale entries) require confirmation. No device control, config authoring, or dashboards.",
    PERSONA_CUSTOM: "Custom configuration. Each capability set individually.",
}


def _validate_definitions() -> None:
    """Ensure every persona defines a value for every capability.

    Called at import time so missing entries fail fast in development.
    """
    expected = set(CAPABILITY_NAMES)
    for name, mapping in PERSONA_DEFINITIONS.items():
        missing = expected - mapping.keys()
        extra = mapping.keys() - expected
        if missing:
            raise RuntimeError(
                f"Persona {name!r} is missing capabilities: {sorted(missing)}"
            )
        if extra:
            raise RuntimeError(
                f"Persona {name!r} references unknown capabilities: {sorted(extra)}"
            )


_validate_definitions()


def get_persona_caps(persona: str) -> dict[str, str] | None:
    """Return the cap_*->mode mapping for a named persona, or None for custom/unknown."""
    if persona == PERSONA_CUSTOM:
        return None
    return PERSONA_DEFINITIONS.get(persona)


def matches_persona(token_caps: dict[str, str], persona: str) -> bool:
    """Check whether a token's current cap values exactly match a persona's defaults."""
    expected = get_persona_caps(persona)
    if expected is None:
        return False
    return all(token_caps.get(cap) == mode for cap, mode in expected.items())


def detect_persona(token_caps: dict[str, str]) -> str:
    """Identify which persona (if any) a token's current caps match.

    Returns PERSONA_CUSTOM when the cap values do not exactly match any preset.
    Useful for the frontend to show "Custom (was: voice_assistant)" labels.
    """
    for name in (
        PERSONA_NEW_USER,
        PERSONA_READ_ONLY,
        PERSONA_VOICE_ASSISTANT,
        PERSONA_DASHBOARD_DESIGNER,
        PERSONA_MAINTENANCE,
        PERSONA_AUTOMATION_BUILDER,
        PERSONA_POWER_USER,
        PERSONA_HOME_ADMIN,
    ):
        if matches_persona(token_caps, name):
            return name
    return PERSONA_CUSTOM
