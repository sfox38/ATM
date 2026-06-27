"""Tests for the token-scoped mesa_* MCP tools.

The priority is the no-enumeration-oracle guarantee: query counts, get, and
explain must all be filtered to the token's permission scope, and an
out-of-scope entity must be byte-identical to a nonexistent one.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.atm.data import ATMData
from custom_components.atm.mesa import async_setup_mesa
from custom_components.atm.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.atm.mesa_tools import (
    authored_restrictions,
    call_mesa_tool,
    mesa_tool_defs,
)
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


def _token(*, cap_config_read="allow", pass_through=False, domains=None) -> TokenRecord:
    return TokenRecord(
        id="tok",
        name="scoped",
        token_hash="x",
        created_at=utcnow(),
        created_by="admin",
        persona="read_only",
        cap_config_read=cap_config_read,
        pass_through=pass_through,
        permissions=PermissionTree(
            domains={k: PermissionNode(state=v) for k, v in (domains or {}).items()},
        ),
    )


async def _data_with_profiles(hass: HomeAssistant) -> ATMData:
    runtime = await async_setup_mesa(hass, "advisory")
    for eid in ("light.a", "switch.b"):
        runtime.store.set(
            eid,
            SemanticProfile.from_dict(
                eid,
                {"semantic_profile": {"semantic_tags": ["lighting.ambient"]}},
                default_origin=MetadataOrigin.USER,
            ),
        )
    data = ATMData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(),
                   mesa=runtime)
    return data


@pytest.fixture
def env(hass: HomeAssistant):
    hass.states.async_set("light.a", "on", {})
    hass.states.async_set("switch.b", "off", {})
    # In scope (light GREEN) and existing, but no MESA profile: exercises
    # mesa-core's own not_found path through the scoped store.
    hass.states.async_set("light.unprofiled", "on", {})
    return hass


async def _call(tool, args, token, hass, data):
    result, outcome, resource = await call_mesa_tool(tool, args, token, hass, data, "sess")
    text = result["content"][0]["text"]
    return json.loads(text), outcome, result.get("isError", False)


@pytest.mark.asyncio
async def test_query_returns_only_in_scope_entities(env):
    data = await _data_with_profiles(env)
    token = _token(domains={"light": "GREEN"})  # lights only; switch is out of scope
    payload, outcome, _ = await _call("mesa_query_profiles", {}, token, env, data)
    ids = [r["entity_id"] for r in payload["results"]]
    assert ids == ["light.a"]
    assert payload["total_matched"] == 1  # count is scope-relative, no oracle


@pytest.mark.asyncio
async def test_get_out_of_scope_is_identical_to_nonexistent(env):
    # switch.b exists and has a profile but is out of scope for this token. Its
    # response must be byte-identical to mesa-core's genuine not_found envelope,
    # so the token cannot tell the entity exists or carries a profile.
    from custom_components.atm.mesa_tools import _not_found_envelope

    data = await _data_with_profiles(env)
    token = _token(domains={"light": "GREEN"})

    out_of_scope, _, _ = await _call("mesa_get_profile", {"entity_id": "switch.b"}, token, env, data)
    # An in-scope but unprofiled entity hits mesa-core's own not_found path.
    in_scope_missing, _, _ = await _call("mesa_get_profile", {"entity_id": "light.unprofiled"}, token, env, data)

    assert out_of_scope == _not_found_envelope("switch.b")
    assert in_scope_missing == _not_found_envelope("light.unprofiled")


@pytest.mark.asyncio
async def test_explain_out_of_scope_is_not_found(env):
    data = await _data_with_profiles(env)
    token = _token(domains={"light": "GREEN"})
    payload, outcome, _ = await _call("mesa_explain_profile", {"entity_id": "switch.b"}, token, env, data)
    assert payload["error"] == "not_found"
    assert outcome == "not_found"


@pytest.mark.asyncio
async def test_get_in_scope_returns_profile(env):
    data = await _data_with_profiles(env)
    token = _token(domains={"light": "GREEN"})
    payload, outcome, is_error = await _call("mesa_get_profile", {"entity_id": "light.a"}, token, env, data)
    assert outcome == "allowed"
    assert is_error is False
    assert payload["entity_id"] == "light.a"


@pytest.mark.asyncio
async def test_cap_deny_forbids(env):
    data = await _data_with_profiles(env)
    token = _token(cap_config_read="deny", domains={"light": "GREEN"})
    result, outcome, resource = await call_mesa_tool(
        "mesa_query_profiles", {}, token, env, data, "sess"
    )
    assert outcome == "denied"
    assert result["isError"] is True


@pytest.mark.asyncio
async def test_pass_through_sees_all_non_atm(env):
    data = await _data_with_profiles(env)
    token = _token(pass_through=True)
    payload, _, _ = await _call("mesa_query_profiles", {}, token, env, data)
    ids = sorted(r["entity_id"] for r in payload["results"])
    assert ids == ["light.a", "switch.b"]


@pytest.mark.asyncio
async def test_caller_context_reports_token_identity(env):
    data = await _data_with_profiles(env)
    token = _token(domains={"light": "GREEN"})
    payload, _, _ = await _call("mesa_get_caller_context", {}, token, env, data)
    assert payload["caller_id"] == "tok"
    assert payload["roles"] == ["read_only"]


def test_mesa_tool_defs_carry_cap():
    defs = mesa_tool_defs()
    assert {d["name"] for d in defs} == {
        "mesa_query_profiles", "mesa_get_profile",
        "mesa_explain_profile", "mesa_get_caller_context",
    }
    assert all(d["cap"] == "cap_config_read" for d in defs)
    assert all("inputSchema" in d for d in defs)


# ---- authored_restrictions (get_overview MESA summary) -----------------------


async def _runtime_with_modes(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    profiles = {
        "lock.gun_safe": ("prohibited", "Never operate the gun safe."),
        "switch.sump_pump": ("read_only", "Observe only; never switch off."),
        "light.dimmer": ("confirm", "Ask before changing."),
        "fan.patio": ("autonomous", "Free to control."),
    }
    for eid, (mode, reason) in profiles.items():
        runtime.store.set(
            eid,
            SemanticProfile.from_dict(
                eid,
                {"semantic_profile": {"operational_boundaries": {
                    "control_mode": mode, "control_reason": reason}}},
                default_origin=MetadataOrigin.USER,
            ),
        )
    return runtime


@pytest.mark.asyncio
async def test_authored_restrictions_lists_only_authored_restrictive(hass: HomeAssistant):
    for eid in ("lock.gun_safe", "switch.sump_pump", "light.dimmer", "fan.patio"):
        hass.states.async_set(eid, "on", {})
    runtime = await _runtime_with_modes(hass)
    token = _token(domains={"lock": "GREEN", "switch": "GREEN", "light": "GREEN", "fan": "GREEN"})

    summary = authored_restrictions(runtime, token, hass)

    # Counts cover every authored profile; the list is only the hard restrictions.
    assert summary["authored_profile_count"] == 4
    assert summary["by_control_mode"] == {
        "autonomous": 1, "confirm": 1, "prohibited": 1, "read_only": 1}
    restricted = {e["entity_id"]: e for e in summary["restricted_entities"]}
    assert set(restricted) == {"lock.gun_safe", "switch.sump_pump"}  # not confirm/autonomous
    assert restricted["lock.gun_safe"]["control_mode"] == "prohibited"
    assert restricted["lock.gun_safe"]["reason"] == "Never operate the gun safe."


@pytest.mark.asyncio
async def test_authored_restrictions_is_scope_relative(hass: HomeAssistant):
    for eid in ("lock.gun_safe", "switch.sump_pump"):
        hass.states.async_set(eid, "on", {})
    runtime = await _runtime_with_modes(hass)
    token = _token(domains={"lock": "GREEN"})  # switch.sump_pump out of scope

    summary = authored_restrictions(runtime, token, hass)

    assert {e["entity_id"] for e in summary["restricted_entities"]} == {"lock.gun_safe"}
    assert "read_only" not in summary["by_control_mode"]  # hidden entity not even counted


@pytest.mark.asyncio
async def test_authored_restrictions_empty_without_authored_profiles(hass: HomeAssistant):
    hass.states.async_set("light.a", "on", {})
    runtime = await async_setup_mesa(hass, "enforced")  # no entity profiles authored
    token = _token(domains={"light": "GREEN"})

    summary = authored_restrictions(runtime, token, hass)

    # Baseline-derived modes are never counted; only authored profiles appear.
    assert summary["authored_profile_count"] == 0
    assert summary["restricted_entities"] == []
