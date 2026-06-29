"""Tests for the unauthenticated skill route (skill_view.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.atm.const import DOMAIN
from custom_components.atm.skill_view import ATM_SKILL_MARKDOWN, ATMSkillView


def _make_view(*, kill_switch: bool = False, shutting_down: bool = False, has_data: bool = True) -> ATMSkillView:
    view = ATMSkillView()
    hass = MagicMock()
    if has_data:
        data = MagicMock()
        data.shutting_down = shutting_down
        settings = MagicMock()
        settings.kill_switch = kill_switch
        data.store.get_settings.return_value = settings
        hass.data = {DOMAIN: data}
    else:
        hass.data = {}
    view.hass = hass
    return view


@pytest.mark.asyncio
async def test_serves_markdown_when_active():
    resp = await _make_view().get(MagicMock())
    assert resp.status == 200
    assert resp.content_type == "text/markdown"
    assert resp.text == ATM_SKILL_MARKDOWN


@pytest.mark.asyncio
async def test_503_when_kill_switch_on():
    # Runtime-enabled kill switch: the route is already registered (HA cannot
    # unregister it), so it must refuse service rather than keep serving.
    resp = await _make_view(kill_switch=True).get(MagicMock())
    assert resp.status == 503


@pytest.mark.asyncio
async def test_503_when_shutting_down():
    resp = await _make_view(shutting_down=True).get(MagicMock())
    assert resp.status == 503


@pytest.mark.asyncio
async def test_503_when_data_missing():
    resp = await _make_view(has_data=False).get(MagicMock())
    assert resp.status == 503


def test_skill_includes_domain_authoring_recipes():
    # The modular domain-authoring recipes (v2.1) are the skill's authoring value;
    # guard their headers against accidental removal.
    for header in (
        "### Automations",
        "### Scripts and scenes",
        "### Dashboards and cards",
        "### Conditional and visibility",
        "### Climate",
    ):
        assert header in ATM_SKILL_MARKDOWN
