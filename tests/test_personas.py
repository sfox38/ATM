"""Tests for the personas module."""

from __future__ import annotations

from custom_components.atm.const import (
    CAP_ALLOW,
    CAP_CONFIRM,
    CAP_DENY,
    CAPABILITY_NAMES,
    CONFIRM_AVAILABLE_CAPS,
    PERSONA_AUTOMATION_BUILDER,
    PERSONA_CUSTOM,
    PERSONA_DASHBOARD_DESIGNER,
    PERSONA_MAINTENANCE,
    PERSONA_NAMES,
    PERSONA_NEW_USER,
    PERSONA_POWER_USER,
    PERSONA_READ_ONLY,
    PERSONA_VOICE_ASSISTANT,
)
from custom_components.atm.personas import (
    PERSONA_DEFINITIONS,
    PERSONA_DESCRIPTIONS,
    detect_persona,
    get_persona_caps,
    matches_persona,
)


def test_all_personas_in_persona_names():
    """Every persona referenced in PERSONA_NAMES must have a definition or be custom."""
    expected_named = PERSONA_NAMES - {PERSONA_CUSTOM}
    assert set(PERSONA_DEFINITIONS.keys()) == expected_named


def test_every_persona_covers_every_cap():
    """A missing cap on a persona is a bug; this test asserts the import-time invariant."""
    expected = set(CAPABILITY_NAMES)
    for name, mapping in PERSONA_DEFINITIONS.items():
        assert set(mapping.keys()) == expected, f"persona {name} cap set mismatch"


def test_persona_values_are_valid_modes():
    """Every persona value must be one of deny/allow/confirm."""
    valid = {CAP_DENY, CAP_ALLOW, CAP_CONFIRM}
    for name, mapping in PERSONA_DEFINITIONS.items():
        for cap, mode in mapping.items():
            assert mode in valid, f"persona {name} cap {cap} has invalid mode {mode!r}"


def test_confirm_only_used_on_confirm_eligible_caps():
    """A persona must not set confirm on a cap that does not support it."""
    for name, mapping in PERSONA_DEFINITIONS.items():
        for cap, mode in mapping.items():
            if mode == CAP_CONFIRM:
                assert cap in CONFIRM_AVAILABLE_CAPS, (
                    f"persona {name} sets {cap} to confirm but cap does not support it"
                )


def test_every_persona_has_description():
    expected = set(PERSONA_NAMES)
    assert set(PERSONA_DESCRIPTIONS.keys()) == expected


class TestReadOnly:
    def test_no_writes(self):
        caps = get_persona_caps(PERSONA_READ_ONLY)
        assert caps["cap_automation_write"] == CAP_DENY
        assert caps["cap_script_write"] == CAP_DENY
        assert caps["cap_restart"] == CAP_DENY
        assert caps["cap_physical_control"] == CAP_DENY

    def test_reads_allowed(self):
        caps = get_persona_caps(PERSONA_READ_ONLY)
        assert caps["cap_config_read"] == CAP_ALLOW
        assert caps["cap_log_read"] == CAP_ALLOW
        assert caps["cap_template_render"] == CAP_ALLOW

    def test_broadcast_denied(self):
        caps = get_persona_caps(PERSONA_READ_ONLY)
        assert caps["cap_broadcast"] == CAP_DENY


