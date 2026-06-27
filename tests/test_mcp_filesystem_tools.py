"""Tests for the scoped filesystem MCP tools (list_files, read_file, write_file).

All gate on cap_filesystem; write_file is Confirm-eligible. Access is confined to
www/, themes/, custom_templates/ under the config dir; traversal is refused.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.atm.mcp_view import _EXECUTOR_REGISTRY, _call_tool
from custom_components.atm.token_store import PermissionTree, TokenRecord


def _token(**caps) -> TokenRecord:
    base = {"cap_filesystem": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=PermissionTree(), **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, MagicMock())


@pytest.fixture
def fs_env(hass: HomeAssistant):
    www = os.path.join(hass.config.config_dir, "www")
    os.makedirs(www, exist_ok=True)
    with open(os.path.join(www, "hello.txt"), "w", encoding="utf-8") as f:
        f.write("hi there")
    # A secret outside the allowlist, to prove traversal is refused.
    with open(os.path.join(hass.config.config_dir, "secrets.yaml"), "w", encoding="utf-8") as f:
        f.write("token: supersecret")
    return hass


class TestExecutorRegistration:
    def test_write_file_registered(self):
        assert "write_file" in _EXECUTOR_REGISTRY


class TestReadFile:
    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(cap_filesystem="deny"), hass)
        assert outcome == "denied"

    async def test_read(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "hi there"

    async def test_traversal_refused(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/../secrets.yaml"}, _token(), hass)
        assert outcome == "not_found"  # outside allowlist looks identical to missing

    async def test_missing(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/nope.txt"}, _token(), hass)
        assert outcome == "not_found"


class TestWriteFile:
    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("write_file", {"path": "themes/x.yaml", "content": "a"}, _token(cap_filesystem="deny"), hass)
        assert outcome == "denied"

    async def test_write_creates_file(self, hass, fs_env):
        content, outcome, _ = await _call(
            "write_file", {"path": "themes/mytheme.yaml", "content": "a: 1"}, _token(), hass)
        assert outcome == "allowed"
        written = os.path.join(hass.config.config_dir, "themes", "mytheme.yaml")
        assert os.path.isfile(written)
        with open(written, encoding="utf-8") as f:
            assert f.read() == "a: 1"

    async def test_traversal_refused(self, hass, fs_env):
        content, outcome, _ = await _call("write_file", {"path": "../evil.yaml", "content": "x"}, _token(), hass)
        assert outcome == "denied"
        assert not os.path.isfile(os.path.join(hass.config.config_dir, "evil.yaml"))

    async def test_non_string_content(self, hass, fs_env):
        _, outcome, _ = await _call("write_file", {"path": "themes/x.yaml", "content": 123}, _token(), hass)
        assert outcome == "invalid_request"


class TestListFiles:
    async def test_root_lists_allowed_dirs(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["directories"] == ["www", "themes", "custom_templates"]

    async def test_list_dir(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {"path": "www"}, _token(), hass)
        assert outcome == "allowed"
        names = [e["name"] for e in _json(content)["entries"]]
        assert "hello.txt" in names
