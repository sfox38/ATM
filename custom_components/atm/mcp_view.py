"""MCP SSE endpoint for the ATM integration."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import math
import os
import re
import uuid
from datetime import timedelta
from typing import Any

from aiohttp import web
from homeassistant.components.automation.config import (
    async_validate_config_item as _validate_automation_config,
)
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.script.config import (
    async_validate_config_item as _validate_script_config,
)
from homeassistant.util.file import write_utf8_file_atomic as _write_utf8_file_atomic
from homeassistant.util.yaml import dump as _yaml_dump, load_yaml as _load_yaml
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from .audit import generate_request_id
from .const import (
    ANNOUNCE_BIT,
    ATM_VERSION,
    BLOCKED_DOMAINS,
    CAP_CONFIRM,
    CAP_DENY,
    DOMAIN,
    DUAL_GATE_SERVICES,
    HIGH_RISK_DOMAINS,
    MAX_BATCH_ITEMS,
    MAX_HISTORY_RANGE_DAYS,
    MAX_LOG_ENTRIES,
    MAX_SSE_CONNECTIONS_PER_TOKEN,
    MESA_APPROVED_EXECUTOR,
    PHYSICAL_GATE_DOMAINS,
    PHYSICAL_GATE_SERVICES,
    PROXY_TIMEOUT_SECONDS,
    SENSITIVE_ATTRIBUTES,
    SSE_HEARTBEAT_INTERVAL,
    TOKEN_LENGTH,
    TOKEN_PREFIX,
)
from .data import ATMData
from .mesa import apply_mesa_to_call, fire_mesa_blocked_event
from .mesa_tools import MESA_TOOL_NAMES, call_mesa_tool, mesa_tool_defs
from .helpers import (
    archive_expired_token,
    build_error_response as _error,
    build_permitted_states as _build_permitted_states,
    collect_log_entries as _collect_log_entries,
    effective_cap,
    effective_caps,
    evaluate_capability,
    fire_rate_limit_events as _fire_rate_limit_events,
    get_authenticated_token as _get_authenticated_token,
    get_client_ip as _get_client_ip,
    log_request as _log,
    parse_time_param as _parse_time_param,
    read_json_body as _read_json_body,
    render_template_for_token as _render_template_for_token,
)
from .policy_engine import (
    EntityCreationNotPermitted,
    Permission,
    filter_entities_for_token,
    filter_service_response,
    get_effective_hint,
    resolve,
    resolve_intent_entities,
    resolve_service_targets,
    scrub_sensitive_attributes,
    scrub_state_dict as _scrub_state_dict,
)
from .rate_limiter import RateLimitResult
from .token_store import TokenRecord

_LOGGER = logging.getLogger(__name__)

_MCP_VERSION_SSE = "2024-11-05"
_MCP_VERSION_STREAMABLE = "2025-03-26"

_AUTOMATION_YAML = "automations.yaml"
_AUTOMATION_LOCK_KEY = f"{DOMAIN}_automation_lock"
_SCRIPT_CONFIG_PATH = "scripts.yaml"
_SCRIPT_LOCK_KEY = f"{DOMAIN}_script_lock"


def _get_automation_lock(hass: Any) -> asyncio.Lock:
    if _AUTOMATION_LOCK_KEY not in hass.data:
        hass.data[_AUTOMATION_LOCK_KEY] = asyncio.Lock()
    return hass.data[_AUTOMATION_LOCK_KEY]


def _read_automations_yaml(path: str) -> list:
    if not os.path.isfile(path):
        return []
    data = _load_yaml(path)
    return data if isinstance(data, list) else []


def _write_automations_yaml(path: str, data: list) -> None:
    contents = _yaml_dump(data)
    _write_utf8_file_atomic(path, contents)


def _validate_integer_range(param_name: str, value: Any, min_val: int, max_val: int | None = None) -> str | None:
    """Validate an integer parameter is within range. Returns error message if invalid, None if valid."""
    if not isinstance(value, int) or isinstance(value, bool):
        return f"Input validation error: '{value}' is not of type 'integer'"
    if value < min_val:
        return f"Input validation error: {value} is less than the minimum of {min_val}"
    if max_val is not None and value > max_val:
        return f"Input validation error: {value} is greater than the maximum of {max_val}"
    return None


def _validate_number_range(param_name: str, value: Any, min_val: float | None = None, max_val: float | None = None) -> str | None:
    """Validate a number parameter (int or float) is within range. Returns error message if invalid, None if valid."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"Input validation error: '{value}' is not of type 'number'"
    if min_val is not None and value < min_val:
        return f"Input validation error: {value} is less than the minimum of {min_val}"
    if max_val is not None and value > max_val:
        return f"Input validation error: {value} is greater than the maximum of {max_val}"
    return None


def _validate_string_enum(param_name: str, value: Any, allowed: list[str]) -> str | None:
    """Validate a string is one of the allowed enum values. Returns error message if invalid, None if valid."""
    if not isinstance(value, str):
        return f"Input validation error: '{value}' is not of type 'string'"
    if value not in allowed:
        return f"Input validation error: '{value}' is not one of {allowed}"
    return None


def _get_script_lock(hass: Any) -> asyncio.Lock:
    if _SCRIPT_LOCK_KEY not in hass.data:
        hass.data[_SCRIPT_LOCK_KEY] = asyncio.Lock()
    return hass.data[_SCRIPT_LOCK_KEY]


def _read_scripts_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    data = _load_yaml(path)
    return data if isinstance(data, dict) else {}


def _write_scripts_yaml(path: str, data: dict) -> None:
    contents = _yaml_dump(data)
    _write_utf8_file_atomic(path, contents)