class TestVoiceAssistant:
    def test_physical_is_confirm(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert caps["cap_physical_control"] == CAP_CONFIRM

    def test_no_writes(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert caps["cap_automation_write"] == CAP_DENY
        assert caps["cap_script_write"] == CAP_DENY
        assert caps["cap_restart"] == CAP_DENY

    def test_broadcast_allowed(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert caps["cap_broadcast"] == CAP_ALLOW


class TestAutomationBuilder:
    def test_writes_allowed(self):
        caps = get_persona_caps(PERSONA_AUTOMATION_BUILDER)
        assert caps["cap_automation_write"] == CAP_ALLOW
        assert caps["cap_script_write"] == CAP_ALLOW

    def test_restart_is_confirm(self):
        caps = get_persona_caps(PERSONA_AUTOMATION_BUILDER)
        assert caps["cap_restart"] == CAP_CONFIRM

    def test_physical_is_confirm(self):
        caps = get_persona_caps(PERSONA_AUTOMATION_BUILDER)
        assert caps["cap_physical_control"] == CAP_CONFIRM


class TestPowerUser:
    def test_restart_allowed(self):
        caps = get_persona_caps(PERSONA_POWER_USER)
        assert caps["cap_restart"] == CAP_ALLOW

    def test_physical_remains_confirm(self):
        """Power user gets full reads/writes/restart but physical control still gates.
        Door locks and alarm changes are uniquely high-cost surprises.
        """
        caps = get_persona_caps(PERSONA_POWER_USER)
        assert caps["cap_physical_control"] == CAP_CONFIRM


class TestDashboardDesigner:
    def test_dashboard_write_allowed(self):
        caps = get_persona_caps(PERSONA_DASHBOARD_DESIGNER)
        assert caps["cap_lovelace_write"] == CAP_ALLOW

    def test_filesystem_is_confirm(self):
        caps = get_persona_caps(PERSONA_DASHBOARD_DESIGNER)
        assert caps["cap_filesystem"] == CAP_CONFIRM

    def test_no_device_control_or_config_authoring(self):
        caps = get_persona_caps(PERSONA_DASHBOARD_DESIGNER)
        assert caps["cap_physical_control"] == CAP_DENY
        assert caps["cap_automation_write"] == CAP_DENY
        assert caps["cap_script_write"] == CAP_DENY


class TestMaintenance:
    def test_backup_allowed(self):
        caps = get_persona_caps(PERSONA_MAINTENANCE)
        assert caps["cap_backup"] == CAP_ALLOW

    def test_restart_is_confirm(self):
        caps = get_persona_caps(PERSONA_MAINTENANCE)
        assert caps["cap_restart"] == CAP_CONFIRM

    def test_diagnostics_allowed_no_authoring(self):
        caps = get_persona_caps(PERSONA_MAINTENANCE)
        assert caps["cap_diagnostics"] == CAP_ALLOW
        assert caps["cap_automation_write"] == CAP_DENY
        assert caps["cap_lovelace_write"] == CAP_DENY


class TestGetPersonaCaps:
    def test_returns_none_for_custom(self):
        assert get_persona_caps(PERSONA_CUSTOM) is None

    def test_returns_none_for_unknown(self):
        assert get_persona_caps("not_a_persona") is None

    def test_returns_dict_for_named(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert isinstance(caps, dict)
        assert len(caps) == len(CAPABILITY_NAMES)


class TestMatchesPersona:
    def test_exact_match(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert matches_persona(caps, PERSONA_VOICE_ASSISTANT) is True

    def test_one_diverged_cap_returns_false(self):
        caps = dict(get_persona_caps(PERSONA_VOICE_ASSISTANT))
        caps["cap_automation_write"] = CAP_ALLOW  # diverged
        assert matches_persona(caps, PERSONA_VOICE_ASSISTANT) is False

    def test_custom_never_matches(self):
        caps = get_persona_caps(PERSONA_VOICE_ASSISTANT)
        assert matches_persona(caps, PERSONA_CUSTOM) is False


class TestDetectPersona:
    def test_round_trip_each_persona(self):
        # Every named preset must be recognized from its own caps, otherwise
        # applying it would silently flip the token to "custom".
        for name in PERSONA_DEFINITIONS:
            caps = get_persona_caps(name)
            assert detect_persona(caps) == name, f"{name} did not round-trip"

    def test_new_user_is_detected_not_custom(self):
        # new_user must round-trip through persona detection.
        assert detect_persona(get_persona_caps(PERSONA_NEW_USER)) == PERSONA_NEW_USER

    def test_returns_custom_for_unmatched(self):
        all_allow = {cap: CAP_ALLOW for cap in CAPABILITY_NAMES}
        # all-allow is power_user EXCEPT physical_control which is confirm there.
        # So all-allow is custom.
        assert detect_persona(all_allow) == PERSONA_CUSTOM

    def test_returns_custom_for_empty(self):
        assert detect_persona({}) == PERSONA_CUSTOM
