"""Tests for token_store.py."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.util.dt import utcnow

from custom_components.atm.const import (
    CAP_ALLOW,
    CAP_DENY,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    TOKEN_LENGTH,
    TOKEN_PREFIX,
)
from custom_components.atm.token_store import (
    ArchivedTokenRecord,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
    TokenRecord,
    TokenStore,
    token_name_slug as _slugify,
    hmac_compare,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# PermissionNode
# ---------------------------------------------------------------------------


class TestPermissionNode:
    def test_default_state_is_grey(self):
        node = PermissionNode()
        assert node.state == "GREY"
        assert node.hint is None

    def test_round_trip(self):
        node = PermissionNode(state="GREEN", hint="Main light")
        assert PermissionNode.from_dict(node.to_dict()) == node

    def test_from_dict_missing_fields(self):
        node = PermissionNode.from_dict({})
        assert node.state == "GREY"
        assert node.hint is None


# ---------------------------------------------------------------------------
# PermissionTree
# ---------------------------------------------------------------------------


class TestPermissionTree:
    def test_empty_defaults(self):
        tree = PermissionTree()
        assert tree.domains == {}
        assert tree.devices == {}
        assert tree.entities == {}

    def test_round_trip(self):
        tree = PermissionTree(
            domains={"light": PermissionNode(state="GREEN")},
            entities={"sensor.temp": PermissionNode(state="YELLOW", hint="bedroom temp")},
        )
        restored = PermissionTree.from_dict(tree.to_dict())
        assert restored.domains["light"].state == "GREEN"
        assert restored.entities["sensor.temp"].hint == "bedroom temp"

    def test_from_dict_empty(self):
        tree = PermissionTree.from_dict({})
        assert tree.domains == {}


# ---------------------------------------------------------------------------
# TokenRecord
# ---------------------------------------------------------------------------


class TestTokenRecord:
    def _make(self, **kwargs):
        defaults = {
            "id": "test-uuid",
            "name": "test",
            "token_hash": "abc",
            "created_at": utcnow(),
            "created_by": "user1",
        }
        defaults.update(kwargs)
        return TokenRecord(**defaults)

    def test_is_valid_active(self):
        token = self._make()
        assert token.is_valid() is True

    def test_is_valid_revoked(self):
        token = self._make(revoked=True)
        assert token.is_valid() is False

    def test_is_expired_no_expiry(self):
        token = self._make()
        assert token.is_expired() is False

    def test_is_expired_past(self):
        token = self._make(expires_at=utcnow() - timedelta(seconds=1))
        assert token.is_expired() is True
        assert token.is_valid() is False

    def test_is_expired_future(self):
        token = self._make(expires_at=utcnow() + timedelta(days=1))
        assert token.is_expired() is False

    def test_round_trip(self):
        token = self._make(
            expires_at=utcnow() + timedelta(days=7),
            pass_through=True,
            cap_restart="allow",
            permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
        )
        restored = TokenRecord.from_dict(token.to_storage_dict())
        assert restored.id == token.id
        assert restored.name == token.name
        assert restored.pass_through is True
        assert restored.cap_restart == "allow"
        assert restored.permissions.domains["light"].state == "GREEN"

    def test_from_dict_defaults(self):
        token = TokenRecord.from_dict({
            "id": "x",
            "name": "x",
            "token_hash": "x",
            "created_at": utcnow().isoformat(),
            "created_by": "user1",
        })
        assert token.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert token.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST
        assert token.revoked is False
        assert token.pass_through is False
        assert token.announce_all_tools is False

    def test_from_dict_fills_new_caps_to_deny(self):
        # A token persisted before the capability expansion carries only the
        # original caps. Loading it must default every newly-added cap to deny
        # (the no-storage-bump upgrade path), never raise or leave it unset.
        token = TokenRecord.from_dict({
            "id": "x",
            "name": "x",
            "token_hash": "x",
            "created_at": utcnow().isoformat(),
            "created_by": "user1",
            "cap_config_read": CAP_ALLOW,
            "cap_automation_write": CAP_ALLOW,
        })
        assert token.cap_config_read == CAP_ALLOW
        assert token.cap_automation_write == CAP_ALLOW
        for cap_name in (
            "cap_search", "cap_registry_read", "cap_traces", "cap_diagnostics",
            "cap_scene_write", "cap_helper_write", "cap_integration_write",
            "cap_lovelace_write", "cap_backup", "cap_filesystem", "cap_yaml_edit",
        ):
            assert getattr(token, cap_name) == CAP_DENY

    def test_announce_all_tools_round_trip(self):
        token = self._make()
        token.announce_all_tools = True
        d = token.to_dict()
        assert d["announce_all_tools"] is True
        restored = TokenRecord.from_dict(token.to_storage_dict())
        assert restored.announce_all_tools is True


# ---------------------------------------------------------------------------
# ArchivedTokenRecord
# ---------------------------------------------------------------------------


class TestArchivedTokenRecord:
    def test_round_trip(self):
        now = utcnow()
        record = ArchivedTokenRecord(
            id="a", name="b", token_hash="c",
            created_at=now, created_by="u", revoked_at=now,
            revoked=True,
        )
        restored = ArchivedTokenRecord.from_dict(record.to_storage_dict())
        assert restored.id == "a"
        assert restored.revoked is True


# ---------------------------------------------------------------------------
# GlobalSettings
# ---------------------------------------------------------------------------


class TestGlobalSettings:
    def test_defaults(self):
        s = GlobalSettings()
        assert s.kill_switch is False
        assert s.log_allowed is True
        assert s.notify_on_rate_limit is False

    def test_round_trip(self):
        s = GlobalSettings(kill_switch=True, log_client_ip=False)
        restored = GlobalSettings.from_dict(s.to_dict())
        assert restored.kill_switch is True
        assert restored.log_client_ip is False

    def test_mesa_inject_default_off(self):
        assert GlobalSettings().mesa_inject_enabled is False

    def test_mesa_inject_round_trip(self):
        s = GlobalSettings(mesa_inject_enabled=True)
        assert GlobalSettings.from_dict(s.to_dict()).mesa_inject_enabled is True


# ---------------------------------------------------------------------------
# TokenStore - creation and loading
# ---------------------------------------------------------------------------


class TestTokenStoreLoad:
    async def test_empty_load(self, token_store):
        assert token_store.list_tokens() == []
        assert token_store.list_archived() == []

    async def test_load_with_existing_data(self, hass):
        now = utcnow()
        existing = {
            "version": 1,
            "tokens": [{
                "id": "tok1",
                "name": "mytoken",
                "token_hash": "hash1",
                "created_at": now.isoformat(),
                "created_by": "admin",
            }],
            "archived_tokens": [],
            "settings": {"kill_switch": True},
        }
        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=existing)
        mock_store.async_save = AsyncMock()

        with patch("custom_components.atm.token_store._ATMStore", return_value=mock_store):
            store = await TokenStore.async_create(hass)

        tokens = store.list_tokens()
        assert len(tokens) == 1
        assert tokens[0].name == "mytoken"
        assert store.get_settings().kill_switch is True


# ---------------------------------------------------------------------------
# TokenStore - token creation
# ---------------------------------------------------------------------------


class TestTokenCreation:
    async def test_create_returns_raw_token_and_record(self, token_store):
        record, raw = await token_store.async_create_token("mytoken", "user1")
        assert raw.startswith(TOKEN_PREFIX)
        assert len(raw) == TOKEN_LENGTH
        assert record.name == "mytoken"
        assert record.created_by == "user1"

    async def test_raw_token_not_stored(self, token_store):
        record, raw = await token_store.async_create_token("t1", "u")
        expected_hash = _sha256(raw)
        assert record.token_hash == expected_hash
        assert raw not in record.token_hash

    async def test_create_saves_immediately(self, token_store, mock_store):
        await token_store.async_create_token("t1", "u")
        mock_store.async_save.assert_called()

    async def test_create_with_expiry(self, token_store):
        expiry = utcnow() + timedelta(days=30)
        record, _ = await token_store.async_create_token("t1", "u", expires_at=expiry)
        assert record.expires_at == expiry

    async def test_create_pass_through(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u", pass_through=True)
        assert record.pass_through is True

    async def test_burst_coerced_to_zero_when_requests_zero(self, token_store):
        record, _ = await token_store.async_create_token(
            "t1", "u", rate_limit_requests=0, rate_limit_burst=10
        )
        assert record.rate_limit_burst == 0

    async def test_burst_preserved_when_requests_nonzero(self, token_store):
        record, _ = await token_store.async_create_token(
            "t1", "u", rate_limit_requests=30, rate_limit_burst=5
        )
        assert record.rate_limit_burst == 5

    async def test_default_rate_limits(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        assert record.rate_limit_requests == DEFAULT_RATE_LIMIT_REQUESTS
        assert record.rate_limit_burst == DEFAULT_RATE_LIMIT_BURST

    async def test_default_capability_flags_all_deny(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        assert record.cap_automation_write == "deny"
        assert record.cap_config_read == "deny"
        assert record.cap_template_render == "deny"
        assert record.cap_restart == "deny"
        assert record.persona == "custom"

    async def test_multiple_tokens_have_unique_ids(self, token_store):
        r1, _ = await token_store.async_create_token("t1", "u")
        r2, _ = await token_store.async_create_token("t2", "u")
        assert r1.id != r2.id

    async def test_multiple_tokens_have_unique_hashes(self, token_store):
        r1, _ = await token_store.async_create_token("t1", "u")
        r2, _ = await token_store.async_create_token("t2", "u")
        assert r1.token_hash != r2.token_hash


# ---------------------------------------------------------------------------
# TokenStore - lookup
# ---------------------------------------------------------------------------


class TestTokenLookup:
    async def test_get_by_id(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        found = token_store.get_token_by_id(record.id)
        assert found is not None
        assert found.id == record.id

    async def test_get_by_id_missing(self, token_store):
        assert token_store.get_token_by_id("nonexistent") is None

    async def test_get_by_hash(self, token_store):
        record, raw = await token_store.async_create_token("t1", "u")
        presented_hash = _sha256(raw)
        found = token_store.get_token_by_hash(presented_hash)
        assert found is not None
        assert found.id == record.id

    async def test_get_by_hash_wrong_value(self, token_store):
        await token_store.async_create_token("t1", "u")
        assert token_store.get_token_by_hash(_sha256("wrong")) is None

    async def test_get_archived_by_hash(self, token_store):
        record, raw = await token_store.async_create_token("t1", "u")
        await token_store.async_archive_token(record.id, revoked=True)
        presented_hash = _sha256(raw)
        found = token_store.get_archived_by_hash(presented_hash)
        assert found is not None
        assert found.id == record.id


# ---------------------------------------------------------------------------
# TokenStore - listing
# ---------------------------------------------------------------------------


class TestTokenListing:
    async def test_list_tokens_empty(self, token_store):
        assert token_store.list_tokens() == []

    async def test_list_tokens_contains_created(self, token_store):
        r1, _ = await token_store.async_create_token("t1", "u")
        r2, _ = await token_store.async_create_token("t2", "u")
        ids = {t.id for t in token_store.list_tokens()}
        assert r1.id in ids
        assert r2.id in ids

    async def test_active_token_count(self, token_store):
        assert token_store.active_token_count() == 0
        await token_store.async_create_token("t1", "u")
        assert token_store.active_token_count() == 1
        await token_store.async_create_token("t2", "u")
        assert token_store.active_token_count() == 2

    async def test_list_archived_empty(self, token_store):
        assert token_store.list_archived() == []


# ---------------------------------------------------------------------------
# TokenStore - archival
# ---------------------------------------------------------------------------


class TestTokenArchival:
    async def test_revoke_moves_to_archived(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_archive_token(record.id, revoked=True)
        assert token_store.get_token_by_id(record.id) is None
        archived = token_store.list_archived()
        assert len(archived) == 1
        assert archived[0].id == record.id
        assert archived[0].revoked is True

    async def test_expire_archived_with_revoked_false(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_archive_token(record.id, revoked=False)
        archived = token_store.list_archived()
        assert archived[0].revoked is False

    async def test_archive_saves_immediately(self, token_store, mock_store):
        mock_store.async_save.reset_mock()
        record, _ = await token_store.async_create_token("t1", "u")
        mock_store.async_save.reset_mock()
        await token_store.async_archive_token(record.id, revoked=True)
        mock_store.async_save.assert_called()

    async def test_archive_nonexistent_returns_none(self, token_store):
        result = await token_store.async_archive_token("no-such-id", revoked=True)
        assert result is None

    async def test_archive_retains_audit_fields(self, token_store):
        expiry = utcnow() + timedelta(days=1)
        record, _ = await token_store.async_create_token("t1", "u", expires_at=expiry)
        token_store.update_last_used(record.id, utcnow())
        archived = await token_store.async_archive_token(record.id, revoked=True)
        assert archived.name == "t1"
        assert archived.created_by == "u"
        assert archived.expires_at == expiry
        assert archived.last_used_at is not None

    async def test_permission_tree_not_in_archived(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        archived = await token_store.async_archive_token(record.id, revoked=True)
        assert not hasattr(archived, "permissions")


# ---------------------------------------------------------------------------
# TokenStore - patching
# ---------------------------------------------------------------------------


class TestTokenPatch:
    async def test_patch_pass_through(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        updated = await token_store.async_patch_token(record.id, pass_through=True)
        assert updated.pass_through is True

    async def test_patch_announce_all_tools(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        assert record.announce_all_tools is False
        updated = await token_store.async_patch_token(record.id, announce_all_tools=True)
        assert updated.announce_all_tools is True

    async def test_patch_rate_limit(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        updated = await token_store.async_patch_token(
            record.id, rate_limit_requests=30, rate_limit_burst=5
        )
        assert updated.rate_limit_requests == 30
        assert updated.rate_limit_burst == 5

    async def test_patch_rate_requests_zero_coerces_burst(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u", rate_limit_burst=10)
        updated = await token_store.async_patch_token(record.id, rate_limit_requests=0)
        assert updated.rate_limit_burst == 0

    async def test_patch_capability_flags(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        updated = await token_store.async_patch_token(
            record.id,
            cap_restart="allow",
            cap_config_read="allow",
            cap_automation_write="allow",
            cap_template_render="allow",
        )
        assert updated.cap_restart == "allow"
        assert updated.cap_config_read == "allow"
        assert updated.cap_automation_write == "allow"
        assert updated.cap_template_render == "allow"

    async def test_patch_cap_change_after_persona_drops_to_custom(self, token_store):
        """Applying a persona and then changing any cap should auto-switch persona to custom
        (or to another matching preset if the new state happens to match one).
        """
        from custom_components.atm.personas import get_persona_caps

        record, _ = await token_store.async_create_token("t1", "u")
        # Seed the voice_assistant persona.
        await token_store.async_patch_token(
            record.id,
            persona="voice_assistant",
            **get_persona_caps("voice_assistant"),
        )
        assert token_store.get_token_by_id(record.id).persona == "voice_assistant"

        # Override cap_automation_write (was deny in voice_assistant). Persona should re-derive.
        updated = await token_store.async_patch_token(
            record.id,
            cap_automation_write="allow",
        )
        assert updated.cap_automation_write == "allow"
        assert updated.persona == "custom"

    async def test_patch_cap_change_can_promote_to_matching_persona(self, token_store):
        """If post-change caps happen to match a different preset exactly, detect that preset."""
        from custom_components.atm.personas import get_persona_caps

        record, _ = await token_store.async_create_token("t1", "u")
        # Start from automation_builder.
        await token_store.async_patch_token(
            record.id,
            persona="automation_builder",
            **get_persona_caps("automation_builder"),
        )
        assert token_store.get_token_by_id(record.id).persona == "automation_builder"

        # Patch exactly the caps that differ between automation_builder and
        # power_user; the resulting set matches power_user, so the label promotes.
        ab = get_persona_caps("automation_builder")
        pu = get_persona_caps("power_user")
        diff = {cap: mode for cap, mode in pu.items() if ab.get(cap) != mode}
        updated = await token_store.async_patch_token(record.id, **diff)
        assert updated.persona == "power_user"

    async def test_patch_non_cap_change_does_not_touch_persona(self, token_store):
        """Patching only rate-limit fields should NOT trigger persona re-derivation."""
        from custom_components.atm.personas import get_persona_caps

        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_patch_token(
            record.id,
            persona="voice_assistant",
            **get_persona_caps("voice_assistant"),
        )
        updated = await token_store.async_patch_token(record.id, rate_limit_requests=30)
        assert updated.persona == "voice_assistant"
        assert updated.rate_limit_requests == 30

    async def test_patch_saves_immediately(self, token_store, mock_store):
        record, _ = await token_store.async_create_token("t1", "u")
        mock_store.async_save.reset_mock()
        await token_store.async_patch_token(record.id, cap_restart="allow")
        mock_store.async_save.assert_called()

    async def test_patch_nonexistent_returns_none(self, token_store):
        result = await token_store.async_patch_token("no-such-id", cap_restart="allow")
        assert result is None

    async def test_patch_applies_name_and_ignores_immutable(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_patch_token(record.id, name="renamed-x", created_by="hacker")
        fetched = token_store.get_token_by_id(record.id)
        assert fetched.name == "renamed-x"   # name is now mutable (rename)
        assert fetched.created_by == "u"     # created_by stays immutable (ignored)

    async def test_name_slug_exists_excludes_self(self, token_store):
        a, _ = await token_store.async_create_token("alpha", "u")
        await token_store.async_create_token("beta", "u")
        assert token_store.name_slug_exists("beta") is True
        assert token_store.name_slug_exists("beta", exclude_token_id=a.id) is True   # beta is a different token
        assert token_store.name_slug_exists("alpha", exclude_token_id=a.id) is False  # only match is self


# ---------------------------------------------------------------------------
# TokenStore - permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    async def test_set_full_permission_tree(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        tree = PermissionTree(
            domains={"light": PermissionNode(state="GREEN")},
            entities={"sensor.temp": PermissionNode(state="YELLOW", hint="temp sensor")},
        )
        updated = await token_store.async_set_permissions(record.id, tree)
        assert updated.permissions.domains["light"].state == "GREEN"
        assert updated.permissions.entities["sensor.temp"].hint == "temp sensor"

    async def test_patch_permission_node_set(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_patch_permission_node(
            record.id, "domains", "light", "GREEN", hint="ceiling lights"
        )
        token = token_store.get_token_by_id(record.id)
        assert token.permissions.domains["light"].state == "GREEN"
        assert token.permissions.domains["light"].hint == "ceiling lights"

    async def test_patch_permission_node_grey_removes_node(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_patch_permission_node(record.id, "domains", "light", "GREEN")
        await token_store.async_patch_permission_node(record.id, "domains", "light", "GREY")
        token = token_store.get_token_by_id(record.id)
        assert "light" not in token.permissions.domains

    async def test_patch_permission_node_invalid_type_returns_none(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        result = await token_store.async_patch_permission_node(
            record.id, "invalid_type", "light", "GREEN"
        )
        assert result is None

    async def test_permissions_save_immediately(self, token_store, mock_store):
        record, _ = await token_store.async_create_token("t1", "u")
        mock_store.async_save.reset_mock()
        await token_store.async_patch_permission_node(record.id, "domains", "light", "GREEN")
        mock_store.async_save.assert_called()

    async def test_set_permissions_nonexistent_returns_none(self, token_store):
        result = await token_store.async_set_permissions("no-such-id", PermissionTree())
        assert result is None


# ---------------------------------------------------------------------------
# TokenStore - last_used_at
# ---------------------------------------------------------------------------


class TestLastUsed:
    async def test_update_last_used_in_memory(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        ts = utcnow()
        token_store.update_last_used(record.id, ts)
        token = token_store.get_token_by_id(record.id)
        assert token.last_used_at == ts

    async def test_update_last_used_nonexistent_no_error(self, token_store):
        token_store.update_last_used("no-such-id", utcnow())

    async def test_flush_last_used_calls_save(self, token_store, mock_store):
        mock_store.async_save.reset_mock()
        await token_store.async_flush_last_used()
        mock_store.async_save.assert_called_once()


# ---------------------------------------------------------------------------
# TokenStore - archived deletion
# ---------------------------------------------------------------------------


class TestArchivedDeletion:
    async def test_delete_archived_removes_record(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_archive_token(record.id, revoked=True)
        result = await token_store.async_delete_archived(record.id)
        assert result is True
        assert token_store.list_archived() == []

    async def test_delete_archived_saves_immediately(self, token_store, mock_store):
        record, _ = await token_store.async_create_token("t1", "u")
        await token_store.async_archive_token(record.id, revoked=True)
        mock_store.async_save.reset_mock()
        await token_store.async_delete_archived(record.id)
        mock_store.async_save.assert_called()

    async def test_delete_archived_nonexistent_returns_false(self, token_store):
        result = await token_store.async_delete_archived("no-such-id")
        assert result is False


# ---------------------------------------------------------------------------
# TokenStore - settings
# ---------------------------------------------------------------------------


class TestSettings:
    async def test_get_settings_defaults(self, token_store):
        settings = token_store.get_settings()
        assert settings.kill_switch is False
        assert settings.log_allowed is True

    async def test_patch_settings(self, token_store):
        updated = await token_store.async_patch_settings(kill_switch=True, log_client_ip=False)
        assert updated.kill_switch is True
        assert updated.log_client_ip is False
        assert token_store.get_settings().kill_switch is True

    async def test_patch_settings_saves_immediately(self, token_store, mock_store):
        mock_store.async_save.reset_mock()
        await token_store.async_patch_settings(kill_switch=True)
        mock_store.async_save.assert_called()


# ---------------------------------------------------------------------------
# TokenStore - wipe
# ---------------------------------------------------------------------------


class TestWipe:
    async def test_wipe_clears_all(self, token_store):
        await token_store.async_create_token("t1", "u")
        await token_store.async_create_token("t2", "u")
        await token_store.async_patch_settings(kill_switch=True)
        await token_store.async_wipe()
        assert token_store.list_tokens() == []
        assert token_store.list_archived() == []
        assert token_store.get_settings().kill_switch is False

    async def test_wipe_saves_immediately(self, token_store, mock_store):
        mock_store.async_save.reset_mock()
        await token_store.async_wipe()
        mock_store.async_save.assert_called()


# ---------------------------------------------------------------------------
# TokenStore - slug uniqueness
# ---------------------------------------------------------------------------


class TestSlugUniqueness:
    async def test_same_name_slug_detected(self, token_store):
        await token_store.async_create_token("my-token", "u")
        assert token_store.name_slug_exists("my-token") is True
        assert token_store.name_slug_exists("my_token") is True

    async def test_different_name_not_collision(self, token_store):
        await token_store.async_create_token("my-token", "u")
        assert token_store.name_slug_exists("other-token") is False

    async def test_slugify(self):
        assert _slugify("my-token") == "my_token"
        assert _slugify("MyToken") == "mytoken"
        assert _slugify("my_token") == "my_token"


# ---------------------------------------------------------------------------
# hmac_compare
# ---------------------------------------------------------------------------


class TestHmacCompare:
    def test_equal_hashes(self):
        h = _sha256("atm_abc123")
        assert hmac_compare(h, h) is True

    def test_unequal_hashes(self):
        h1 = _sha256("atm_abc")
        h2 = _sha256("atm_xyz")
        assert hmac_compare(h1, h2) is False

    def test_empty_strings(self):
        assert hmac_compare("", "") is True

    def test_one_empty(self):
        assert hmac_compare("abc", "") is False


# ---------------------------------------------------------------------------
# Concurrent PATCH (async_lock)
# ---------------------------------------------------------------------------


class TestConcurrentPatch:
    async def test_async_lock_prevents_interleaving(self, token_store):
        record, _ = await token_store.async_create_token("t1", "u")
        results = []

        async def patch_and_record(value):
            async with token_store.async_lock:
                await token_store.async_patch_token(
                    record.id, rate_limit_requests=value
                )
                token = token_store.get_token_by_id(record.id)
                results.append(token.rate_limit_requests)

        await asyncio.gather(patch_and_record(10), patch_and_record(20))
        assert len(results) == 2
        assert set(results) == {10, 20}

    async def test_async_lock_is_asyncio_lock(self, token_store):
        assert isinstance(token_store.async_lock, asyncio.Lock)