def _yaml_file_has_includes(path: str) -> bool:
    """Return True if the file exists and contains YAML !include directives."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "!include" in f.read()
    except OSError:
        return False


_SCRIPT_ID_RE = re.compile(r"^[a-z0-9_]+$")

_ENTITY_TOOL_DEFS: list[dict] = [
    {
        "name": "get_state",
        "description": "Get the current state of a Home Assistant entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID, e.g. light.living_room."},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "get_states",
        "description": "Get the current state of all accessible Home Assistant entities.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_history",
        "description": "Get the state history for a Home Assistant entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "start_time": {
                    "type": "string",
                    "description": "ISO timestamp or relative string (24h, 7d, 2w, 1m).",
                },
                "end_time": {
                    "type": "string",
                    "description": "ISO timestamp or relative string. Defaults to now.",
                },
            },
            "required": ["entity_id", "start_time"],
        },
    },
    {
        "name": "get_statistics",
        "description": "Get long-term statistics for a Home Assistant entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "period": {
                    "type": "string",
                    "enum": ["5minute", "hour", "day", "week", "month"],
                    "default": "hour",
                },
                "statistic_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subset of: mean, min, max, sum, state, change.",
                },
            },
            "required": ["entity_id", "start_time"],
        },
    },
    {
        "name": "call_service",
        "description": "Call a Home Assistant service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain, e.g. light."},
                "service": {"type": "string", "description": "Service name, e.g. turn_on."},
                "service_data": {"type": "object", "description": "Additional service parameters."},
                "entity_id": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Target entity ID or list of entity IDs.",
                },
                "device_id": {"type": "string"},
                "area_id": {"type": "string"},
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "get_approval_status",
        "description": (
            "Check the status of a pending approval that was created by an earlier tool call. "
            "Returns status (pending, approved, rejected, expired, cancelled), and the result if approved. "
            "Tokens can only fetch approvals they themselves created."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
            },
            "required": ["approval_id"],
        },
    },
]

_SYSTEM_TOOL_DEFS: list[dict] = [
    {
        "name": "get_config",
        "description": "Get the Home Assistant configuration.",
        "cap": "cap_config_read",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "render_template",
        "description": "Render a Jinja2 template in Home Assistant.",
        "cap": "cap_template_render",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "Jinja2 template string."},
            },
            "required": ["template"],
        },
    },
    {
        "name": "create_automation",
        "description": (
            "Create a new Home Assistant automation stored in automations.yaml. "
            "Do not include an 'id' field - ATM assigns the ID automatically. "
            "Returns the saved configuration including the generated automation_id. "
            "The config is validated by HA before saving - invalid configs are rejected with an error. "
            "config structure: 'alias' (string, required), "
            "'trigger' (list of trigger objects, each with a 'platform' field, required), "
            "'action' (list of action objects - service calls, delays, conditions, etc., required), "
            "'condition' (list of condition objects, optional), "
            "'mode' ('single'|'restart'|'queued'|'parallel', default 'single', optional)."
        ),
        "cap": "cap_automation_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config": {"type": "object", "description": "Full HA automation configuration (alias, trigger, action, condition, mode). Do not include 'id'."},
            },
            "required": ["config"],
        },
    },
    {
        "name": "edit_automation",
        "description": (
            "Replace the configuration of an existing Home Assistant automation. "
            "The 'config' object entirely replaces the current automation configuration. "
            "The automation_id is preserved - do not include it in 'config'. "
            "Returns the updated configuration. "
            "The config is validated by HA before saving - invalid configs are rejected with an error. "
            "Use get_config (requires cap_config_read) to list all existing automations and their IDs. "
            "ATM-created automations have IDs prefixed with 'atm_'."
        ),
        "cap": "cap_automation_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "ID of the automation to edit, as returned by create_automation or get_config."},
                "config": {"type": "object", "description": "Full replacement automation configuration (alias, trigger, action, condition, mode). Do not include 'id'."},
            },
            "required": ["automation_id", "config"],
        },
    },
    {
        "name": "delete_automation",
        "description": (
            "Permanently delete a Home Assistant automation from automations.yaml. "
            "Use get_config (requires cap_config_read) to list all existing automations and their IDs. "
            "ATM-created automations have IDs prefixed with 'atm_'."
        ),
        "cap": "cap_automation_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "ID of the automation to delete."},
            },
            "required": ["automation_id"],
        },
    },
    {
        "name": "create_script",
        "description": (
            "Create a new Home Assistant script stored in scripts.yaml. "
            "Provide a unique script_id (slug, e.g. 'morning_routine') - this becomes the entity_id: script.<script_id>. "
            "Returns the saved configuration. "
            "The config is validated by HA before saving - invalid configs are rejected with an error. "
            "config structure: 'alias' (string, required), "
            "'sequence' (list of action objects - service calls, delays, conditions, etc., required), "
            "'mode' ('single'|'restart'|'queued'|'parallel', default 'single', optional), "
            "'variables' (dict of script-level variables, optional), "
            "'fields' (dict of input field definitions for callable scripts, optional)."
        ),
        "cap": "cap_script_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_id": {"type": "string", "description": "Unique slug for the script (e.g. 'morning_routine'). Becomes script.<script_id> in HA. Must not already exist."},
                "config": {"type": "object", "description": "Full HA script configuration (alias, sequence, mode, variables, fields)."},
            },
            "required": ["script_id", "config"],
        },
    },
    {
        "name": "edit_script",
        "description": (
            "Replace the configuration of an existing Home Assistant script. "
            "The 'config' object entirely replaces the current script configuration. "
            "Returns the updated configuration. "
            "The config is validated by HA before saving - invalid configs are rejected with an error. "
            "Use get_config (requires cap_config_read) to list all existing scripts and their IDs."
        ),
        "cap": "cap_script_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_id": {"type": "string", "description": "ID of the script to edit (the slug, e.g. 'morning_routine')."},
                "config": {"type": "object", "description": "Full replacement script configuration (alias, sequence, mode, variables, fields)."},
            },
            "required": ["script_id", "config"],
        },
    },
    {
        "name": "delete_script",
        "description": (
            "Permanently delete a Home Assistant script from scripts.yaml. "
            "Use get_config (requires cap_config_read) to list all existing scripts and their IDs."
        ),
        "cap": "cap_script_write",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_id": {"type": "string", "description": "ID of the script to delete (the slug, e.g. 'morning_routine')."},
            },
            "required": ["script_id"],
        },
    },
    {
        "name": "get_logs",
        "description": (
            "Read recent Home Assistant system log entries. "
            "Useful for diagnosing errors, failed automations, or integration problems. "
            "Returns entries at or above the specified level, newest first. "
            "ATM's own log entries are excluded."
        ),
        "cap": "cap_log_read",
        "inputSchema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["INFO", "WARNING", "ERROR"],
                    "description": "Minimum log level. INFO returns INFO+WARNING+ERROR; WARNING returns WARNING+ERROR; ERROR returns ERROR only. Defaults to WARNING.",
                    "default": "WARNING",
                },
                "integration": {
                    "type": "string",
                    "description": "Optional integration name to filter by (e.g. 'hue', 'mqtt'). Matches homeassistant.components.<name> and custom_components.<name>.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                    "description": "Maximum number of entries to return (1-100, default 50).",
                },
            },
        },
    },
    {
        "name": "restart_ha",
        "description": "Restart Home Assistant.",
        "cap": "cap_restart",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "HassBroadcast",
        "description": "Broadcast a message through the home",
        "cap": "cap_broadcast",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
]

_NATIVE_TOOL_DEFS: list[dict] = [
    {
        "name": "GetLiveContext",
        "description": (
            "Provides real-time information about the CURRENT state, value, or mode of devices, "
            "sensors, entities, or areas. Use this tool for: 1. Answering questions about current "
            "conditions (e.g., 'Is the light on?'). 2. As the first step in conditional actions "
            "(e.g., 'If the weather is rainy, turn off sprinklers' requires checking the weather first)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "GetDateTime",
        "description": "Provides the current date and time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "HassTurnOn",
        "description": "Turns on/opens/presses a device or entity. For locks, this performs a 'lock' action. Use for requests like 'turn on', 'activate', 'enable', or 'lock'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string"}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassTurnOff",
        "description": "Turns off/closes a device or entity. For locks, this performs an 'unlock' action. Use for requests like 'turn off', 'deactivate', 'disable', or 'unlock'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string"}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassLightSet",
        "description": "Sets the brightness percentage or color of a light",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string"}},
                "brightness": {"type": "integer", "minimum": 0, "maximum": 100, "description": "The brightness percentage of the light between 0 and 100, where 0 is off and 100 is fully lit"},
                "color": {"type": "string"},
                "temperature": {"type": "integer", "minimum": 0},
            },
        },
    },
    {
        "name": "HassFanSetSpeed",
        "description": "Sets a fan's speed by percentage",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["fan"]}},
                "percentage": {"type": "integer", "minimum": 0, "maximum": 100, "description": "The speed percentage of the fan"},
            },
            "required": ["percentage"],
        },
    },
    {
        "name": "HassClimateSetTemperature",
        "description": "Sets the target temperature of a climate device or entity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "temperature": {"type": "number"},
            },
            "required": ["temperature"],
        },
    },
    {
        "name": "HassSetPosition",
        "description": "Sets the position of a device or entity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string"}},
                "device_class": {"type": "array", "items": {"type": "string"}},
                "position": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
    },
    {
        "name": "HassSetVolume",
        "description": "Sets the volume percentage of a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
                "volume_level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "The volume percentage of the media player"},
            },
        },
    },
    {
        "name": "HassSetVolumeRelative",
        "description": "Increases or decreases the volume of a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "volume_step": {"anyOf": [{"type": "string", "enum": ["up", "down"]}, {"type": "integer", "minimum": -100, "maximum": 100}]},
            },
        },
    },
    {
        "name": "HassMediaPause",
        "description": "Pauses a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassMediaUnpause",
        "description": "Resumes a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassMediaNext",
        "description": "Skips a media player to the next item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassMediaPrevious",
        "description": "Replays the previous item for a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassMediaSearchAndPlay",
        "description": "Searches for media and plays the first result",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "search_query": {"type": "string"},
                "media_class": {"type": "string", "enum": ["album", "app", "artist", "channel", "composer", "contributing_artist", "directory", "episode", "game", "genre", "image", "movie", "music", "playlist", "podcast", "season", "track", "tv_show", "url", "video"]},
            },
        },
    },
    {
        "name": "HassMediaPlayerMute",
        "description": "Mutes a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassMediaPlayerUnmute",
        "description": "Unmutes a media player",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string", "enum": ["media_player"]}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "HassCancelAllTimers",
        "description": "Cancels all timers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
            },
        },
    },
    {
        "name": "HassStopMoving",
        "description": "Stops a moving device or entity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "area": {"type": "string"},
                "floor": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "string"}},
                "device_class": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
]


def _jsonrpc_result(msg_id: Any, result: Any) -> dict:
    """Wrap a result in a JSON-RPC 2.0 success envelope."""
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _sanitize_jsonrpc_id(raw_id: Any) -> str | int | None:
    """Coerce a JSON-RPC id to a valid type (string, number, or null).

    JSON-RPC 2.0 requires id to be a string, number, or null. If the client
    sends a dict, list, or other non-conforming type, coerce to None rather
    than echoing it back.
    """
    if raw_id is None or isinstance(raw_id, (str, int, float)):
        return raw_id
    return None


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict:
    """Wrap an error in a JSON-RPC 2.0 error envelope."""
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _jsonrpc_notification(method: str, params: dict | None = None) -> dict:
    """Build a JSON-RPC 2.0 notification (no id field)."""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _tool_success(text: str) -> dict:
    """Return an MCP tool result content block with a plain-text payload."""
    return {"content": [{"type": "text", "text": text}]}


def _tool_error(message: str) -> dict:
    """Return an MCP tool result content block indicating an error."""
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_pending(approval: Any) -> dict:
    """Return an MCP tool result indicating a pending admin approval.

    isError is False because pending is a valid outcome, not a failure.
    """
    body = json.dumps({
        "status": "pending_approval",
        "approval_id": approval.id,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "review_url": f"/atm#approvals/{approval.id}",
        "message": "This action requires admin approval. The admin has been notified.",
    })
    return {"content": [{"type": "text", "text": body}]}


def _approval_resource(approval: Any) -> str:
    """Resource string used in audit logs for a pending-approval entry."""
    return f"approval:{approval.tool_name}:{approval.id}"


async def _gate(
    cap_name: str,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    *,
    tool_name: str,
    args: dict,
    request_id: str,
    client_ip: str | None,
    diff: dict,
) -> tuple[dict, str, str] | None:
    """Run capability gating. Returns a response tuple for deny/pending, or None for allow.

    For deny, returns (error_dict, "denied", tool_name).
    For pending, returns (pending_dict, "pending_approval", approval_resource).
    For allow, returns None and the caller proceeds with the side effect.
    """
    result = await evaluate_capability(
        cap_name, token, hass, data,
        tool_name=tool_name, args=args, request_id=request_id,
        client_ip=client_ip, diff=diff,
    )
    if result.is_deny:
        return _tool_error("Forbidden."), "denied", tool_name
    if result.is_pending:
        return _tool_pending(result.approval), "pending_approval", _approval_resource(result.approval)
    return None


async def _tool_get_state(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: return the current state of a single entity."""
    entity_id = args.get("entity_id", "")
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "denied", "get_state"

    perm = resolve(entity_id, token, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    state = hass.states.get(entity_id)
    if state is None:
        return _tool_error("Entity not found."), "not_found", entity_id

    return _tool_success(json.dumps(scrub_sensitive_attributes(state), default=str)), "allowed", entity_id


async def _tool_get_states(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: return all entities accessible to the token."""
    states = hass.states.async_all()
    filtered = filter_entities_for_token(states, token, hass)
    return _tool_success(json.dumps(filtered, default=str)), "allowed", "get_states"


async def _tool_get_history(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: fetch state history for a single permitted entity."""
    entity_id = args.get("entity_id", "")
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "denied", "get_history"

    perm = resolve(entity_id, token, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    start_time_raw = args.get("start_time", "")
    if not start_time_raw:
        return _tool_error("Missing required argument: start_time"), "denied", entity_id

    try:
        start_time = _parse_time_param(start_time_raw)
    except ValueError:
        return _tool_error("Invalid start_time format."), "denied", entity_id

    end_time = None
    end_time_raw = args.get("end_time")
    if end_time_raw:
        try:
            end_time = _parse_time_param(end_time_raw)
        except ValueError:
            return _tool_error("Invalid end_time format."), "denied", entity_id

    effective_end = end_time or utcnow()
    max_start = effective_end - timedelta(days=MAX_HISTORY_RANGE_DAYS)
    if start_time < max_start:
        start_time = max_start

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder import history as rec_history

        fn = functools.partial(
            rec_history.get_significant_states,
            hass,
            start_time,
            end_time,
            [entity_id],
            None,
            False,
            True,
            False,
            False,
        )
        result = await get_instance(hass).async_add_executor_job(fn)
    except Exception:
        _LOGGER.warning("MCP history call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("History call failed."), "denied", entity_id

    output = {}
    for eid, states_list in result.items():
        output[eid] = [
            _scrub_state_dict(s.as_dict() if hasattr(s, "as_dict") else s)
            for s in states_list
        ]

    return _tool_success(json.dumps(output, default=str)), "allowed", entity_id


async def _tool_get_statistics(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: fetch long-term statistics for a single permitted entity."""
    entity_id = args.get("entity_id", "")
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "denied", "get_statistics"

    perm = resolve(entity_id, token, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    start_time_raw = args.get("start_time", "")
    if not start_time_raw:
        return _tool_error("Missing required argument: start_time"), "denied", entity_id

    try:
        start_time = _parse_time_param(start_time_raw)
    except ValueError:
        return _tool_error("Invalid start_time format."), "denied", entity_id

    end_time = None
    end_time_raw = args.get("end_time")
    if end_time_raw:
        try:
            end_time = _parse_time_param(end_time_raw)
        except ValueError:
            return _tool_error("Invalid end_time format."), "denied", entity_id

    effective_end = end_time or utcnow()
    max_start = effective_end - timedelta(days=MAX_HISTORY_RANGE_DAYS)
    if start_time < max_start:
        start_time = max_start

    period = args.get("period", "hour")
    if period not in ("5minute", "hour", "day", "week", "month"):
        return _tool_error("Invalid period. Must be one of: 5minute, hour, day, week, month."), "denied", entity_id

    valid_types = {"mean", "min", "max", "sum", "state", "change"}
    raw_types = args.get("statistic_types")
    type_set: set | None = None
    if raw_types:
        type_set = {t for t in raw_types if t in valid_types} or None

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder import statistics as recorder_stats

        fn = functools.partial(
            recorder_stats.statistics_during_period,
            hass,
            start_time,
            end_time,
            {entity_id},
            period,
            None,
            # types became non-optional in HA 2026.4; default to all types when not specified.
            type_set or {"mean", "min", "max", "sum", "state", "change"},
        )
        result = await get_instance(hass).async_add_executor_job(fn)
    except Exception:
        _LOGGER.warning("MCP statistics call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("Statistics call failed."), "denied", entity_id

    return _tool_success(json.dumps(result, default=str)), "allowed", entity_id


async def _tool_call_service(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: call a HA service with entity targets filtered to WRITE-permitted entities."""
    domain = args.get("domain", "")
    service = args.get("service", "")
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "denied", "call_service"

    service_key = f"{domain}/{service}"

    if service_key in DUAL_GATE_SERVICES:
        blocked = await _gate(
            "cap_restart", token, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=_build_diff_call_service(args, token, hass),
        )
        if blocked is not None:
            return blocked
    elif service_key in PHYSICAL_GATE_SERVICES:
        blocked = await _gate(
            "cap_physical_control", token, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=_build_diff_call_service(args, token, hass),
        )
        if blocked is not None:
            return blocked
    return await _execute_call_service(
        args, token, hass, data, request_id=request_id, client_ip=client_ip,
    )


async def _execute_call_service(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    *,
    request_id: str = "",
    client_ip: str | None = None,
    mesa_approved: bool = False,
) -> tuple[dict, str, str]:
    domain = args.get("domain", "")
    service = args.get("service", "")
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "denied", "call_service"

    resource = f"service:{domain}/{service}"
    service_key = f"{domain}/{service}"

    entity_id = args.get("entity_id")
    device_id = args.get("device_id")
    area_id = args.get("area_id")
    service_data = args.get("service_data") or {}
    if not isinstance(service_data, dict):
        service_data = {}

    # DUAL_GATE_SERVICES have no entities in hass.states; routing them through
    # resolve_service_targets always produces an empty list and a spurious 403.
    # The cap_restart gate above is the only permission check required.
    if service_key in DUAL_GATE_SERVICES:
        if domain in HIGH_RISK_DOMAINS:
            _LOGGER.info(
                "High-risk service call %s/%s by token %s",
                domain, service, token.name,
            )
        try:
            async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
                await hass.services.async_call(
                    domain, service, service_data, blocking=True, return_response=False,
                )
        except asyncio.TimeoutError:
            return (
                _tool_success(json.dumps({
                    "success": True,
                    "partial": True,
                    "message": "Service dispatched but HA did not respond within the timeout window.",
                })),
                "allowed",
                resource,
            )
        except ServiceNotFound:
            # Return generic error - spec §4.3: never confirm or deny service existence.
            return _tool_error("Forbidden."), "denied", resource
        except HomeAssistantError:
            return _tool_error("Forbidden."), "denied", resource
        return _tool_success(json.dumps({"success": True})), "allowed", resource

    try:
        permitted_entities, _requested_count = resolve_service_targets(
            entity_id=entity_id,
            device_id=device_id,
            area_id=area_id,
            service_domain=domain,
            token=token,
            hass=hass,
        )
    except EntityCreationNotPermitted:
        return _tool_error("Forbidden."), "denied", resource

    if not permitted_entities:
        return _tool_error("Forbidden."), "denied", resource

    # MESA enforcement runs last, on the flattened entity list ATM already
    # permitted (rule 15: never pass device_id/area_id/"all" to HA). MESA never
    # sees entities ATM denied; it can drop entities, gate the whole call for
    # confirmation, or block outright.
    mesa_outcome = await apply_mesa_to_call(
        hass, data, token,
        domain=domain, service=service, service_data=service_data,
        entities=permitted_entities,
        request_id=request_id, client_ip=client_ip, session_id=request_id,
        confirm_approved=mesa_approved,
    )
    if mesa_outcome.blocked:
        fire_mesa_blocked_event(hass, token, mesa_outcome.blocked)
    if mesa_outcome.decision == "pending":
        return (
            _tool_pending(mesa_outcome.approval),
            "pending_approval",
            _approval_resource(mesa_outcome.approval),
        )
    if mesa_outcome.decision == "deny":
        return _tool_error("Forbidden."), "denied", resource
    permitted_entities = mesa_outcome.entities

    if domain in HIGH_RISK_DOMAINS:
        _LOGGER.info(
            "High-risk service call %s/%s by token %s",
            domain, service, token.name,
        )

    call_data = dict(service_data)
    call_data["entity_id"] = permitted_entities

    use_return_response = False
    if effective_cap(token, "cap_service_response") != CAP_DENY:
        try:
            from homeassistant.core import SupportsResponse as _SR
            handler = hass.services.async_services().get(domain, {}).get(service)
            use_return_response = (
                handler is not None and
                getattr(handler, "supports_response", None) not in (None, _SR.NONE)
            )
        except Exception:
            pass

    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            svc_response = await hass.services.async_call(
                domain,
                service,
                call_data,
                blocking=True,
                return_response=use_return_response,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({
                "success": True,
                "partial": True,
                "message": "Service dispatched but HA did not respond within the timeout window.",
            })),
            "allowed",
            resource,
        )
    except ServiceNotFound:
        # Return generic error - spec §4.3: never confirm or deny service existence.
        return _tool_error("Forbidden."), "denied", resource
    except HomeAssistantError:
        return _tool_error("Forbidden."), "denied", resource

    filtered_response = filter_service_response(svc_response, token, hass) if svc_response is not None else None

    body: dict[str, Any] = {"success": True}
    if filtered_response is not None:
        body["service_response"] = filtered_response

    return _tool_success(json.dumps(body, default=str)), "allowed", resource


async def _execute_call_service_mesa_approved(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    """Re-run a MESA-gated service call after admin approval.

    Registered under MESA_APPROVED_EXECUTOR but never dispatchable from the tool
    router, so a token cannot reach the confirm-approved path itself. Re-runs
    ATM scope resolution and MESA evaluation; only control_mode:confirm blocks
    are treated as satisfied, so an entity that became prohibited or read_only
    since the request is still rejected.
    """
    return await _execute_call_service(args, token, hass, data, mesa_approved=True)


async def _tool_get_config(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: return HA config (requires cap_config_read)."""
    if effective_cap(token, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_config"
    config_dict = hass.config.as_dict()
    config_dict["components"] = [
        c for c in config_dict.get("components", [])
        if c != DOMAIN and not c.startswith(DOMAIN + ".")
    ]
    return _tool_success(json.dumps(config_dict, default=str)), "allowed", "get_config"




async def _tool_get_logs(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: read system_log entries (requires cap_log_read)."""
    if effective_cap(token, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_logs"

    raw_level = str(args.get("level") or "WARNING").strip().upper()
    if raw_level not in ("INFO", "WARNING", "ERROR"):
        raw_level = "WARNING"

    integration = str(args.get("integration") or "").strip() or None

    # Default matches _DEFAULT_LOG_LIMIT in proxy_view.py. Both are 50 intentionally;
    # they are not shared via a constant to avoid coupling the two view modules.
    limit = 50
    raw_limit = args.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
            if not (1 <= limit <= MAX_LOG_ENTRIES):
                limit = max(1, min(limit, MAX_LOG_ENTRIES))
        except (TypeError, ValueError):
            limit = 50

    entries = _collect_log_entries(hass, raw_level, integration, limit)
    return _tool_success(json.dumps({"count": len(entries), "entries": entries}, default=str)), "allowed", "get_logs"



async def _tool_render_template(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: render a Jinja2 template against permitted entity state."""
    if effective_cap(token, "cap_template_render") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "render_template"

    template_str = args.get("template", "")
    if not template_str:
        return _tool_error("Missing required argument: template"), "invalid_request", "render_template"

    try:
        rendered = _render_template_for_token(template_str, token, hass)
    except Exception:
        return _tool_error("Template rendering failed. Check your template syntax."), "invalid_request", "render_template"

    return _tool_success(rendered), "allowed", "render_template"


async def _tool_create_automation(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a new UI automation by appending to automations.yaml."""
    blocked = await _gate(
        "cap_automation_write", token, hass, data,
        tool_name="create_automation", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_create_automation(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_automation(args, token, hass, data)


async def _execute_create_automation(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "create_automation"

    automation_id = "atm_" + uuid.uuid4().hex[:16]
    config = {k: v for k, v in config.items() if k != "id"}
    config["id"] = automation_id

    # Validate config but write the original (not the validated result) to YAML.
    # HA's validator may return internal representations that don't round-trip
    # through YAML cleanly. HA's own automation UI follows the same pattern:
    # write the user's config and let automation.reload normalize it.
    try:
        validated = await _validate_automation_config(hass, automation_id, config)
        if validated is None:
            return _tool_error("Automation config failed validation. Check trigger, condition, and action fields."), "invalid_request", "create_automation"
    except Exception as exc:
        _LOGGER.debug("create_automation validation error: %s", exc)
        return _tool_error("Automation config validation failed. Check trigger, condition, and action fields."), "invalid_request", "create_automation"

    path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
    lock = _get_automation_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("automations.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "create_automation"
            items = await hass.async_add_executor_job(_read_automations_yaml, path)
            items.append(config)
            await hass.async_add_executor_job(_write_automations_yaml, path, items)
        await hass.services.async_call("automation", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("create_automation failed: %s", exc)
        return _tool_error("Failed to create automation. Check HA logs for details."), "denied", "create_automation"

    return _tool_success(json.dumps(config, indent=2, default=str)), "allowed", "create_automation"


async def _tool_edit_automation(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: replace the config of an existing UI automation."""
    blocked = await _gate(
        "cap_automation_write", token, hass, data,
        tool_name="edit_automation", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_edit_automation(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_automation(args, token, hass, data)


async def _execute_edit_automation(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    # automation_id is not format-validated (unlike script_id which uses _SCRIPT_ID_RE).
    # HA's async_validate_config_item rejects unknown IDs, so the impact is limited to
    # accepting cosmetically wrong IDs that HA then rejects. Not a security concern.
    automation_id = args.get("automation_id", "").strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "edit_automation"

    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "edit_automation"

    config = {k: v for k, v in config.items() if k != "id"}
    config["id"] = automation_id

    try:
        validated = await _validate_automation_config(hass, automation_id, config)
        if validated is None:
            return _tool_error("Automation config failed validation. Check trigger, condition, and action fields."), "invalid_request", "edit_automation"
    except Exception as exc:
        _LOGGER.debug("edit_automation validation error: %s", exc)
        return _tool_error("Automation config validation failed. Check trigger, condition, and action fields."), "invalid_request", "edit_automation"

    path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
    lock = _get_automation_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("automations.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "edit_automation"
            items = await hass.async_add_executor_job(_read_automations_yaml, path)
            idx = next((i for i, a in enumerate(items) if a.get("id") == automation_id), None)
            if idx is None:
                return _tool_error(f"No automation found with id '{automation_id}'."), "denied", "edit_automation"
            items[idx] = config
            await hass.async_add_executor_job(_write_automations_yaml, path, items)
        await hass.services.async_call("automation", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("edit_automation failed: %s", exc)
        return _tool_error("Failed to edit automation. Check HA logs for details."), "denied", "edit_automation"

    return _tool_success(json.dumps(config, indent=2, default=str)), "allowed", "edit_automation"


async def _tool_delete_automation(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: permanently delete a UI automation."""
    blocked = await _gate(
        "cap_automation_write", token, hass, data,
        tool_name="delete_automation", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_delete_automation(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_automation(args, token, hass, data)


async def _execute_delete_automation(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    automation_id = args.get("automation_id", "").strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "delete_automation"

    path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
    lock = _get_automation_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("automations.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "delete_automation"
            items = await hass.async_add_executor_job(_read_automations_yaml, path)
            filtered = [a for a in items if a.get("id") != automation_id]
            if len(filtered) == len(items):
                return _tool_error(f"No automation found with id '{automation_id}'."), "denied", "delete_automation"
            await hass.async_add_executor_job(_write_automations_yaml, path, filtered)
        await hass.services.async_call("automation", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("delete_automation failed: %s", exc)
        return _tool_error("Failed to delete automation. Check HA logs for details."), "denied", "delete_automation"

    return _tool_success(f"Automation '{automation_id}' deleted successfully."), "allowed", "delete_automation"


async def _tool_create_script(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a new script in scripts.yaml."""
    blocked = await _gate(
        "cap_script_write", token, hass, data,
        tool_name="create_script", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_create_script(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_script(args, token, hass, data)


async def _execute_create_script(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    script_id = args.get("script_id", "").strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "create_script"
    if not _SCRIPT_ID_RE.match(script_id):
        return _tool_error("script_id must contain only lowercase letters, digits, and underscores."), "invalid_request", "create_script"

    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "create_script"

    try:
        validated = await _validate_script_config(hass, script_id, config)
        if validated is None:
            return _tool_error("Script config failed validation. Check sequence, mode, and field definitions."), "invalid_request", "create_script"
    except Exception as exc:
        _LOGGER.debug("create_script validation error: %s", exc)
        return _tool_error("Script config validation failed. Check sequence, mode, and field definitions."), "invalid_request", "create_script"

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("scripts.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "create_script"
            scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
            if script_id in scripts:
                return _tool_error(f"A script with id '{script_id}' already exists. Use edit_script to update it."), "invalid_request", "create_script"
            scripts[script_id] = config
            await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
        await hass.services.async_call("script", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("create_script failed: %s", exc)
        return _tool_error("Failed to create script. Check HA logs for details."), "denied", "create_script"

    return _tool_success(json.dumps({script_id: config}, indent=2, default=str)), "allowed", "create_script"


async def _tool_edit_script(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: replace the config of an existing script in scripts.yaml."""
    blocked = await _gate(
        "cap_script_write", token, hass, data,
        tool_name="edit_script", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_edit_script(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_script(args, token, hass, data)


async def _execute_edit_script(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    script_id = args.get("script_id", "").strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "edit_script"
    if not _SCRIPT_ID_RE.match(script_id):
        return _tool_error("script_id must contain only lowercase letters, digits, and underscores."), "invalid_request", "edit_script"

    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "edit_script"

    try:
        validated = await _validate_script_config(hass, script_id, config)
        if validated is None:
            return _tool_error("Script config failed validation. Check sequence, mode, and field definitions."), "invalid_request", "edit_script"
    except Exception as exc:
        _LOGGER.debug("edit_script validation error: %s", exc)
        return _tool_error("Script config validation failed. Check sequence, mode, and field definitions."), "invalid_request", "edit_script"

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("scripts.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "edit_script"
            scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
            if script_id not in scripts:
                return _tool_error(f"No script found with id '{script_id}'."), "denied", "edit_script"
            scripts[script_id] = config
            await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
        await hass.services.async_call("script", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("edit_script failed: %s", exc)
        return _tool_error("Failed to edit script. Check HA logs for details."), "denied", "edit_script"

    return _tool_success(json.dumps({script_id: config}, indent=2, default=str)), "allowed", "edit_script"


async def _tool_delete_script(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: permanently delete a script from scripts.yaml."""
    blocked = await _gate(
        "cap_script_write", token, hass, data,
        tool_name="delete_script", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_delete_script(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_script(args, token, hass, data)


async def _execute_delete_script(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    script_id = args.get("script_id", "").strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "delete_script"
    if not _SCRIPT_ID_RE.match(script_id):
        return _tool_error("Invalid script ID format."), "invalid_request", "delete_script"

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                return _tool_error("scripts.yaml uses !include directives. ATM cannot safely edit it without destroying the include structure."), "denied", "delete_script"
            scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
            if script_id not in scripts:
                return _tool_error(f"No script found with id '{script_id}'."), "denied", "delete_script"
            del scripts[script_id]
            await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
        await hass.services.async_call("script", "reload", blocking=True)
    except Exception as exc:
        _LOGGER.error("delete_script failed: %s", exc)
        return _tool_error("Failed to delete script. Check HA logs for details."), "denied", "delete_script"

    return _tool_success(f"Script '{script_id}' deleted successfully."), "allowed", "delete_script"


async def _tool_restart_ha(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: restart HA (gated by cap_restart, supports Confirm)."""
    blocked = await _gate(
        "cap_restart", token, hass, data,
        tool_name="restart_ha", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_restart_ha(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_restart_ha(args, token, hass, data)


async def _execute_restart_ha(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    """Side-effect path for restart_ha. Assumes capability is already satisfied."""
    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            await hass.services.async_call(
                "homeassistant",
                "restart",
                {},
                blocking=True,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({"success": True, "partial": True, "message": "Restart dispatched."})),
            "allowed",
            "restart_ha",
        )
    except ServiceNotFound:
        return _tool_error("Restart failed."), "denied", "restart_ha"
    except HomeAssistantError:
        return _tool_error("Restart failed."), "denied", "restart_ha"
    return _tool_success(json.dumps({"success": True})), "allowed", "restart_ha"


async def _tool_get_approval_status(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    """MCP tool: poll an approval the token previously created.

    Cross-token reads return 404 (matching the missing-record response) to avoid
    a token-existence oracle.
    """
    from .approvals import get_approval  # noqa: PLC0415

    approval_id = args.get("approval_id")
    if not approval_id or not isinstance(approval_id, str):
        return _tool_error("Missing approval_id."), "invalid_request", "get_approval_status"
    record = get_approval(data.store, approval_id)
    if record is None or record.token_id != token.id:
        return _tool_error("Approval not found."), "not_found", "get_approval_status"
    payload = {
        "approval_id": record.id,
        "status": record.status,
        "tool_name": record.tool_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "result": record.result,
        "rejected_reason": record.rejected_reason,
    }
    return _tool_success(json.dumps(payload, default=str)), "allowed", _approval_resource(record)


# Executor registry for the admin-approval gate. When an admin approves a pending
# request, the approve handler looks up the saved tool_name here and invokes the
# corresponding _execute_X function with the saved args.
_EXECUTOR_REGISTRY: dict[str, Any] = {}


def _register_executor(tool_name: str, fn: Any) -> None:
    """Record an executor function for a tool. Called once at module import."""
    _EXECUTOR_REGISTRY[tool_name] = fn


async def execute_approved_tool(
    tool_name: str,
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
) -> tuple[dict, str, str]:
    """Run the side-effect path for a previously-gated tool. Returns the tool result tuple.

    Raises KeyError if no executor is registered for the tool_name.
    """
    fn = _EXECUTOR_REGISTRY.get(tool_name)
    if fn is None:
        raise KeyError(f"No executor registered for tool {tool_name!r}")
    return await fn(args, token, hass, data)


def _build_diff_restart_ha(args: dict, token: TokenRecord, hass: Any) -> dict:
    """Diff payload for restart_ha approvals.

    Includes the count of currently-active SSE clients that will disconnect on restart
    so admin sees real-time impact.
    """
    sse_count = 0
    try:
        domain_data = hass.data.get(DOMAIN)
        if domain_data is not None and hasattr(domain_data, "sse_connections"):
            sse_count = sum(len(q) for q in domain_data.sse_connections.values())
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    return {
        "kind": "system_action",
        "summary": "Restart Home Assistant",
        "target": {"type": "system", "id": "homeassistant", "label": "Home Assistant"},
        "preview": {
            "active_atm_sse_clients": sse_count,
            "warning": "All current ATM SSE connections will disconnect on restart.",
        },
    }


_YAML_RESERVED: frozenset[str] = frozenset({
    "true", "false", "yes", "no", "on", "off", "null", "~",
})
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_LIVE_CONTEXT_ATTRS: tuple[str, ...] = (
    "unit_of_measurement",
    "device_class",
    "brightness",
    "volume_level",
    "media_title",
    "current_temperature",
    "temperature",
    "current_position",
    "percentage",
)


def _yaml_scalar(value: Any) -> str:
    """Format a state or attribute value as a YAML scalar string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return f"'{value}'"
    if isinstance(value, int):
        return f"'{value}'"
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return str(value)
    s = str(value)
    if not s:
        return "''"
    if "'" in s:
        s = s.replace("'", "''")
        return f"'{s}'"
    if s.lower() in _YAML_RESERVED:
        return f"'{s}'"
    try:
        int(s)
        return f"'{s}'"
    except ValueError:
        pass
    try:
        float(s)
        return f"'{s}'"
    except ValueError:
        pass
    if _DATE_PREFIX_RE.match(s):
        return f"'{s}'"
    return s


def _build_live_context(token: TokenRecord, hass: Any) -> str:
    """Build a GetLiveContext-format YAML-like summary of accessible entities."""
    registry = er.async_get(hass)
    dr_inst = dr.async_get(hass)
    ar_inst = ar.async_get(hass)
    area_names: dict[str, str] = {a.id: a.name for a in ar_inst.async_list_areas()}

    states = hass.states.async_all()
    if token.pass_through:
        if token.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            accessible = [
                s for s in states
                if _should_expose(hass, "conversation", s.entity_id)
                and s.entity_id.split(".")[0] not in BLOCKED_DOMAINS
                and not (
                    (entry := registry.async_get(s.entity_id)) is not None
                    and entry.platform == DOMAIN
                )
            ]
        else:
            accessible = [
                s for s in states
                if s.entity_id.split(".")[0] not in BLOCKED_DOMAINS
                and not (
                    (entry := registry.async_get(s.entity_id)) is not None
                    and entry.platform == DOMAIN
                )
            ]
    else:
        accessible = [
            s for s in states
            if resolve(s.entity_id, token, hass) in (Permission.READ, Permission.WRITE)
        ]

    accessible.sort(key=lambda s: s.attributes.get("friendly_name") or s.entity_id)

    lines = ["Live Context: An overview of the areas and the devices in this smart home:"]
    for state in accessible:
        friendly_name = state.attributes.get("friendly_name") or state.entity_id
        domain = state.entity_id.split(".")[0]
        lines.append(f"- names: {_yaml_scalar(friendly_name)}")
        lines.append(f"  domain: {domain}")
        lines.append(f"  state: {_yaml_scalar(state.state)}")

        entry = registry.async_get(state.entity_id)
        area_id = None
        if entry:
            if entry.area_id:
                area_id = entry.area_id
            elif entry.device_id:
                device = dr_inst.async_get(entry.device_id)
                if device and device.area_id:
                    area_id = device.area_id
        if area_id and area_id in area_names:
            lines.append(f"  areas: {_yaml_scalar(area_names[area_id])}")

        attr_lines: list[str] = []
        for attr_key in _LIVE_CONTEXT_ATTRS:
            if attr_key in state.attributes and attr_key not in SENSITIVE_ATTRIBUTES:
                val = state.attributes[attr_key]
                attr_lines.append(f"    {attr_key}: {_yaml_scalar(val)}")
        if attr_lines:
            lines.append("  attributes:")
            lines.extend(attr_lines)

    return "\n".join(lines)


async def _tool_get_live_context(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: GetLiveContext - return a human-readable summary of accessible entities."""
    text = _build_live_context(token, hass)
    return _tool_success(text), "allowed", "GetLiveContext"


async def _tool_get_date_time(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: GetDateTime - return the current local date and time."""
    from homeassistant.util.dt import now as ha_now
    local = ha_now()
    offset = local.strftime("%z")
    sign = offset[0]
    hours = int(offset[1:3])
    mins = int(offset[3:5])
    tz_str = f"{sign}{hours:02d}" if mins == 0 else f"{sign}{hours:02d}:{mins:02d}"
    result = {
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H:%M:%S"),
        "timezone": tz_str,
        "weekday": local.strftime("%A"),
    }
    return _tool_success(json.dumps(result)), "allowed", "GetDateTime"


def _area_id_from_name(hass: Any, area_name: str) -> str:
    """Return the area registry ID for a given area name, falling back to the name itself."""
    ar_inst = ar.async_get(hass)
    for a in ar_inst.async_list_areas():
        if a.name.lower() == area_name.lower() or a.id == area_name:
            return a.id
    return area_name


def _build_target_context(args: dict, hass: Any) -> list[dict]:
    """Build the leading context entries for the native HA action response."""
    area = args.get("area")
    floor = args.get("floor")
    if area:
        return [{"name": area, "type": "area", "id": _area_id_from_name(hass, area)}]
    if floor:
        return [{"name": floor, "type": "floor", "id": floor}]
    return []


async def _tool_intent_action(
    tool_name: str,
    service_domain: str,
    service_name: str,
    service_data: dict,
    entities: list[str],
    hass: Any,
    token: TokenRecord,
    args: dict | None = None,
) -> tuple[dict, str, str]:
    """Execute a service call on pre-resolved, permission-filtered entity list.

    MESA enforcement runs here, the single choke point for all native Hass*
    tools, on the already-flattened entity list. data/request_id are fetched
    from hass since native tools do not thread them; when the MESA runtime is
    absent (tests, setup failure) the gate degrades to allow-all.
    """
    if not entities:
        return _tool_error("No accessible entities matched your request."), "denied", tool_name

    data = hass.data.get(DOMAIN)
    if data is not None and getattr(data, "mesa", None) is not None:
        request_id = generate_request_id()
        mesa_outcome = await apply_mesa_to_call(
            hass, data, token,
            domain=service_domain, service=service_name, service_data=service_data,
            entities=entities, request_id=request_id, client_ip=None,
            session_id=request_id,
        )
        if mesa_outcome.blocked:
            fire_mesa_blocked_event(hass, token, mesa_outcome.blocked)
        if mesa_outcome.decision == "pending":
            return (
                _tool_pending(mesa_outcome.approval),
                "pending_approval",
                _approval_resource(mesa_outcome.approval),
            )
        if mesa_outcome.decision == "deny":
            return _tool_error("No accessible entities matched your request."), "denied", tool_name
        entities = mesa_outcome.entities

    call_data = dict(service_data)
    call_data["entity_id"] = entities
    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            await hass.services.async_call(
                service_domain,
                service_name,
                call_data,
                blocking=True,
                return_response=False,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({"success": True, "partial": True, "message": "Action dispatched."})),
            "allowed",
            tool_name,
        )
    except ServiceNotFound:
        return _tool_error("Service call failed."), "denied", tool_name
    except HomeAssistantError:
        return _tool_error("Service call failed."), "denied", tool_name

    success: list[dict] = _build_target_context(args or {}, hass)
    for entity_id in entities:
        state = hass.states.get(entity_id)
        name = state.attributes.get("friendly_name", entity_id) if state else entity_id
        success.append({"name": name, "type": "entity", "id": entity_id})

    return _tool_success(json.dumps({
        "speech": {},
        "response_type": "action_done",
        "data": {"success": success, "failed": []},
    })), "allowed", tool_name


def _resolve_turn_entities(args: dict, token: TokenRecord, hass: Any) -> list[str]:
    return resolve_intent_entities(
        hass, token,
        domains=args.get("domain"),
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )


async def _hass_turn_gate(
    tool_name: str,
    service: str,
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str,
    client_ip: str | None,
) -> tuple[dict, str, str]:
    """Shared gate for HassTurnOn/HassTurnOff.

    homeassistant.turn_on/off route lock/alarm/cover entities to their physical
    services (lock.lock, alarm_control_panel.alarm_arm_*, cover.open_cover), so a
    call that targets any of those is subject to cap_physical_control. When that
    cap is confirm AND physical entities are in scope, the whole call is gated as
    a pending approval (the executor re-runs it on approval). When the cap is deny
    the physical entities are silently dropped inside the executor; non-physical
    entities always proceed immediately.
    """
    entities = _resolve_turn_entities(args, token, hass)
    physical = [e for e in entities if e.split(".")[0] in PHYSICAL_GATE_DOMAINS]
    if physical and effective_cap(token, "cap_physical_control") == CAP_CONFIRM:
        blocked = await _gate(
            "cap_physical_control", token, hass, data,
            tool_name=tool_name, args=args, request_id=request_id,
            client_ip=client_ip, diff=_build_diff_hass_turn(service, physical, args, hass),
        )
        if blocked is not None:
            return blocked
    return await _hass_turn_execute(tool_name, service, args, token, hass, data)


async def _hass_turn_execute(
    tool_name: str, service: str, args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    entities = _resolve_turn_entities(args, token, hass)
    # Drop physical entities only when cap_physical_control is deny. Under allow
    # (direct) or confirm (reached here only after admin approval) they are kept.
    if effective_cap(token, "cap_physical_control") == CAP_DENY:
        entities = [e for e in entities if e.split(".")[0] not in PHYSICAL_GATE_DOMAINS]
    return await _tool_intent_action(tool_name, "homeassistant", service, {}, entities, hass, token, args=args)


async def _tool_hass_turn_on(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    return await _hass_turn_gate("HassTurnOn", "turn_on", args, token, hass, data, request_id, client_ip)


async def _execute_hass_turn_on(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    return await _hass_turn_execute("HassTurnOn", "turn_on", args, token, hass, data)


async def _tool_hass_turn_off(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    return await _hass_turn_gate("HassTurnOff", "turn_off", args, token, hass, data, request_id, client_ip)


async def _execute_hass_turn_off(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    return await _hass_turn_execute("HassTurnOff", "turn_off", args, token, hass, data)


async def _tool_hass_light_set(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    if "brightness" in args and args["brightness"] is not None:
        error = _validate_integer_range("brightness", args["brightness"], 0, 100)
        if error:
            return _tool_error(error), "invalid_request", "HassLightSet"
    if "temperature" in args and args["temperature"] is not None:
        error = _validate_integer_range("temperature", args["temperature"], 0, None)
        if error:
            return _tool_error(error), "invalid_request", "HassLightSet"

    domains = args.get("domain") or ["light"]
    entities = resolve_intent_entities(
        hass, token,
        domains=domains,
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    service_data: dict[str, Any] = {}
    if "brightness" in args and args["brightness"] is not None:
        service_data["brightness_pct"] = args["brightness"]
    if "color" in args and args["color"] is not None:
        service_data["color_name"] = args["color"]
    if "temperature" in args and args["temperature"] is not None:
        service_data["color_temp_kelvin"] = args["temperature"]
    return await _tool_intent_action("HassLightSet", "light", "turn_on", service_data, entities, hass, token, args=args)


async def _tool_hass_fan_set_speed(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    if "percentage" in args and args["percentage"] is not None:
        error = _validate_integer_range("percentage", args["percentage"], 0, 100)
        if error:
            return _tool_error(error), "invalid_request", "HassFanSetSpeed"

    entities = resolve_intent_entities(
        hass, token,
        domains=["fan"],
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    service_data: dict[str, Any] = {}
    if "percentage" in args and args["percentage"] is not None:
        service_data["percentage"] = args["percentage"]
    return await _tool_intent_action("HassFanSetSpeed", "fan", "set_percentage", service_data, entities, hass, token, args=args)


async def _tool_hass_climate_set_temperature(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    if "temperature" in args and args["temperature"] is not None:
        error = _validate_number_range("temperature", args["temperature"], None, None)
        if error:
            return _tool_error(error), "invalid_request", "HassClimateSetTemperature"

    entities = resolve_intent_entities(
        hass, token,
        domains=["climate"],
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    service_data: dict[str, Any] = {}
    if "temperature" in args and args["temperature"] is not None:
        service_data["temperature"] = args["temperature"]
    return await _tool_intent_action("HassClimateSetTemperature", "climate", "set_temperature", service_data, entities, hass, token, args=args)


async def _tool_hass_set_position(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    blocked = await _gate(
        "cap_physical_control", token, hass, data,
        tool_name="HassSetPosition", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_hass_set_position(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_hass_set_position(args, token, hass, data)


async def _execute_hass_set_position(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    if "position" in args and args["position"] is not None:
        error = _validate_integer_range("position", args["position"], 0, 100)
        if error:
            return _tool_error(error), "invalid_request", "HassSetPosition"

    entities = resolve_intent_entities(
        hass, token,
        domains=args.get("domain") or ["cover"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    service_data: dict[str, Any] = {}
    if "position" in args and args["position"] is not None:
        service_data["position"] = args["position"]
    return await _tool_intent_action("HassSetPosition", "cover", "set_cover_position", service_data, entities, hass, token, args=args)


async def _tool_hass_set_volume(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    if "volume_level" in args and args["volume_level"] is not None:
        error = _validate_integer_range("volume_level", args["volume_level"], 0, 100)
        if error:
            return _tool_error(error), "invalid_request", "HassSetVolume"

    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    service_data: dict[str, Any] = {}
    if "volume_level" in args and args["volume_level"] is not None:
        service_data["volume_level"] = args["volume_level"] / 100.0
    return await _tool_intent_action("HassSetVolume", "media_player", "volume_set", service_data, entities, hass, token, args=args)


async def _tool_hass_set_volume_relative(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    if "volume_step" in args and args["volume_step"] is not None:
        step = args["volume_step"]
        if isinstance(step, str):
            error = _validate_string_enum("volume_step", step, ["up", "down"])
            if error:
                return _tool_error(error), "invalid_request", "HassSetVolumeRelative"
        elif isinstance(step, int):
            error = _validate_integer_range("volume_step", step, -100, 100)
            if error:
                return _tool_error(error), "invalid_request", "HassSetVolumeRelative"
        else:
            return _tool_error(f"Input validation error: '{step}' is not of type 'string' or 'integer'"), "invalid_request", "HassSetVolumeRelative"

    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    # Integer step values use sign for direction only; magnitude is discarded.
    # This mirrors native HA's HassSetVolumeRelative intent handler, which calls
    # volume_up/volume_down (fixed-increment services, not adjustable-step).
    step = args.get("volume_step")
    if step == "down" or (isinstance(step, int) and step < 0):
        svc = "volume_down"
    else:
        svc = "volume_up"
    return await _tool_intent_action("HassSetVolumeRelative", "media_player", svc, {}, entities, hass, token, args=args)


async def _tool_hass_media_pause(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    entities = [e for e in entities if (s := hass.states.get(e)) and s.state == "playing"]
    return await _tool_intent_action("HassMediaPause", "media_player", "media_pause", {}, entities, hass, token, args=args)


async def _tool_hass_media_unpause(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    entities = [e for e in entities if (s := hass.states.get(e)) and s.state == "paused"]
    return await _tool_intent_action("HassMediaUnpause", "media_player", "media_play", {}, entities, hass, token, args=args)


async def _tool_hass_media_next(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    entities = [e for e in entities if (s := hass.states.get(e)) and s.state == "playing"]
    return await _tool_intent_action("HassMediaNext", "media_player", "media_next_track", {}, entities, hass, token, args=args)


async def _tool_hass_media_previous(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    entities = [e for e in entities if (s := hass.states.get(e)) and s.state in ("playing", "paused")]
    return await _tool_intent_action("HassMediaPrevious", "media_player", "media_previous_track", {}, entities, hass, token, args=args)


async def _tool_hass_media_search_and_play(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    search_query = args.get("search_query", "")
    media_class = args.get("media_class") or "music"
    service_data: dict[str, Any] = {
        "media_content_id": search_query,
        "media_content_type": media_class,
    }
    return await _tool_intent_action("HassMediaSearchAndPlay", "media_player", "play_media", service_data, entities, hass, token, args=args)


async def _tool_hass_media_player_mute(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    return await _tool_intent_action("HassMediaPlayerMute", "media_player", "volume_mute", {"is_volume_muted": True}, entities, hass, token, args=args)


async def _tool_hass_media_player_unmute(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["media_player"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    return await _tool_intent_action("HassMediaPlayerUnmute", "media_player", "volume_mute", {"is_volume_muted": False}, entities, hass, token, args=args)


async def _tool_hass_cancel_all_timers(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=["timer"],
        area=args.get("area"),
    )
    canceled = len(entities)
    if entities:
        try:
            async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
                await hass.services.async_call(
                    "timer", "cancel", {"entity_id": entities},
                    blocking=True, return_response=False,
                )
        except asyncio.TimeoutError:
            pass
        except ServiceNotFound:
            return _tool_error("Service call failed."), "denied", "HassCancelAllTimers"
        except HomeAssistantError:
            return _tool_error("Service call failed."), "denied", "HassCancelAllTimers"
    return _tool_success(json.dumps({
        "speech": {},
        "response_type": "action_done",
        "data": {"success": [], "failed": []},
        "speech_slots": {"canceled": canceled},
    })), "allowed", "HassCancelAllTimers"


async def _tool_hass_stop_moving(
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    blocked = await _gate(
        "cap_physical_control", token, hass, data,
        tool_name="HassStopMoving", args=args, request_id=request_id,
        client_ip=client_ip, diff=_build_diff_hass_stop_moving(args, token, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_hass_stop_moving(args, token, hass, data)


async def _execute_hass_stop_moving(
    args: dict, token: TokenRecord, hass: Any, data: ATMData
) -> tuple[dict, str, str]:
    entities = resolve_intent_entities(
        hass, token,
        domains=args.get("domain") or ["cover"],
        device_classes=args.get("device_class"),
        name=args.get("name"),
        area=args.get("area"),
        floor=args.get("floor"),
    )
    return await _tool_intent_action("HassStopMoving", "cover", "stop_cover", {}, entities, hass, token, args=args)


async def _tool_hass_broadcast(
    args: dict, token: TokenRecord, hass: Any
) -> tuple[dict, str, str]:
    """MCP tool: HassBroadcast - announce a message via assist satellite devices."""
    if effective_cap(token, "cap_broadcast") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "HassBroadcast"

    message = args.get("message", "")
    if not message:
        return _tool_error("Missing required argument: message"), "invalid_request", "HassBroadcast"

    targets: list[str] = []
    for state in hass.states.async_all():
        if state.entity_id.split(".")[0] != "assist_satellite":
            continue
        features = state.attributes.get("supported_features", 0)
        if isinstance(features, int) and (features & ANNOUNCE_BIT):
            if token.pass_through or resolve(state.entity_id, token, hass) == Permission.WRITE:
                targets.append(state.entity_id)

    if not targets:
        return _tool_error("No accessible broadcast devices found."), "denied", "HassBroadcast"

    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            await hass.services.async_call(
                "assist_satellite",
                "announce",
                {"message": message, "entity_id": targets},
                blocking=True,
                return_response=False,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({"success": True, "partial": True, "message": "Broadcast dispatched."})),
            "allowed",
            "HassBroadcast",
        )
    except ServiceNotFound:
        return _tool_error("Broadcast failed."), "denied", "HassBroadcast"
    except HomeAssistantError:
        return _tool_error("Broadcast failed. No compatible satellite devices found."), "denied", "HassBroadcast"

    return _tool_success(json.dumps({"success": True})), "allowed", "HassBroadcast"


async def _call_tool(
    tool_name: str,
    arguments: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Route a tools/call request to the appropriate tool handler."""
    if tool_name == "get_state":
        return await _tool_get_state(arguments, token, hass)
    if tool_name == "get_states":
        return await _tool_get_states(arguments, token, hass)
    if tool_name == "get_history":
        return await _tool_get_history(arguments, token, hass)
    if tool_name == "get_statistics":
        return await _tool_get_statistics(arguments, token, hass)
    if tool_name == "call_service":
        return await _tool_call_service(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "get_config":
        return await _tool_get_config(arguments, token, hass)
    if tool_name == "render_template":
        return await _tool_render_template(arguments, token, hass)
    if tool_name == "create_automation":
        return await _tool_create_automation(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "edit_automation":
        return await _tool_edit_automation(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "delete_automation":
        return await _tool_delete_automation(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "restart_ha":
        return await _tool_restart_ha(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "get_approval_status":
        return await _tool_get_approval_status(arguments, token, hass, data)
    if tool_name in MESA_TOOL_NAMES:
        return await call_mesa_tool(tool_name, arguments, token, hass, data, request_id)
    if tool_name == "GetLiveContext":
        return await _tool_get_live_context(arguments, token, hass)
    if tool_name == "GetDateTime":
        return await _tool_get_date_time(arguments, token, hass)
    if tool_name == "HassTurnOn":
        return await _tool_hass_turn_on(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "HassTurnOff":
        return await _tool_hass_turn_off(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "HassLightSet":
        return await _tool_hass_light_set(arguments, token, hass)
    if tool_name == "HassFanSetSpeed":
        return await _tool_hass_fan_set_speed(arguments, token, hass)
    if tool_name == "HassClimateSetTemperature":
        return await _tool_hass_climate_set_temperature(arguments, token, hass)
    if tool_name == "HassSetPosition":
        return await _tool_hass_set_position(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "HassSetVolume":
        return await _tool_hass_set_volume(arguments, token, hass)
    if tool_name == "HassSetVolumeRelative":
        return await _tool_hass_set_volume_relative(arguments, token, hass)
    if tool_name == "HassMediaPause":
        return await _tool_hass_media_pause(arguments, token, hass)
    if tool_name == "HassMediaUnpause":
        return await _tool_hass_media_unpause(arguments, token, hass)
    if tool_name == "HassMediaNext":
        return await _tool_hass_media_next(arguments, token, hass)
    if tool_name == "HassMediaPrevious":
        return await _tool_hass_media_previous(arguments, token, hass)
    if tool_name == "HassMediaSearchAndPlay":
        return await _tool_hass_media_search_and_play(arguments, token, hass)
    if tool_name == "HassMediaPlayerMute":
        return await _tool_hass_media_player_mute(arguments, token, hass)
    if tool_name == "HassMediaPlayerUnmute":
        return await _tool_hass_media_player_unmute(arguments, token, hass)
    if tool_name == "HassCancelAllTimers":
        return await _tool_hass_cancel_all_timers(arguments, token, hass)
    if tool_name == "HassStopMoving":
        return await _tool_hass_stop_moving(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "HassBroadcast":
        return await _tool_hass_broadcast(arguments, token, hass)
    if tool_name == "get_logs":
        return await _tool_get_logs(arguments, token, hass)
    if tool_name == "create_script":
        return await _tool_create_script(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "edit_script":
        return await _tool_edit_script(arguments, token, hass, data, request_id, client_ip)
    if tool_name == "delete_script":
        return await _tool_delete_script(arguments, token, hass, data, request_id, client_ip)
    return _tool_error(f"Unknown tool: {tool_name}"), "denied", tool_name


def _resolve_area_id(entry: Any, device_registry: Any) -> str | None:
    """Return the area_id for an entity registry entry, falling back to the device's area."""
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = device_registry.async_get(entry.device_id)
        if device and device.area_id:
            return device.area_id
    return None


async def _get_ha_assist_api(hass: Any) -> Any:
    """Return HA's Assist LLM APIInstance, or raise if unavailable."""
    from homeassistant.helpers import llm as _ha_llm
    llm_context = _ha_llm.LLMContext(
        platform=DOMAIN,
        context=None,
        user_prompt=None,
        language="en",
        assistant="conversation",
        device_id=None,
    )
    return await _ha_llm.async_get_api(hass, _ha_llm.LLM_API_ASSIST, llm_context)


def _build_server_info(token: TokenRecord, hass: Any, base_url: str) -> dict:
    """Build the atm://server-info resource payload for the MCP resources/read endpoint."""
    states = hass.states.async_all()
    if token.pass_through:
        # Use build_permitted_states to get the same set the token actually sees,
        # including the ATM-platform entity filter (sensor.atm_* telemetry sensors).
        count = len(_build_permitted_states(token, hass))
    else:
        filtered = filter_entities_for_token(states, token, hass)
        count = len(filtered)

    return {
        "name": "ATM Scoped Proxy",
        "version": ATM_VERSION,
        "token_name": token.name,
        "permitted_entity_count": count,
        "capability_flags": effective_caps(token),
        "persona": token.persona,
        "native_ha_mcp_endpoint": f"{base_url}/api/mcp",
        "atm_context_endpoint": f"{base_url}/api/atm/mcp/context",
    }


def _build_context_plain(token: TokenRecord, hass: Any) -> str:
    """Build the plain-text context document listing accessible entities and capabilities."""
    lines: list[str] = []

    if token.pass_through:
        # Use build_permitted_states for an accurate count that respects ATM-platform
        # entity filtering and use_assist_exposure (same set the token actually sees).
        count = len(_build_permitted_states(token, hass))
        lines.append("This token operates in pass-through mode.")
        lines.append(
            f"It has unrestricted access to all {count} accessible Home Assistant entities and services."
        )
        lines.append("")
        lines.append("The atm domain is always blocked regardless of token type.")
    else:
        states = hass.states.async_all()
        accessible: list[tuple[str, str, str | None]] = []
        for state in states:
            perm = resolve(state.entity_id, token, hass)
            if perm == Permission.WRITE:
                accessible.append((state.entity_id, "READ/WRITE", get_effective_hint(token, state.entity_id, hass)))
            elif perm == Permission.READ:
                accessible.append((state.entity_id, "READ", get_effective_hint(token, state.entity_id, hass)))

        accessible.sort(key=lambda x: x[0])
        lines.append("You have access to the following Home Assistant entities:")
        if accessible:
            for eid, perm_str, hint in accessible:
                hint_part = f' - "{hint}"' if hint else ""
                lines.append(f"- {eid} ({perm_str}){hint_part}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append(
            "You cannot access any other entities. "
            "Do not attempt to call services on entities not listed above."
        )

    lines.append("")
    caps = effective_caps(token)
    lines.append("Capabilities (deny / allow / confirm; confirm requires admin approval per request):")
    label_map = (
        ("cap_config_read", "Config read"),
        ("cap_automation_write", "Automation write"),
        ("cap_script_write", "Script write"),
        ("cap_template_render", "Template render"),
        ("cap_restart", "Restart"),
        ("cap_physical_control", "Physical control (locks/alarms/covers)"),
        ("cap_broadcast", "Broadcast"),
        ("cap_log_read", "Log read"),
        ("cap_service_response", "Service response"),
    )
    for cap_key, label in label_map:
        lines.append(f"- {label}: {caps.get(cap_key, 'deny')}")
    lines.append("")
    if token.rate_limit_requests > 0:
        lines.append(
            f"Rate limit: {token.rate_limit_requests} requests/min, burst {token.rate_limit_burst}/sec"
        )
    else:
        lines.append("Rate limit: none")

    return "\n".join(lines)


def _build_context_json(token: TokenRecord, hass: Any) -> dict:
    """Build the structured JSON context document for the ?format=json context endpoint."""
    registry = er.async_get(hass)
    dev_registry = dr.async_get(hass)

    entities: list[dict] = []
    states = hass.states.async_all()

    if token.pass_through:
        _expose_check = None
        if token.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        for state in states:
            eid = state.entity_id
            if eid.split(".")[0] in BLOCKED_DOMAINS:
                continue
            entry = registry.async_get(eid)
            # Exclude ATM telemetry sensors (registered to the atm platform) so
            # pass_through tokens see the same entity set as build_permitted_states().
            if entry is not None and entry.platform == DOMAIN:
                continue
            if _expose_check is not None and not _expose_check(eid):
                continue
            area_id = _resolve_area_id(entry, dev_registry)
            entities.append({
                "entity_id": eid,
                "permission": "READ/WRITE",
                "area_id": area_id,
            })
    else:
        for state in states:
            perm = resolve(state.entity_id, token, hass)
            if perm not in (Permission.READ, Permission.WRITE):
                continue
            entry = registry.async_get(state.entity_id)
            area_id = _resolve_area_id(entry, dev_registry)
            perm_str = "READ/WRITE" if perm == Permission.WRITE else "READ"
            e: dict = {"entity_id": state.entity_id, "permission": perm_str, "area_id": area_id}
            hint = get_effective_hint(token, state.entity_id, hass)
            if hint:
                e["hint"] = hint
            entities.append(e)

    entities.sort(key=lambda e: e["entity_id"])

    return {
        "token_name": token.name,
        "pass_through": token.pass_through,
        "persona": token.persona,
        "entities": entities,
        "capability_flags": effective_caps(token),
        "rate_limit": {
            "requests_per_minute": token.rate_limit_requests,
            "burst_per_second": token.rate_limit_burst,
        },
    }


async def _dispatch_mcp(
    method: str,
    msg_id: Any,
    params: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    client_ip: str,
    base_url: str,
    protocol_version: str = _MCP_VERSION_SSE,
) -> tuple[dict | None, str, str, str]:
    """Dispatch one MCP method call.

    Returns (response_msg, log_method, log_resource, outcome).
    response_msg is None for notifications that require no response.
    protocol_version is returned in initialize responses to reflect the active transport.
    """
    request_id = generate_request_id()

    if method == "initialize":
        resp = _jsonrpc_result(msg_id, {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": False},
                "prompts": {},
            },
            "serverInfo": {"name": "ATM", "version": ATM_VERSION},
        })
        _log(data, token, request_id=request_id, method="initialize",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "initialize", "/api/atm/mcp", "allowed"

    if method in ("notifications/initialized", "initialized"):
        _log(data, token, request_id=request_id, method=method,
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return None, method, "/api/atm/mcp", "allowed"

    if method == "ping":
        resp = _jsonrpc_result(msg_id, {})
        _log(data, token, request_id=request_id, method="ping",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "ping", "/api/atm/mcp", "allowed"

    if method == "tools/list":
        tools = list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS)
        mesa_defs = mesa_tool_defs() if data.mesa is not None else []
        for tool_def in list(_SYSTEM_TOOL_DEFS) + mesa_defs:
            cap = tool_def["cap"]
            if effective_cap(token, cap) != CAP_DENY:
                tools.append({k: v for k, v in tool_def.items() if k != "cap"})
        resp = _jsonrpc_result(msg_id, {"tools": tools})
        _log(data, token, request_id=request_id, method="tools/list",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "tools/list", "/api/atm/mcp", "allowed"

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        tool_result, outcome, resource = await _call_tool(
            tool_name, arguments, token, hass, data,
            request_id=request_id, client_ip=client_ip,
        )
        _log(data, token, request_id=request_id, method=tool_name or "tools/call",
             resource=resource, outcome=outcome, client_ip=client_ip,
             payload={"name": tool_name, "arguments": arguments})
        return _jsonrpc_result(msg_id, tool_result), tool_name or "tools/call", resource, outcome

    if method == "resources/list":
        resp = _jsonrpc_result(msg_id, {
            "resources": [
                {
                    "uri": "homeassistant://assist/context-snapshot",
                    "name": "Assist Context Snapshot",
                    "description": "A snapshot of the current Assist context, matching the existing GetLiveContext tool output",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "atm://server-info",
                    "name": "ATM Server Info",
                    "mimeType": "application/json",
                },
            ]
        })
        _log(data, token, request_id=request_id, method="resources/list",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "resources/list", "/api/atm/mcp", "allowed"

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "homeassistant://assist/context-snapshot":
            context_text = _build_live_context(token, hass)
            resp = _jsonrpc_result(msg_id, {
                "contents": [{
                    "uri": "homeassistant://assist/context-snapshot",
                    "mimeType": "text/plain",
                    "text": context_text,
                }]
            })
            _log(data, token, request_id=request_id, method="resources/read",
                 resource="homeassistant://assist/context-snapshot", outcome="allowed", client_ip=client_ip)
            return resp, "resources/read", "homeassistant://assist/context-snapshot", "allowed"
        if uri != "atm://server-info":
            if msg_id is not None:
                _log(data, token, request_id=request_id, method="resources/read",
                     resource=uri or "/api/atm/mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32602, "Unknown resource URI."), "resources/read", uri, "denied"
            return None, "resources/read", uri, "denied"
        server_info = _build_server_info(token, hass, base_url)
        resp = _jsonrpc_result(msg_id, {
            "contents": [{
                "uri": "atm://server-info",
                "mimeType": "application/json",
                "text": json.dumps(server_info, default=str),
            }]
        })
        _log(data, token, request_id=request_id, method="resources/read",
             resource="atm://server-info", outcome="allowed", client_ip=client_ip)
        return resp, "resources/read", "atm://server-info", "allowed"

    if method == "prompts/list":
        if token.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                prompt_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                prompts = [{"name": prompt_name, "description": f"Default prompt for Home Assistant {api_inst.api.name} API"}]
            except Exception:
                prompts = []
        else:
            prompts = [{
                "name": "ATM access context",
                "description": "Describes the Home Assistant entities and capabilities accessible to this token",
            }]
        resp = _jsonrpc_result(msg_id, {"prompts": prompts})
        _log(data, token, request_id=request_id, method="prompts/list",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/list", "/api/atm/mcp", "allowed"

    if method == "prompts/get":
        name = params.get("name", "")
        if token.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                expected_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                if name != expected_name:
                    _log(data, token, request_id=request_id, method="prompts/get",
                         resource="/api/atm/mcp", outcome="denied", client_ip=client_ip)
                    return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/atm/mcp", "denied"
                resp = _jsonrpc_result(msg_id, {
                    "description": f"Default prompt for Home Assistant {api_inst.api.name} API",
                    "messages": [{"role": "assistant", "content": {"type": "text", "text": api_inst.api_prompt}}],
                })
            except Exception:
                _log(data, token, request_id=request_id, method="prompts/get",
                     resource="/api/atm/mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32603, "Prompt unavailable."), "prompts/get", "/api/atm/mcp", "denied"
        else:
            if name != "ATM access context":
                _log(data, token, request_id=request_id, method="prompts/get",
                     resource="/api/atm/mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/atm/mcp", "denied"
            prompt_text = _build_context_plain(token, hass)
            resp = _jsonrpc_result(msg_id, {
                "description": "Describes the Home Assistant entities and capabilities accessible to this token",
                "messages": [{"role": "assistant", "content": {"type": "text", "text": prompt_text}}],
            })
        _log(data, token, request_id=request_id, method="prompts/get",
             resource="/api/atm/mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/get", "/api/atm/mcp", "allowed"

    if msg_id is not None:
        _log(data, token, request_id=request_id, method=method or "unknown",
             resource="/api/atm/mcp", outcome="not_implemented", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32601, "Method not found."), method or "unknown", "/api/atm/mcp", "not_implemented"

    return None, method or "unknown", "/api/atm/mcp", "not_implemented"


async def _handle_streamable_batch(
    items: list,
    token: TokenRecord,
    rl_result: RateLimitResult,
    hass: Any,
    data: ATMData,
    request_id: str,
    client_ip: str,
    base_url: str,
) -> web.Response:
    """Dispatch a JSON-RPC batch array per MCP 2025-03-26.

    Each item is dispatched independently. Failed items produce per-item error objects
    rather than failing the whole batch. Notifications (no id) produce no response entry.
    Returns 202 when all items are notifications; 200 with a results array otherwise.
    """
    if not items:
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, "Empty batch.")),
            headers={"X-ATM-Request-ID": request_id},
        )

    # Batch rate limiting design: each batch consumes ONE rate-limit token, not one
    # per item. Per-item counting would let a single 50-item batch exhaust a token's
    # entire 60 req/min budget, making batching worse than sequential calls. The
    # MAX_BATCH_ITEMS cap bounds the multiplier to 50x, which is an acceptable
    # tradeoff for MCP batch usability. Reviewed and accepted in audit 2026-04-19.
    if len(items) > MAX_BATCH_ITEMS:
        return web.Response(
            status=400,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, f"Batch too large. Maximum {MAX_BATCH_ITEMS} items.")),
            headers={"X-ATM-Request-ID": request_id},
        )

    async def _dispatch_one(item: Any) -> dict | None:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            msg_id = _sanitize_jsonrpc_id(item.get("id")) if isinstance(item, dict) else None
            return _jsonrpc_error(msg_id, -32600, "Invalid Request.")
        msg_id = _sanitize_jsonrpc_id(item.get("id"))
        method = item.get("method", "")
        params = item.get("params") or {}
        response_msg, _, _, _ = await _dispatch_mcp(
            method, msg_id, params, token, hass, data, client_ip,
            protocol_version=_MCP_VERSION_STREAMABLE,
            base_url=base_url,
        )
        return response_msg

    raw_results = await asyncio.gather(
        *[_dispatch_one(item) for item in items],
        return_exceptions=True,
    )

    responses = []
    for item, r in zip(items, raw_results):
        if isinstance(r, Exception):
            responses.append(_jsonrpc_error(_sanitize_jsonrpc_id(item.get("id")), -32603, "Internal error."))
        elif r is not None:
            responses.append(r)

    if not responses:
        return web.Response(status=202, headers={"X-ATM-Request-ID": request_id})

    resp = web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(responses, default=str),
        headers={"X-ATM-Request-ID": request_id},
    )
    if token.rate_limit_requests > 0:
        resp.headers["X-RateLimit-Limit"] = str(token.rate_limit_requests)
        resp.headers["X-RateLimit-Remaining"] = str(rl_result.remaining)
        resp.headers["X-RateLimit-Reset"] = str(rl_result.reset)
    return resp


class ATMMcpSseView(HomeAssistantView):
    """GET /api/atm/mcp - SSE endpoint (MCP 2024-11-05 transport).
    POST /api/atm/mcp - Streamable HTTP transport (MCP 2025-03-26).
    """

    url = "/api/atm/mcp"
    name = "api:atm:mcp:sse"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse | web.Response:
        """Open an SSE stream. Sends an 'endpoint' event with the messages URL, then heartbeats."""
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        request_id = generate_request_id()
        client_ip = _get_client_ip(request)

        if data.shutting_down:
            return _error("service_unavailable", "Service unavailable.", 503, request_id)

        if data.store.get_settings().kill_switch:
            return _error("service_unavailable", "Service unavailable.", 503, request_id)

        _401 = _error("unauthorized", "Unauthorized.", 401, request_id)
        _401.headers["WWW-Authenticate"] = 'Bearer realm="ATM"'

        for key in ("token", "access_token"):

            if key in request.query:
                return _401

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _401

        presented = auth_header[7:]
        if not presented.startswith(TOKEN_PREFIX) or len(presented) != TOKEN_LENGTH:
            return _401

        token_hash = hashlib.sha256(presented.encode()).hexdigest()
        token = data.store.get_token_by_hash(token_hash)
        if token is None:
            return _401

        if not token.is_valid():
            if token.is_expired():
                await archive_expired_token(hass, data, token)
            return _401

        # SSE connection limit check intentionally runs after full token validation.
        # Moving it before auth would create a connection-count oracle: an attacker
        # presenting a valid-format token could distinguish "token exists and is maxed"
        # (429) from "token doesn't exist or isn't maxed" (401). Since ATM token space
        # is 2^256 the practical risk is negligible, but the check is cheap so there is
        # no performance reason to move it earlier. This is a deliberate deviation from
        # the CLAUDE.md rule 19 wording "before full token validation".
        #
        # The rate-limit check runs BEFORE the queue/session are registered so a
        # rejected connection allocates nothing. Doing it after registration (as a
        # previous version did) leaked a connection slot on every 429 because the
        # early return skipped cleanup, eventually locking the token out of SSE.
        data.store.update_last_used(token.id, utcnow())

        rl_result = data.rate_limiter.check(token.id, token.rate_limit_requests, token.rate_limit_burst)
        if not rl_result.allowed:
            _fire_rate_limit_events(hass, data, token)
            _log(data, token, request_id=request_id, method="GET", resource="/api/atm/mcp",
                 outcome="rate_limited", client_ip=client_ip)
            resp = _error("rate_limited", "Rate limit exceeded.", 429, request_id)
            resp.headers["Retry-After"] = str(rl_result.retry_after)
            return resp

        # The lock serialises the count-check-and-add to prevent two concurrent
        # connections from both passing the limit check (TOCTOU race).
        async with data.sse_connect_lock:
            current_count = len(data.sse_connections.get(token.id, set()))
            if current_count >= MAX_SSE_CONNECTIONS_PER_TOKEN:
                _log(data, token, request_id=request_id, method="GET", resource="/api/atm/mcp",
                     outcome="rate_limited", client_ip=client_ip)
                resp = _error("rate_limited", "Too many SSE connections for this token.", 429, request_id)
                resp.headers["Retry-After"] = "60"
                return resp

            session_id = str(uuid.uuid4())
            queue: asyncio.Queue = asyncio.Queue(maxsize=100)

            data.mcp_sessions[session_id] = (queue, token.id)
            if token.id not in data.sse_connections:
                data.sse_connections[token.id] = set()
            data.sse_connections[token.id].add(queue)

        _log(data, token, request_id=request_id, method="GET", resource="/api/atm/mcp",
             outcome="allowed", client_ip=client_ip)

        def _cleanup() -> None:
            data.mcp_sessions.pop(session_id, None)
            conns = data.sse_connections.get(token.id)
            if conns is not None:
                conns.discard(queue)
                if not conns:
                    data.sse_connections.pop(token.id, None)

        # Re-check token after queue insertion to close the TOCTOU window where
        # a DELETE/archive could have run between auth check and queue add.
        # Must happen BEFORE response.prepare() - once headers are sent the
        # connection is bound to SSE and returning a web.Response is invalid.
        if data.store.get_token_by_id(token.id) is None:
            _cleanup()
            return _error("unauthorized", "Unauthorized.", 401, request_id)

        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["X-ATM-Request-ID"] = request_id

        try:
            await response.prepare(request)

            base_url = str(request.url.origin())
            messages_url = f"{base_url}/api/atm/mcp/messages?session_id={session_id}"
            await response.write(f"event: endpoint\ndata: {messages_url}\n\n".encode())

            session_epoch = data.wipe_epoch
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            queue.get(),
                            timeout=SSE_HEARTBEAT_INTERVAL.total_seconds(),
                        )
                    except asyncio.TimeoutError:
                        if data.wipe_epoch != session_epoch:
                            break
                        if data.store.get_settings().kill_switch:
                            break
                        await response.write(b": heartbeat\n\n")
                        continue
                    if msg is None:
                        break
                    await response.write(
                        f"event: message\ndata: {json.dumps(msg, default=str)}\n\n".encode()
                    )
            except ConnectionResetError:
                pass
        finally:
            _cleanup()

        return response

    async def post(self, request: web.Request) -> web.Response:
        """Handle Streamable HTTP transport (MCP 2025-03-26)."""
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        request_id = generate_request_id()
        client_ip = _get_client_ip(request)

        result = await _get_authenticated_token(
            hass, request, data, request_id, "/api/atm/mcp"
        )
        if isinstance(result, web.Response):
            return result
        token, rl_result = result

        from .const import MAX_REQUEST_BODY_BYTES as _MAX_BODY
        if request.content_length is not None and request.content_length > _MAX_BODY:
            return _error("request_too_large", "Request body too large.", 413, request_id)
        try:
            body_bytes = await request.content.read(_MAX_BODY + 1)
        except Exception:
            return _error("invalid_request", "Failed to read request body.", 400, request_id)
        if len(body_bytes) > _MAX_BODY:
            return _error("request_too_large", "Request body too large.", 413, request_id)
        if not body_bytes:
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32700, "Parse error.")),
                headers={"X-ATM-Request-ID": request_id},
            )
        try:
            parsed = json.loads(body_bytes)
        except json.JSONDecodeError:
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32700, "Parse error.")),
                headers={"X-ATM-Request-ID": request_id},
            )

        if isinstance(parsed, list):
            return await _handle_streamable_batch(parsed, token, rl_result, hass, data, request_id, client_ip, base_url=str(request.url.origin()))

        if not isinstance(parsed, dict):
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32600, "Invalid Request.")),
                headers={"X-ATM-Request-ID": request_id},
            )

        body = parsed
        if body.get("jsonrpc") != "2.0":
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(_sanitize_jsonrpc_id(body.get("id")), -32600, "Invalid Request.")),
                headers={"X-ATM-Request-ID": request_id},
            )

        msg_id = _sanitize_jsonrpc_id(body.get("id"))
        method = body.get("method", "")
        params = body.get("params") or {}

        response_msg, _log_method, _log_resource, _outcome = await _dispatch_mcp(
            method, msg_id, params, token, hass, data, client_ip,
            protocol_version=_MCP_VERSION_STREAMABLE,
            base_url=str(request.url.origin()),
        )

        if response_msg is None:
            return web.Response(status=202, headers={"X-ATM-Request-ID": request_id})

        resp = web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(response_msg, default=str),
            headers={"X-ATM-Request-ID": request_id},
        )
        if token.rate_limit_requests > 0:
            resp.headers["X-RateLimit-Limit"] = str(token.rate_limit_requests)
            resp.headers["X-RateLimit-Remaining"] = str(rl_result.remaining)
            resp.headers["X-RateLimit-Reset"] = str(rl_result.reset)
        return resp


class ATMMcpMessagesView(HomeAssistantView):
    """POST /api/atm/mcp/messages - receive a JSON-RPC message and push the response to the SSE queue."""

    url = "/api/atm/mcp/messages"
    name = "api:atm:mcp:messages"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        request_id = generate_request_id()
        client_ip = _get_client_ip(request)

        result = await _get_authenticated_token(
            hass, request, data, request_id, "/api/atm/mcp/messages"
        )
        if isinstance(result, web.Response):
            return result
        token, _rl = result

        body_result = await _read_json_body(request, request_id)
        if isinstance(body_result, web.Response):
            return body_result
        body = body_result

        session_id = request.query.get("session_id", "")
        session_entry = data.mcp_sessions.get(session_id)
        if session_entry is None:
            return _error("invalid_request", "Invalid or expired session.", 400, request_id)

        queue, owner_token_id = session_entry
        if owner_token_id != token.id:
            return _error("unauthorized", "Unauthorized.", 401, request_id)

        if body.get("jsonrpc") != "2.0":
            if body.get("id") is not None:
                try:
                    queue.put_nowait(_jsonrpc_error(_sanitize_jsonrpc_id(body.get("id")), -32600, "Invalid Request."))
                except asyncio.QueueFull:
                    return _error("service_unavailable", "SSE queue full; client is not reading.", 503, request_id)
            return web.Response(
                status=202,
                headers={"X-ATM-Request-ID": request_id},
            )

        msg_id = _sanitize_jsonrpc_id(body.get("id"))
        method = body.get("method", "")
        params = body.get("params") or {}

        response_msg, _log_method, _log_resource, _outcome = await _dispatch_mcp(
            method, msg_id, params, token, hass, data, client_ip,
            protocol_version=_MCP_VERSION_SSE,
            base_url=str(request.url.origin()),
        )

        if response_msg is not None:
            try:
                queue.put_nowait(response_msg)
            except asyncio.QueueFull:
                return _error("service_unavailable", "SSE queue full; client is not reading.", 503, request_id)

        return web.Response(
            status=202,
            headers={"X-ATM-Request-ID": request_id},
        )


class ATMMcpContextView(HomeAssistantView):
    """GET /api/atm/mcp/context - context document listing accessible entities and capability flags.

    Returns plain text by default; pass ?format=json for a structured JSON response.
    """

    url = "/api/atm/mcp/context"
    name = "api:atm:mcp:context"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        request_id = generate_request_id()
        client_ip = _get_client_ip(request)

        result = await _get_authenticated_token(
            hass, request, data, request_id, "/api/atm/mcp/context"
        )
        if isinstance(result, web.Response):
            return result
        token, _rl = result

        _log(data, token, request_id=request_id, method="GET", resource="/api/atm/mcp/context",
             outcome="allowed", client_ip=client_ip)

        fmt = request.query.get("format", "")
        if fmt == "json":
            body = _build_context_json(token, hass)
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(body, default=str),
                headers={"X-ATM-Request-ID": request_id},
            )

        text = _build_context_plain(token, hass)
        return web.Response(
            status=200,
            content_type="text/plain",
            text=text,
            headers={"X-ATM-Request-ID": request_id},
        )


ALL_MCP_VIEWS: list[type[HomeAssistantView]] = [
    ATMMcpSseView,
    ATMMcpMessagesView,
    ATMMcpContextView,
]


# --- Diff builders for Confirm-eligible tools ---------------------------------
# Each builder produces the structured payload shown in the admin Approvals UI.
# Diffs are best-effort: anything missing or non-fatal renders an empty preview
# rather than blocking creation of the pending approval.


def _truncate(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... ({len(text) - max_chars} more characters)"


def _build_diff_call_service(args: dict, token: TokenRecord, hass: Any) -> dict:
    """Diff for call_service. Resolves entity targets read-only and lists service params."""
    domain = args.get("domain", "")
    service = args.get("service", "")
    service_data = args.get("service_data") or {}
    entity_arg = args.get("entity_id")
    if isinstance(entity_arg, str):
        entity_ids: list[str] = [entity_arg]
    elif isinstance(entity_arg, list):
        entity_ids = [e for e in entity_arg if isinstance(e, str)]
    else:
        entity_ids = []
    return {
        "kind": "service_preview",
        "summary": f"Call {domain}/{service}",
        "target": {"type": "service", "id": f"{domain}/{service}", "label": f"{domain}/{service}"},
        "preview": {
            "domain": domain,
            "service": service,
            "service_data": service_data if isinstance(service_data, dict) else {},
            "requested_entity_ids": entity_ids,
            "device_id": args.get("device_id"),
            "area_id": args.get("area_id"),
        },
    }


def _build_diff_create_automation(args: dict, token: TokenRecord, hass: Any) -> dict:
    config = args.get("config") if isinstance(args.get("config"), dict) else {}
    return {
        "kind": "config_diff",
        "summary": f"Create automation '{config.get('alias', '<no alias>')}'",
        "target": {"type": "automation", "id": None, "label": config.get("alias")},
        "before": None,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode")},
    }


def _build_diff_edit_automation(args: dict, token: TokenRecord, hass: Any) -> dict:
    automation_id = (args.get("automation_id") or "").strip()
    config = args.get("config") if isinstance(args.get("config"), dict) else {}
    before = None
    try:
        path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
        if os.path.exists(path):
            items = _read_automations_yaml(path)
            current = next((a for a in items if a.get("id") == automation_id), None)
            if current is not None:
                before = _truncate(json.dumps(current, indent=2, default=str))
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    return {
        "kind": "yaml_diff",
        "summary": f"Edit automation '{automation_id}'",
        "target": {"type": "automation", "id": automation_id, "label": config.get("alias")},
        "before": before,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode")},
    }


def _build_diff_delete_automation(args: dict, token: TokenRecord, hass: Any) -> dict:
    automation_id = (args.get("automation_id") or "").strip()
    before = None
    try:
        path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
        if os.path.exists(path):
            items = _read_automations_yaml(path)
            current = next((a for a in items if a.get("id") == automation_id), None)
            if current is not None:
                before = _truncate(json.dumps(current, indent=2, default=str))
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    return {
        "kind": "system_action",
        "summary": f"Delete automation '{automation_id}'",
        "target": {"type": "automation", "id": automation_id, "label": automation_id},
        "before": before,
        "preview": {"warning": "This automation will be removed permanently."},
    }


def _build_diff_create_script(args: dict, token: TokenRecord, hass: Any) -> dict:
    script_id = (args.get("script_id") or "").strip()
    config = args.get("config") if isinstance(args.get("config"), dict) else {}
    return {
        "kind": "config_diff",
        "summary": f"Create script '{script_id}'",
        "target": {"type": "script", "id": script_id, "label": config.get("alias")},
        "before": None,
        "after": _truncate(json.dumps({script_id: config}, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode")},
    }


def _build_diff_edit_script(args: dict, token: TokenRecord, hass: Any) -> dict:
    script_id = (args.get("script_id") or "").strip()
    config = args.get("config") if isinstance(args.get("config"), dict) else {}
    before = None
    try:
        path = hass.config.path(_SCRIPT_CONFIG_PATH)
        if os.path.exists(path):
            scripts = _read_scripts_yaml(path)
            current = scripts.get(script_id)
            if current is not None:
                before = _truncate(json.dumps({script_id: current}, indent=2, default=str))
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    return {
        "kind": "yaml_diff",
        "summary": f"Edit script '{script_id}'",
        "target": {"type": "script", "id": script_id, "label": config.get("alias")},
        "before": before,
        "after": _truncate(json.dumps({script_id: config}, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode")},
    }


def _build_diff_delete_script(args: dict, token: TokenRecord, hass: Any) -> dict:
    script_id = (args.get("script_id") or "").strip()
    before = None
    try:
        path = hass.config.path(_SCRIPT_CONFIG_PATH)
        if os.path.exists(path):
            scripts = _read_scripts_yaml(path)
            current = scripts.get(script_id)
            if current is not None:
                before = _truncate(json.dumps({script_id: current}, indent=2, default=str))
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    return {
        "kind": "system_action",
        "summary": f"Delete script '{script_id}'",
        "target": {"type": "script", "id": script_id, "label": script_id},
        "before": before,
        "preview": {"warning": "This script will be removed permanently."},
    }


def _build_diff_hass_turn(service: str, physical: list[str], args: dict, hass: Any) -> dict:
    """Diff payload for a HassTurnOn/Off approval triggered by physical entities.

    Only the physical (lock/alarm/cover) targets are listed, since those are the
    entities cap_physical_control gates; non-physical targets are not part of the
    approval decision.
    """
    targets = []
    for eid in physical:
        state = hass.states.get(eid)
        label = str(state.attributes.get("friendly_name") or eid) if state else eid
        targets.append({"entity_id": eid, "name": label})
    verb = "on" if service == "turn_on" else "off"
    return {
        "kind": "service_preview",
        "summary": f"Turn {verb} physical device(s): {', '.join(t['name'] for t in targets)}",
        "target": {"type": "service", "id": f"homeassistant/{service}", "label": f"homeassistant/{service}"},
        "preview": {
            "physical_targets": targets,
            "name": args.get("name"),
            "area": args.get("area"),
            "floor": args.get("floor"),
            "domain": args.get("domain"),
            "device_class": args.get("device_class"),
        },
    }


def _build_diff_hass_set_position(args: dict, token: TokenRecord, hass: Any) -> dict:
    return {
        "kind": "service_preview",
        "summary": "Set cover position",
        "target": {"type": "service", "id": "cover/set_cover_position", "label": "cover/set_cover_position"},
        "preview": {
            "position": args.get("position"),
            "name": args.get("name"),
            "area": args.get("area"),
            "floor": args.get("floor"),
            "domain": args.get("domain"),
            "device_class": args.get("device_class"),
        },
    }


def _build_diff_hass_stop_moving(args: dict, token: TokenRecord, hass: Any) -> dict:
    return {
        "kind": "service_preview",
        "summary": "Stop moving cover",
        "target": {"type": "service", "id": "cover/stop_cover", "label": "cover/stop_cover"},
        "preview": {
            "name": args.get("name"),
            "area": args.get("area"),
            "floor": args.get("floor"),
            "domain": args.get("domain"),
            "device_class": args.get("device_class"),
        },
    }


# Register executors for tools that support the admin-approval gate.
# Each entry maps an MCP tool name to its side-effect-only _execute_X function.
# When an admin approves a pending request, execute_approved_tool() invokes the
# matching executor with the saved args.
_register_executor("restart_ha", _execute_restart_ha)
_register_executor("call_service", _execute_call_service)
_register_executor("create_automation", _execute_create_automation)
_register_executor("edit_automation", _execute_edit_automation)
_register_executor("delete_automation", _execute_delete_automation)
_register_executor("create_script", _execute_create_script)
_register_executor("edit_script", _execute_edit_script)
_register_executor("delete_script", _execute_delete_script)
_register_executor("HassSetPosition", _execute_hass_set_position)
_register_executor("HassStopMoving", _execute_hass_stop_moving)
_register_executor("HassTurnOn", _execute_hass_turn_on)
_register_executor("HassTurnOff", _execute_hass_turn_off)
# MESA control_mode:confirm re-execution. Registered but intentionally NOT
# dispatchable from _call_tool, so only the admin approve path can reach it.
_register_executor(MESA_APPROVED_EXECUTOR, _execute_call_service_mesa_approved)
