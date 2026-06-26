"""Tests for the v1 -> v2 storage migration in token_store._migrate_storage_v1_to_v2."""

from __future__ import annotations

from custom_components.atm.const import (
    CAP_ALLOW,
    CAP_DENY,
    PERSONA_CUSTOM,
)
from custom_components.atm.token_store import (
    _LEGACY_ALLOW_TO_CAP,
    _migrate_storage_v1_to_v2,
)


def _legacy_token() -> dict:
    return {
        "id": "tok-1",
        "name": "legacy",
        "token_hash": "x",
        "created_at": "2025-01-01T00:00:00+00:00",
        "created_by": "admin",
        "allow_restart": True,
        "allow_config_read": True,
        "allow_template_render": False,
        "allow_automation_write": False,
        "allow_script_write": False,
        "allow_physical_control": True,
        "allow_service_response": False,
        "allow_broadcast": False,
        "allow_log_read": True,
    }


class TestMigrationOnEmpty:
    def test_empty_dict_returns_false(self):
        assert _migrate_storage_v1_to_v2({}) is False

    def test_no_pending_approvals_then_added(self):
        raw = {"tokens": [], "archived_tokens": [], "settings": {}}
        changed = _migrate_storage_v1_to_v2(raw)
        assert changed is True
        assert raw["pending_approvals"] == []


class TestTokenMigration:
    def test_renames_allow_to_cap(self):
        raw = {"tokens": [_legacy_token()]}
        _migrate_storage_v1_to_v2(raw)
        token = raw["tokens"][0]
        # The v1 -> v2 migration owns only the legacy allow_* caps; caps added
        # after v2 are default-filled by TokenRecord.from_dict at load, not here.
        for cap in _LEGACY_ALLOW_TO_CAP.values():
            assert cap in token

    def test_true_becomes_allow(self):
        raw = {"tokens": [_legacy_token()]}
        _migrate_storage_v1_to_v2(raw)
        token = raw["tokens"][0]
        assert token["cap_restart"] == CAP_ALLOW
        assert token["cap_config_read"] == CAP_ALLOW
        assert token["cap_physical_control"] == CAP_ALLOW
        assert token["cap_log_read"] == CAP_ALLOW

    def test_false_becomes_deny(self):
        raw = {"tokens": [_legacy_token()]}
        _migrate_storage_v1_to_v2(raw)
        token = raw["tokens"][0]
        assert token["cap_template_render"] == CAP_DENY
        assert token["cap_automation_write"] == CAP_DENY
        assert token["cap_script_write"] == CAP_DENY
        assert token["cap_service_response"] == CAP_DENY
        assert token["cap_broadcast"] == CAP_DENY

    def test_persona_defaults_to_custom(self):
        raw = {"tokens": [_legacy_token()]}
        _migrate_storage_v1_to_v2(raw)
        assert raw["tokens"][0]["persona"] == PERSONA_CUSTOM

    def test_old_keys_dropped(self):
        raw = {"tokens": [_legacy_token()]}
        _migrate_storage_v1_to_v2(raw)
        token = raw["tokens"][0]
        for old_key in (
            "allow_restart", "allow_config_read", "allow_template_render",
            "allow_automation_write", "allow_script_write",
            "allow_physical_control", "allow_service_response",
            "allow_broadcast", "allow_log_read",
        ):
            assert old_key not in token

    def test_returns_true_when_migration_applied(self):
        raw = {"tokens": [_legacy_token()]}
        assert _migrate_storage_v1_to_v2(raw) is True


class TestMixedState:
    def test_already_migrated_token_is_left_alone(self):
        raw = {
            "tokens": [{
                "id": "tok-1",
                "name": "modern",
                "token_hash": "x",
                "created_at": "2025-01-01T00:00:00+00:00",
                "created_by": "admin",
                "cap_restart": "allow",
                "persona": "voice_assistant",
            }],
            "pending_approvals": [],
        }
        changed = _migrate_storage_v1_to_v2(raw)
        token = raw["tokens"][0]
        assert token["cap_restart"] == "allow"
        assert token["persona"] == "voice_assistant"
        # No new fields needed -> no change.
        assert changed is False

    def test_only_some_legacy_keys_present(self):
        # Partial legacy state: only one allow_ field present.
        raw = {
            "tokens": [{
                "id": "tok-1",
                "name": "partial",
                "token_hash": "x",
                "created_at": "2025-01-01T00:00:00+00:00",
                "created_by": "admin",
                "allow_restart": True,
            }],
        }
        changed = _migrate_storage_v1_to_v2(raw)
        assert changed is True
        token = raw["tokens"][0]
        assert token["cap_restart"] == "allow"
        assert "allow_restart" not in token
        assert token["persona"] == PERSONA_CUSTOM


class TestArchivedTokens:
    def test_legacy_keys_dropped_from_archives(self):
        # Spec says archived records do not retain capability flags;
        # if a legacy archive carried them, the migration drops them.
        raw = {
            "tokens": [],
            "archived_tokens": [{
                "id": "arc-1",
                "name": "old",
                "token_hash": "x",
                "created_at": "2025-01-01T00:00:00+00:00",
                "created_by": "admin",
                "revoked_at": "2025-02-01T00:00:00+00:00",
                "allow_restart": True,
                "allow_config_read": True,
            }],
        }
        _migrate_storage_v1_to_v2(raw)
        archived = raw["archived_tokens"][0]
        for old_key in ("allow_restart", "allow_config_read"):
            assert old_key not in archived
        # Capability migration is NOT applied to archives — they don't carry caps.
        assert "cap_restart" not in archived
