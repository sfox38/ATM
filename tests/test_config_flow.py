"""Tests for the ATM config flow.

The flow is a single-step, single-instance flow with no remote connection, so the
prompt's cannot_connect / invalid_auth cases do not apply. We cover: the initial
form, creating the entry, and the single-instance already_configured abort.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.const import DOMAIN


@pytest.fixture(autouse=True)
def _enable(hass, enable_custom_integrations):
    """Make custom_components/atm discoverable to the config-entries machinery.

    ATM's manifest depends on `frontend`, whose `hass_frontend` package is not in
    the test venv. Mark it already-loaded so dependency processing during flow init
    does not try to bootstrap it.
    """
    hass.config.components.add("frontend")
    yield


async def test_user_step_shows_form(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry(hass: HomeAssistant):
    # Patch setup so the flow test does not trigger full integration setup
    # (route/panel/MESA wiring is covered separately in the setup tests).
    with patch("custom_components.atm.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={})
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "ATM"
    assert result2["data"] == {}


async def test_single_instance_only(hass: HomeAssistant):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
