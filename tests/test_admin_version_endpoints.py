"""Tests for the admin version-history HTTP endpoints (SPEC Section 16)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

from custom_components.atm.admin_view import ATMAdminVersionsView, ATMAdminVersionView
from custom_components.atm.const import DOMAIN
from custom_components.atm.data import ATMData
from custom_components.atm.version_store import VersionStore


def _data() -> ATMData:
    return ATMData(
        store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(),
    )


def _request(query: dict | None = None) -> MagicMock:
    user = MagicMock()
    user.is_admin = True
    user.id = "admin-user"
    state = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "atm_rid": "test-rid"}
    req = MagicMock()
    req.query = query or {}
    req.__getitem__ = MagicMock(side_effect=lambda k: state.get(k))
    req.get = MagicMock(side_effect=lambda k, d=None: state.get(k, d))
    return req


def _hass(data: ATMData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    return hass


def _body(resp) -> dict:
    return json.loads(resp.body)


async def _seed(data: ATMData):
    """Create -> edit history on one automation; returns the edit record."""
    await data.versions.record(
        resource_type="automation", resource_id="aid1", action="create",
        before=None, after={"alias": "A"}, alias="A", token_name="agent")
    return await data.versions.record(
        resource_type="automation", resource_id="aid1", action="edit",
        before={"alias": "A"}, after={"alias": "B"}, alias="B", token_name="agent")


class TestVersionsList:
    @pytest.mark.asyncio
    async def test_lists_newest_first_as_summaries(self):
        data = _data()
        await _seed(data)
        view = ATMAdminVersionsView()
        view.hass = _hass(data)
        resp = await view.get(_request({"resource_type": "automation", "resource_id": "aid1"}))
        assert resp.status == 200
        body = _body(resp)
        assert body["total"] == 2
        assert [v["action"] for v in body["versions"]] == ["edit", "create"]
        first = body["versions"][0]
        # summary omits the full configs but flags their presence
        assert "before" not in first and "after" not in first
        assert first["has_before"] is True and first["has_after"] is True
        assert first["alias"] == "B"

    @pytest.mark.asyncio
    async def test_missing_params_returns_400(self):
        view = ATMAdminVersionsView()
        view.hass = _hass(_data())
        resp = await view.get(_request({"resource_type": "automation"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_resource_returns_empty(self):
        view = ATMAdminVersionsView()
        view.hass = _hass(_data())
        resp = await view.get(_request({"resource_type": "automation", "resource_id": "nope"}))
        assert resp.status == 200
        assert _body(resp)["total"] == 0

    @pytest.mark.asyncio
    async def test_recent_feed_when_no_params(self):
        data = _data()
        await _seed(data)  # two automation versions
        await data.versions.record(
            resource_type="script", resource_id="s1", action="create",
            before=None, after={"alias": "S"}, alias="S", token_name="agent")
        view = ATMAdminVersionsView()
        view.hass = _hass(data)
        resp = await view.get(_request())  # neither param -> global recent feed
        assert resp.status == 200
        body = _body(resp)
        assert body["total"] == 3
        assert body["resource_type"] is None
        assert body["versions"][0]["resource_type"] == "script"  # newest first


class TestVersionDetail:
    @pytest.mark.asyncio
    async def test_returns_full_before_after(self):
        data = _data()
        rec = await _seed(data)
        view = ATMAdminVersionView()
        view.hass = _hass(data)
        resp = await view.get(_request(), version_id=rec.id)
        assert resp.status == 200
        body = _body(resp)
        assert body["id"] == rec.id
        assert body["before"] == {"alias": "A"}
        assert body["after"] == {"alias": "B"}

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        view = ATMAdminVersionView()
        view.hass = _hass(_data())
        resp = await view.get(_request(), version_id="does-not-exist")
        assert resp.status == 404
