"""Shared test fixtures for ATM integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def mock_store_data():
    return {
        "version": 1,
        "tokens": [],
        "archived_tokens": [],
        "settings": {},
    }


@pytest.fixture
def mock_store(mock_store_data):
    store = AsyncMock()
    store.async_load = AsyncMock(return_value=mock_store_data)
    store.async_save = AsyncMock()
    return store


@pytest.fixture
async def token_store(hass, mock_store):
    from custom_components.atm.token_store import TokenStore

    with patch(
        "custom_components.atm.token_store._ATMStore",
        return_value=mock_store,
    ):
        instance = await TokenStore.async_create(hass)
    return instance
