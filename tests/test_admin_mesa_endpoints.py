"""Tests for the MESA profile admin HTTP endpoints in admin_view.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.atm.admin_view import (
    ATMAdminMesaAreasView,
    ATMAdminMesaAreaView,
    ATMAdminMesaDefaultsView,
    ATMAdminMesaDomainsView,
    ATMAdminMesaDomainView,
    ATMAdminMesaIntegrationsView,
    ATMAdminMesaIntegrationView,
    ATMAdminMesaIntegrationOptionsView,
    ATMAdminMesaIssuesView,
    ATMAdminMesaOrphansClearView,
    ATMAdminMesaProfilesView,
    ATMAdminMesaProfileView,
    ATMAdminMesaVocabularyView,
)
from custom_components.atm.audit import AuditLog
from custom_components.atm.const import DOMAIN
from custom_components.atm.data import ATMData
from custom_components.atm.mesa import async_setup_mesa
from custom_components.atm.rate_limiter import RateLimiter
from custom_components.atm.token_store import GlobalSettings, TokenStore


def _admin_request(body: bytes = b"", query: dict | None = None) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock()
    user.is_admin = True
    user.id = "admin-user"
    state: dict = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "atm_rid": "rid"}

    def _get(k, default=None):
        if k == KEY_HASS_USER:
            return user
        if k == KEY_AUTHENTICATED:
            return True
        return default

    request = MagicMock()
    request.method = "GET"
    request.path = "/api/atm/admin/mesa"
    request.remote = "127.0.0.1"
    request.query = query or {}
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.__getitem__ = MagicMock(side_effect=lambda k: state.get(k))
    request.__setitem__ = MagicMock(side_effect=lambda k, v: state.__setitem__(k, v))
    request.get = MagicMock(side_effect=_get)
    return request


async def _setup(hass: HomeAssistant, mesa_mode: str = "advisory") -> ATMData:
    runtime = await async_setup_mesa(hass, mesa_mode)
    store = MagicMock(spec=TokenStore)
    store.get_settings = MagicMock(return_value=GlobalSettings(mesa_mode=mesa_mode))
    audit = MagicMock(spec=AuditLog)
    audit.record = MagicMock()
    data = ATMData(
        store=store, rate_limiter=MagicMock(spec=RateLimiter),
        audit=audit, mesa=runtime,
    )
    hass.data[DOMAIN] = data
    return data


def _body(resp) -> dict:
    return json.loads(resp.text)


@pytest.mark.asyncio
async def test_put_then_get_profile(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaProfileView()
    view.hass = hass

    put_body = json.dumps({
        "semantic_profile": {
            "semantic_tags": ["lighting.ambient"],
            "operational_boundaries": {"control_mode": "autonomous"},
        }
    }).encode()
    resp = await view.put(_admin_request(body=put_body), entity_id="light.kitchen")
    assert resp.status == 200
    assert _body(resp)["entity_id"] == "light.kitchen"

    get_resp = await view.get(_admin_request(), entity_id="light.kitchen")
    out = _body(get_resp)
    assert out["stored"] is not None
    assert out["effective"]["semantic_profile"]["operational_boundaries"]["control_mode"] == "autonomous"
    assert "explanation" in out


@pytest.mark.asyncio
async def test_put_invalid_profile_rejected(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaProfileView()
    view.hass = hass
    # control_mode 'yolo' is not a valid enum value.
    bad = json.dumps({"semantic_profile": {"operational_boundaries": {"control_mode": "yolo"}}}).encode()
    resp = await view.put(_admin_request(body=bad), entity_id="light.kitchen")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_put_rejects_noncanonical_tag(hass: HomeAssistant):
    # Canonical-only tag entry is enforced server-side (not just in the UI):
    # a malformed/non-namespaced tag is rejected by mesa-core validation.
    await _setup(hass)
    view = ATMAdminMesaProfileView()
    view.hass = hass
    bad = json.dumps({"semantic_profile": {"semantic_tags": ["notdotted"]}}).encode()
    resp = await view.put(_admin_request(body=bad), entity_id="light.kitchen")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_put_stamps_user_origin(hass: HomeAssistant):
    data = await _setup(hass)
    view = ATMAdminMesaProfileView()
    view.hass = hass
    put_body = json.dumps({"semantic_profile": {"semantic_tags": ["lighting.ambient"]}}).encode()
    await view.put(_admin_request(body=put_body), entity_id="light.kitchen")
    stored = data.mesa.store.get("light.kitchen")
    assert stored.metadata.source.value == "user"


@pytest.mark.asyncio
async def test_delete_profile(hass: HomeAssistant):
    data = await _setup(hass)
    view = ATMAdminMesaProfileView()
    view.hass = hass
    put_body = json.dumps({"semantic_profile": {"semantic_tags": ["lighting.ambient"]}}).encode()
    await view.put(_admin_request(body=put_body), entity_id="light.kitchen")
    resp = await view.delete(_admin_request(), entity_id="light.kitchen")
    assert resp.status == 200
    assert data.mesa.store.get("light.kitchen") is None


@pytest.mark.asyncio
async def test_list_profiles(hass: HomeAssistant):
    data = await _setup(hass)
    view_one = ATMAdminMesaProfileView()
    view_one.hass = hass
    for eid in ("light.a", "light.b"):
        body = json.dumps({"semantic_profile": {"semantic_tags": ["lighting.ambient"]}}).encode()
        await view_one.put(_admin_request(body=body), entity_id=eid)

    view = ATMAdminMesaProfilesView()
    view.hass = hass
    resp = await view.get(_admin_request())
    out = _body(resp)
    assert out["total_matched"] == 2
    assert {p["entity_id"] for p in out["profiles"]} == {"light.a", "light.b"}


@pytest.mark.asyncio
async def test_list_domain_profiles(hass: HomeAssistant):
    await _setup(hass)
    one = ATMAdminMesaDomainView()
    one.hass = hass
    for domain in ("lock", "cover"):
        body = json.dumps({"semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}}).encode()
        await one.put(_admin_request(body=body), domain=domain)

    view = ATMAdminMesaDomainsView()
    view.hass = hass
    out = _body(await view.get(_admin_request()))
    assert {d["domain"] for d in out["domains"]} == {"lock", "cover"}
    assert all("document" in d for d in out["domains"])


@pytest.mark.asyncio
async def test_list_area_profiles_empty(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaAreasView()
    view.hass = hass
    out = _body(await view.get(_admin_request()))
    assert out["areas"] == []


@pytest.mark.asyncio
async def test_list_area_profiles(hass: HomeAssistant):
    await _setup(hass)
    one = ATMAdminMesaAreaView()
    one.hass = hass
    body = json.dumps({"semantic_profile": {"operational_boundaries": {"control_mode": "read_only"}}}).encode()
    await one.put(_admin_request(body=body), area_id="bedroom")

    view = ATMAdminMesaAreasView()
    view.hass = hass
    out = _body(await view.get(_admin_request()))
    assert [a["area_id"] for a in out["areas"]] == ["bedroom"]


@pytest.mark.asyncio
async def test_vocabulary_returns_canonical_tags(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaVocabularyView()
    view.hass = hass
    out = _body(await view.get(_admin_request()))
    assert "lighting.ambient" in out["canonical_tags"]
    assert "security.camera" in out["canonical_tags"]
    assert "lighting" in out["canonical_roots"]
    # Sorted for stable autocomplete ordering.
    assert out["canonical_tags"] == sorted(out["canonical_tags"])


@pytest.mark.asyncio
async def test_defaults_round_trip(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaDefaultsView()
    view.hass = hass
    body = json.dumps({"deployment_defaults": {"default_control_mode": "confirm"}}).encode()
    put_resp = await view.put(_admin_request(body=body))
    assert put_resp.status == 200

    get_resp = await view.get(_admin_request())
    out = _body(get_resp)
    assert out["deployment_defaults"]["deployment_defaults"]["default_control_mode"] == "confirm"


@pytest.mark.asyncio
async def test_issues_endpoint_refresh(hass: HomeAssistant):
    data = await _setup(hass)
    # Profile declares triggers_automations: none, but an automation references it.
    view = ATMAdminMesaProfileView()
    view.hass = hass
    pbody = json.dumps({
        "semantic_profile": {"operational_boundaries": {"triggers_automations": "none"}}
    }).encode()
    await view.put(_admin_request(body=pbody), entity_id="input_boolean.guest_mode")

    with open(hass.config.path("automations.yaml"), "w", encoding="utf-8") as fh:
        fh.write(
            "- id: a1\n  trigger:\n    - platform: state\n"
            "      entity_id: input_boolean.guest_mode\n  action: []\n"
        )

    issues_view = ATMAdminMesaIssuesView()
    issues_view.hass = hass
    resp = await issues_view.get(_admin_request(query={"refresh": "1"}))
    out = _body(resp)
    assert any(i["entity_id"] == "input_boolean.guest_mode" for i in out["issues"])
    # Area and integration orphan lists are always present in the response shape.
    assert out["orphan_areas"] == []
    assert out["orphan_integrations"] == []


@pytest.mark.asyncio
async def test_endpoints_503_when_mesa_unavailable(hass: HomeAssistant):
    data = await _setup(hass)
    data.mesa = None
    view = ATMAdminMesaProfilesView()
    view.hass = hass
    resp = await view.get(_admin_request())
    assert resp.status == 503


@pytest.mark.asyncio
async def test_domain_profile_crud(hass: HomeAssistant):
    data = await _setup(hass)
    view = ATMAdminMesaDomainView()
    view.hass = hass

    body = json.dumps({
        "semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}
    }).encode()
    put_resp = await view.put(_admin_request(body=body), domain="lock")
    assert put_resp.status == 200
    assert data.mesa.store.get_domain_profile("lock") is not None

    get_resp = await view.get(_admin_request(), domain="lock")
    assert _body(get_resp)["stored"] is not None

    del_resp = await view.delete(_admin_request(), domain="lock")
    assert del_resp.status == 200
    assert data.mesa.store.get_domain_profile("lock") is None


@pytest.mark.asyncio
async def test_integration_profile_crud(hass: HomeAssistant):
    data = await _setup(hass)
    view = ATMAdminMesaIntegrationView()
    view.hass = hass

    body = json.dumps({
        "semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}
    }).encode()
    put_resp = await view.put(_admin_request(body=body), integration="hue")
    assert put_resp.status == 200
    assert data.mesa.store.get_integration_profile("hue") is not None

    get_resp = await view.get(_admin_request(), integration="hue")
    assert _body(get_resp)["stored"] is not None

    list_view = ATMAdminMesaIntegrationsView()
    list_view.hass = hass
    list_resp = await list_view.get(_admin_request())
    assert {i["integration"] for i in _body(list_resp)["integrations"]} == {"hue"}

    del_resp = await view.delete(_admin_request(), integration="hue")
    assert del_resp.status == 200
    assert data.mesa.store.get_integration_profile("hue") is None


@pytest.mark.asyncio
async def test_integration_options_lists_platforms_with_entities(hass: HomeAssistant):
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    reg.async_get_or_create("light", "hue", "u1", suggested_object_id="a")
    reg.async_get_or_create("sensor", "hue", "u2", suggested_object_id="b")  # same platform -> deduped
    reg.async_get_or_create("lock", "yale_access_bluetooth", "u3", suggested_object_id="c")
    await _setup(hass)

    view = ATMAdminMesaIntegrationOptionsView()
    view.hass = hass
    resp = await view.get(_admin_request())
    opts = _body(resp)["integrations"]
    assert {o["id"] for o in opts} == {"hue", "yale_access_bluetooth"}
    # Every option carries a label (friendly title, or the component id as fallback).
    assert all(o.get("name") for o in opts)


@pytest.mark.asyncio
async def test_integration_put_rejects_invalid_name(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaIntegrationView()
    view.hass = hass
    body = json.dumps({"semantic_profile": {}}).encode()
    resp = await view.put(_admin_request(body=body), integration="Not An Integration")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_domain_put_rejects_invalid_domain_name(hass: HomeAssistant):
    await _setup(hass)
    view = ATMAdminMesaDomainView()
    view.hass = hass
    body = json.dumps({"semantic_profile": {}}).encode()
    resp = await view.put(_admin_request(body=body), domain="Not A Domain")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_area_profile_crud(hass: HomeAssistant):
    data = await _setup(hass)
    view = ATMAdminMesaAreaView()
    view.hass = hass

    body = json.dumps({
        "semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}
    }).encode()
    put_resp = await view.put(_admin_request(body=body), area_id="bedroom")
    assert put_resp.status == 200
    assert data.mesa.store.get_area_profile("bedroom") is not None

    del_resp = await view.delete(_admin_request(), area_id="bedroom")
    assert del_resp.status == 200
    assert data.mesa.store.get_area_profile("bedroom") is None


@pytest.mark.asyncio
async def test_domain_delete_changes_entity_provenance(hass: HomeAssistant):
    # Deleting a domain profile falls entities back to the next inheritance level;
    # explain reflects the new provenance. Covers the wide-blast-radius case.
    data = await _setup(hass)
    domain_view = ATMAdminMesaDomainView()
    domain_view.hass = hass
    body = json.dumps({
        "semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}
    }).encode()
    await domain_view.put(_admin_request(body=body), domain="light")

    # An unprofiled light inherits confirm from the domain profile.
    before = data.mesa.store.get_effective("light.somewhere")
    assert before.operational_boundaries.control_mode.value == "confirm"

    await domain_view.delete(_admin_request(), domain="light")
    after = data.mesa.store.get_effective("light.somewhere")
    # Falls back to the built-in light baseline (autonomous).
    assert after.operational_boundaries.control_mode.value == "autonomous"


@pytest.mark.asyncio
async def test_orphans_clear_deletes_all_orphan_profiles(hass: HomeAssistant):
    data = await _setup(hass)
    runtime = data.mesa
    body = json.dumps(
        {"semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}}
    ).encode()

    # Seed one orphan of each kind: a stored profile whose target does not exist.
    ev = ATMAdminMesaProfileView()
    ev.hass = hass
    av = ATMAdminMesaAreaView()
    av.hass = hass
    iv = ATMAdminMesaIntegrationView()
    iv.hass = hass
    await ev.put(_admin_request(body=body), entity_id="input_boolean.ghost_clear")
    await av.put(_admin_request(body=body), area_id="ghost_area_clear")
    await iv.put(_admin_request(body=body), integration="ghost_integration_clear")

    assert runtime.store.get("input_boolean.ghost_clear") is not None
    assert runtime.store.get_area_profile("ghost_area_clear") is not None
    assert runtime.store.get_integration_profile("ghost_integration_clear") is not None

    clear = ATMAdminMesaOrphansClearView()
    clear.hass = hass
    resp = await clear.post(_admin_request())
    assert resp.status == 200
    out = _body(resp)
    assert out["count"] == 3
    assert "input_boolean.ghost_clear" in out["deleted"]["entities"]
    assert "ghost_area_clear" in out["deleted"]["areas"]
    assert "ghost_integration_clear" in out["deleted"]["integrations"]

    # Profiles are gone and the orphan lists are now empty.
    assert runtime.store.get("input_boolean.ghost_clear") is None
    assert runtime.store.get_area_profile("ghost_area_clear") is None
    assert runtime.store.get_integration_profile("ghost_integration_clear") is None
    assert list(runtime.orphans) == []
    assert list(runtime.orphan_areas) == []
    assert list(runtime.orphan_integrations) == []


@pytest.mark.asyncio
async def test_orphans_clear_no_orphans_returns_zero(hass: HomeAssistant):
    await _setup(hass)
    clear = ATMAdminMesaOrphansClearView()
    clear.hass = hass
    resp = await clear.post(_admin_request())
    assert resp.status == 200
    out = _body(resp)
    assert out["count"] == 0
    assert out["deleted"] == {"entities": [], "areas": [], "integrations": []}


@pytest.mark.asyncio
async def test_orphans_clear_503_when_mesa_unavailable(hass: HomeAssistant):
    data = await _setup(hass)
    data.mesa = None
    clear = ATMAdminMesaOrphansClearView()
    clear.hass = hass
    resp = await clear.post(_admin_request())
    assert resp.status == 503
