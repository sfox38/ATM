# Advanced Token Management (ATM)

Why give your AI agent basically unrestricted access to your home? ATM is a drop-in replacement for Home Assistant's native MCP server. Security focused, ATM implements all 20 native HA MCP tools so your existing AI client setup works without changes.

The big difference is control and security: each client gets its own token scoped to exactly the entities you allow, with its own rate limit and optional expiry. Every request is logged. If a token is ever compromised, one click revokes it and terminates all open connections immediately.

ATM runs entirely inside Home Assistant. No extra process, no cloud dependency, no configuration beyond the ATM panel.

---

## Why ATM instead of an LLAT

| | LLAT + native MCP | ATM token |
|---|---|---|
| MCP tool compatibility | 20 native tools | Same 20 tools, identical names and responses; plus 16 additional tools |
| MCP Prompts and Resources | Native HA behavior | Identical for pass-through tokens; permission-scoped for scoped tokens |
| Client reconfiguration needed | /api/mcp | /api/atm/mcp (URL change only) |
| Entity filtering | Binary: expose/hide, same for all clients | Four permission states, per token |
| Per-client control | No - all clients see the same exposed entities | Yes - each token has independent permissions |
| Read-only access | No | Yes - YELLOW state allows reads, blocks writes |
| Audit trail | None | Every request logged with outcome and entity |
| Rate limiting | None | Per-token, configurable |
| Expiry | None | Optional, auto-archived on expiry |
| Revocation | Revoke LLAT via HA profile page | Instant, terminates open connections immediately |
| Sensitive attribute scrubbing | None | Always applied |

If you are connecting Claude Code, Cursor, ChatGPT, Antigravity, or any other AI tool to your Home Assistant, ATM gives you control that the native system cannot provide.

---

## Table of Contents

**Getting started**
- [Requirements](#requirements)
- [Installation](#installation)
- [Connecting Claude Code via MCP](#connecting-claude-code-via-mcp)
- [Available MCP Tools](#available-mcp-tools)
- [Tools Reference](#tools-reference)
- [Using Third-Party MCP Servers](#using-third-party-mcp-servers)

**Reference**
- [The Permissions Panel](#the-permissions-panel)
- [The Permission System](#the-permission-system)
- [Capability Flags](#capability-flags)
- [Pass-Through Mode](#pass-through-mode)
- [Rate Limiting](#rate-limiting)
- [Security](#security)
- [Telemetry and Sensors](#telemetry-and-sensors)
- [Global Settings](#global-settings)
- [Audit Log](#audit-log)
- [HA Events](#ha-events)
- [Route Reference](#route-reference)

---

## Requirements

- Home Assistant 2024.5 or later

---

## Installation

### Via HACS

1. In HACS, go to **Integrations** and click the menu in the top-right corner.
2. Choose **Custom repositories**.
3. Enter `https://github.com/sfox38/atm` and select **Integration** as the category.
4. Click **Add**, then find ATM in the HACS integration list and install it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/atm` folder into your HA config directory under `custom_components/atm`.
2. Restart Home Assistant.

### Setup

After installation, go to **Settings > Devices and services > Add integration** and search for "Advanced Token Management". Click through the single-step config flow. Only one ATM instance can be configured at a time.

> [!NOTE]
> Once installed, open the **ATM** panel in your Home Assistant sidebar to manage your tokens.

> [!TIP]
> **Migrating from the native HA MCP server?** ATM replaces it entirely. Once you have created a token and updated your AI client's MCP URL to point at `/api/atm/mcp`, you can disable or remove the native HA MCP integration from **Settings > Devices and services**. Your AI client configuration needs only a URL change - the tool names and parameters are identical.

---

## Connecting Claude Code via MCP

ATM exposes an MCP endpoint at `/api/atm/mcp`. This is the recommended way to connect Claude Code to your Home Assistant instance.

### Step 1: Create a token

In the ATM sidebar panel, go to the **Tokens** tab and click **Create Token**. Give it a name like `claude-code` and click **Create**. Copy the token value when it appears. It is shown exactly once and cannot be retrieved later.

### Step 2: Set permissions

A new token has no permissions by default. Use the permissions panel to grant access to the domains, devices, and entities you want Claude to work with. See [The Permissions Panel](#the-permissions-panel) for a walkthrough of how the tree and permission buttons work.

You can also enable capability flags to unlock specific operations such as restarting HA or reading system logs. See [Capability Flags](#capability-flags) for the full list.

### Step 3: Add the MCP server to Claude Code

Run this command in your terminal, replacing the URL with your HA address and the token you copied:

```bash
claude mcp add --transport http home-assistant \
  http://your-ha-address:8123/api/atm/mcp \
  --header "Authorization: Bearer atm_your_token_here"
```

If you use Nabu Casa remote access or a custom domain:

```bash
claude mcp add --transport http home-assistant \
  https://your-instance.ui.nabu.casa/api/atm/mcp \
  --header "Authorization: Bearer atm_your_token_here"
```

### Step 4: Verify

Start a new Claude Code session and run `/mcp`. The `home-assistant` server should appear as connected. Ask Claude to list your entities or check a light state to confirm it is working.

---

## Available MCP Tools

ATM implements all 20 native HA MCP tools using the same tool names and response formats, and exposes the same MCP Prompts and Resources as the native HA MCP server. It also adds ATM-specific tools for direct entity access and system operations. All tools, prompts, and resources are scoped to the token's Permissions Tree. Pass-through tokens receive the same prompt and resource content as the native HA MCP server.

**Native HA MCP tools** - functionally identical to the native HA MCP server:

| Tool | Description |
|---|---|
| `GetLiveContext` | YAML snapshot of accessible entity states |
| `GetDateTime` | Current date, time, and timezone |
| `HassTurnOn` / `HassTurnOff` | Turn devices on or off by area, name, floor, or domain |
| `HassLightSet` | Set light brightness, color, or color temperature |
| `HassFanSetSpeed` | Set fan speed |
| `HassClimateSetTemperature` | Set climate device target temperature |
| `HassSetPosition` | Set position of covers or similar devices |
| `HassSetVolume` / `HassSetVolumeRelative` | Set or adjust media player volume |
| `HassMediaPause` / `HassMediaUnpause` | Pause or resume media playback |
| `HassMediaNext` / `HassMediaPrevious` | Skip tracks on a media player |
| `HassMediaSearchAndPlay` | Search and play media |
| `HassMediaPlayerMute` / `HassMediaPlayerUnmute` | Mute or unmute a media player |
| `HassCancelAllTimers` | Cancel all timers in an area |
| `HassStopMoving` | Stop a moving cover or device |

**ATM entity tools** - direct entity access also filtered by the Permissions Tree:

| Tool | Description |
|---|---|
| `get_state` | Current state of one entity |
| `get_states` | All accessible entity states |
| `get_history` | State history (`24h`, `7d`, `2w`, `1m`; `mode` defaults to `transitions`, `raw` opt-in) |
| `get_statistics` | Long-term statistics for numeric entities |
| `call_service` | Call any HA service by domain and name |

**Discovery, registry & diagnostics tools** - read-only, scoped to the token's accessible entities:

| Tool | Requires capability |
|---|---|
| `search_entities` / `get_overview` / `describe_area` / `find_available_actions` / `get_relationships` / `describe_entity` | `cap_search` |
| `whatif` / `compare_state` / `recent_activity` / `dry_run_service` | `cap_search` |
| `list_areas` / `list_floors` / `list_zones` / `list_devices` / `get_device` / `list_scenes` / `list_helpers` | `cap_registry_read` |
| `get_automation_traces` | `cap_traces` |
| `get_system_health` / `check_config` / `validate_config` | `cap_diagnostics` |
| `watch_entity` / `subscribe_event` | `cap_config_read` (bounded blocking, max 30s) |
| `get_capability_summary` / `get_audit_summary` | none (own data only) |

**System tools** - gated by capabilities:

| Tool | Requires capability |
|---|---|
| `render_template` | `cap_template_render` |
| `get_config` | `cap_config_read` |
| `restart_ha` | `cap_restart` |
| `get_logs` | `cap_log_read` |
| `HassBroadcast` - announce a message via assist satellite devices | `cap_broadcast` |
| `create_automation` / `edit_automation` / `delete_automation` | `cap_automation_write` |
| `create_script` / `edit_script` / `delete_script` | `cap_script_write` |
| `create_scene` / `edit_scene` / `delete_scene` | `cap_scene_write` |
| `create_helper` / `edit_helper` / `delete_helper` | `cap_helper_write` |

**MCP Prompts** - same prompt protocol as the native HA MCP server:

| Prompt | Description |
|---|---|
| `Default prompt for Home Assistant {name}` | HA instance system prompt. Pass-through tokens receive the same prompt as the native HA MCP server. Scoped tokens receive a permission-filtered version. |

**MCP Resources** - readable via `resources/read`:

| URI | Description |
|---|---|
| `homeassistant://assist/context-snapshot` | Current entity state snapshot. Same URI as the native HA MCP server; content is scoped to the token's permissions. |
| `atm://server-info` | ATM server metadata, token info, and version. JSON format. |

Claude can only see and act on entities within the token's permission scope.

> [!NOTE]
> After enabling or disabling a capability flag, your MCP client must reconnect to receive the updated tool list. In Claude Code, use the `/mcp` menu and select **Reconnect**.

---

## Tools Reference

### Query and Entity Access

#### `get_state`
Returns the current state of a single entity. Requires READ or WRITE permission on the entity.

**Parameters:**
- `entity_id` (string, required) - Entity ID (e.g., `light.living_room`)

**Returns:** Object with `state`, `attributes`, and `last_changed` timestamp. Sensitive attributes are always scrubbed.

---

#### `get_states`
Returns all entity states accessible to the token. Filtered by the Permissions Tree - only entities with READ or WRITE permission are included.

**Parameters:** None

**Returns:** Array of state objects. Pass-through tokens receive all non-ATM entities.

---

#### `get_history`
Returns historical state changes for an entity. Supports relative time strings: `24h` (24 hours ago), `7d` (7 days ago), `2w` (2 weeks ago), `1m` (30 days ago). Max range is 7 days.

**Parameters:**
- `entity_id` (string, required) - Entity ID
- `start_time` (string, optional) - ISO timestamp or relative string. Defaults to 24 hours ago.
- `end_time` (string, optional) - ISO timestamp or relative string. Defaults to now.

**Returns:** Array of history entries with `state` and `timestamp`. If the range exceeds 7 days, it is silently clamped. Actual queried range is returned in `X-ATM-History-Start` and `X-ATM-History-End` response headers.

---

#### `get_statistics`
Returns long-term statistics for numeric entities. Supports hourly, daily, weekly, monthly, or 5-minute aggregation.

**Parameters:**
- `entity_id` (string, required) - Entity ID
- `start_time` (string, optional) - ISO timestamp or relative string
- `end_time` (string, optional) - ISO timestamp or relative string
- `period` (string, optional) - One of `5minute`, `hour`, `day`, `week`, `month`. Defaults to `hour`.

**Returns:** Statistics array with `min`, `max`, `mean`, `sum`, and `state` values for each period.

---

#### `get_live_context`
YAML snapshot of all accessible entity states in a format optimized for LLM context. Equivalent to the native HA MCP `GetLiveContext` tool but filtered by the token's permissions.

**Parameters:** None

**Returns:** YAML-formatted string with entity states. Pass-through tokens receive the same output as the native HA MCP server.

---

#### `get_date_time`
Returns the current date, time, and timezone. Does not require any special permissions.

**Parameters:** None

**Returns:** Object with `date` (YYYY-MM-DD), `time` (HH:MM:SS), and `timezone`.

---

### Service Execution

#### `call_service`
Call any HA service. Requires appropriate permission for the target entities.

**Parameters:**
- `service` (string, required) - Service name in `domain/service` format (e.g., `light/turn_on`)
- `entity_id` (array, optional) - Explicit entity IDs
- `device_id` (array, optional) - Device IDs (expanded to entity list internally)
- `area_id` (array, optional) - Area IDs (expanded to entity list internally)
- `data` (object, optional) - Service parameters

**Behavior:**
- `device_id` and `area_id` are expanded to explicit entity lists before calling HA. Denied entities are silently excluded.
- If all entities in the call resolve to denied, returns 403.
- Service responses are scanned for entity IDs. Any inaccessible ID is replaced with `<redacted>`.
- Physical control services (lock, alarm, cover mutation) require `cap_physical_control` flag even with WRITE permission.
- Restart and stop services require `cap_restart` flag.

**Returns:** Service response data (if the service declares a response schema). Some services return nothing.

---

### Configuration and Diagnostics

#### `get_config`
Read HA configuration data. Requires `cap_config_read` flag.

**Parameters:** None

**Returns:** HA configuration object including integrations, packages, automation, and scripting settings.

---

#### `get_logs`
Read recent HA system log entries. Requires `cap_log_read` flag. ATM's own log entries are always excluded. Token values are scrubbed from messages and tracebacks.

**Parameters:**
- `limit` (integer, optional) - Number of entries to return. Defaults to 50. Max 100.
- `level` (string, optional) - Minimum log level. One of `INFO`, `WARNING`, `ERROR`. Defaults to `WARNING`.

**Returns:** Array of log entries with `timestamp`, `level`, `logger`, and `message`.

---

#### `render_template`
Render a Jinja2 template with access to HA state. Requires `cap_template_render` flag. The template environment is permission-scoped - templates can only access entities the token has READ or WRITE permission for.

**Parameters:**
- `template` (string, required) - Jinja2 template string

**Returns:** Rendered template result as a string.

---

### Discovery, Registry & Diagnostics

All of these are read-only and scoped to the entities the token can access. Per-object lookups (`get_device`, `describe_area`, `describe_entity`, `get_relationships`, `get_automation_traces`) return an identical "not found" for a missing object and one the token cannot access, so they cannot be used to probe for existence.

#### `search_entities`
Filter accessible entities. Requires `cap_search`. This is state/registry/name search; for semantic/profile search (tags, classification, control mode) use `mesa_query_profiles`.

**Parameters (all optional):** `query` (substring of entity_id or friendly name), `domain` (string or list), `area` (name or area_id), `device_class`, `state`, `unavailable` (boolean), `stale_hours` (number), `limit` (default 100, max 500).

**Returns:** `{count, truncated, entities: [{entity_id, state, friendly_name, domain, area, device_class}]}`.

#### `get_overview`
Compact home summary. Requires `cap_search`. Returns total accessible entities, counts by domain and by area, and the unavailable count.

#### `describe_area`
One area, its floor, and accessible entities grouped by domain. Requires `cap_search`. **Parameters:** `area` (name, alias, or area_id).

#### `describe_entity`
Comprehension summary of one entity: state, area, the services in its domain, what references it, and its MESA `control_mode` when MESA is active. Requires `cap_search`. For the full semantic profile use `mesa_get_profile` (`cap_config_read`). **Parameters:** `entity_id`.

#### `find_available_actions`
The services in an entity's domain and whether the token can invoke each now (write access, physical/dual gates), plus the entity's MESA `control_mode`. Requires `cap_search`. **Parameters:** `entity_id`.

#### `get_relationships`
Reverse references (automations/scripts/scenes that reference the entity) and forward references (entities an automation or script references, scoped to what the token can access). Requires `cap_search`. **Parameters:** `entity_id`.

#### `list_areas` / `list_floors` / `list_zones` / `list_devices` / `get_device`
Registry enumeration restricted to areas/floors/zones/devices that contain at least one accessible entity. Requires `cap_registry_read`. `get_device` takes a `device_id` and returns the device plus its accessible entities.

#### `get_automation_traces`
Execution traces for an accessible automation. Requires `cap_traces`. **Parameters:** `automation_id` (entity_id or id), optional `run_id`, optional `summary` (boolean, highlights the error and last step).

#### `get_system_health`
HA version and per-integration system health. Requires `cap_diagnostics`.

#### `check_config`
Validate the HA configuration files. Requires `cap_diagnostics`. Returns `{valid, errors, warnings}`.

#### `whatif`
Predict which automations would fire if an accessible entity changed to a hypothetical state, without changing anything. Requires `cap_search`. State and numeric_state triggers are evaluated best-effort; other trigger types report `unknown`. **Parameters:** `entity_id`, `hypothetical_state`.

#### `compare_state`
Compare accessible entities' states between two times (recorder-backed). Requires `cap_search`. **Parameters:** `entity_id` (string or list), `t1`, optional `t2` (defaults to now).

#### `recent_activity`
Accessible entities that changed in the last N minutes, newest first. Requires `cap_search`. **Parameters:** `minutes` (default 30), `limit` (default 50).

#### `dry_run_service`
Preview a service call without executing: resolves and flattens targets to the entities the token can write and reports the MESA verdict per entity. Requires `cap_search`. **Parameters:** same shape as `call_service` (`domain`, `service`, `service_data`, `entity_id`/`device_id`/`area_id`).

#### `validate_config`
Validate an automation or script config without saving. Requires `cap_diagnostics`. Returns structural validity plus, for each referenced entity, whether it exists and is accessible. **Parameters:** `type` (`automation` or `script`), `config`.

#### `get_capability_summary`
Introspect this token: persona, effective capabilities, Confirm-gated capabilities, write scope, and rate limits. No capability required.

#### `get_audit_summary`
This token's own recent audit entries (request_id, time, method, resource, outcome), newest first. No capability required. **Parameters:** `limit` (default 50), optional `outcome` filter.

---

### Automation and Script Management

#### `create_automation`
Create a new automation in `automations.yaml`. Requires `cap_automation_write` flag. This tool does NOT consult the Permissions Tree - it writes to YAML directly.

**Parameters:**
- `alias` (string, required) - Automation friendly name
- `trigger` (array, required) - Trigger array in HA automation format
- `action` (array, required) - Action array in HA automation format
- `condition` (array, optional) - Condition array
- `mode` (string, optional) - One of `single`, `restart`, `queued`, `parallel`. Defaults to `single`.

**Returns:** Created automation config with assigned ID.

**Security note:** See [Automation and script write flags](#automation-and-script-write-flags).

---

#### `edit_automation`
Edit an existing automation. Requires `cap_automation_write` flag and valid automation ID.

**Parameters:**
- `automation_id` (string, required) - Automation ID (the slug)
- All parameters from `create_automation` (replaces the entire config)

**Returns:** Updated automation config.

---

#### `delete_automation`
Delete an automation. Requires `cap_automation_write` flag.

**Parameters:**
- `automation_id` (string, required) - Automation ID to delete

**Returns:** Confirmation message.

---

#### `create_script`
Create a new script in `scripts.yaml`. Requires `cap_script_write` flag. This tool does NOT consult the Permissions Tree.

**Parameters:**
- `script_id` (string, required) - Script slug (lowercase alphanumeric and underscore only)
- `alias` (string, required) - Script friendly name
- `sequence` (array, required) - Sequence of actions in HA script format
- `mode` (string, optional) - One of `single`, `restart`, `queued`, `parallel`. Defaults to `single`.
- `variables` (object, optional) - Script-level variables
- `fields` (object, optional) - Input field definitions for callable scripts

**Returns:** Created script config.

**Security note:** See [Automation and script write flags](#automation-and-script-write-flags).

---

#### `edit_script`
Edit an existing script. Requires `cap_script_write` flag and valid script ID.

**Parameters:**
- `script_id` (string, required) - Script ID (the slug)
- All parameters from `create_script` (replaces the entire config)

**Returns:** Updated script config.

---

#### `delete_script`
Delete a script. Requires `cap_script_write` flag.

**Parameters:**
- `script_id` (string, required) - Script ID to delete

**Returns:** Confirmation message.

---

### System and Control

#### `restart_ha`
Restart Home Assistant. Requires `cap_restart` flag. This is a pass-through-exempt capability - even pass-through tokens must have this flag enabled.

**Parameters:** None

**Returns:** Confirmation that restart has been queued.

---

### Native HA MCP Tools

The following tools are functionally identical to the native HA MCP server. They use the same tool names, parameters, and response formats. All are scoped to the token's Permissions Tree.

#### `HassTurnOn` / `HassTurnOff`
Turn entities on or off by area, name, floor, or domain.

**Parameters:**
- `area` (string, optional) - Area name
- `floor` (string, optional) - Floor name
- `name` (string, optional) - Entity friendly name
- `domain` (array, optional) - Domain(s)
- `device_class` (array, optional) - Device class(es)

**Behavior:** Returns action_done with a list of successfully controlled entities and a list of failed entities. Only entities with WRITE permission are included. If no accessible entities match, returns "No accessible entities matched."

---

#### `HassLightSet`
Set brightness, color, or color temperature of accessible lights.

**Parameters:**
- `area` (string, optional) - Area name
- `floor` (string, optional) - Floor name
- `name` (string, optional) - Light friendly name
- `brightness` (integer 0-100, optional) - Brightness percentage
- `color` (string, optional) - CSS color name or hex
- `temperature` (integer, optional) - Color temperature in kelvin

---

#### `HassFanSetSpeed`
Set fan speed by percentage.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)
- `percentage` (integer 0-100, required) - Fan speed percentage

---

#### `HassClimateSetTemperature`
Set climate device target temperature.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)
- `temperature` (number, required) - Target temperature

---

#### `HassSetPosition`
Set position of covers, blinds, or similar devices (0-100).

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)
- `position` (integer 0-100, required) - Position percentage

---

#### `HassSetVolume` / `HassSetVolumeRelative`
Set or adjust media player volume.

**HassSetVolume Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)
- `volume_level` (integer 0-100, required) - Absolute volume

**HassSetVolumeRelative Parameters:**
- `volume_step` (string or integer, required) - One of `"up"`, `"down"`, or an integer percentage change (-100 to 100)

---

#### `HassMediaPause` / `HassMediaUnpause`
Pause or resume media playback.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)

---

#### `HassMediaNext` / `HassMediaPrevious`
Skip to next or previous track.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)

---

#### `HassMediaSearchAndPlay`
Search and play media on a player.

**Parameters:**
- `search_query` (string, required) - What to search for
- `media_class` (string, optional) - Media type (album, artist, track, playlist, etc.)
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)

---

#### `HassMediaPlayerMute` / `HassMediaPlayerUnmute`
Mute or unmute a media player.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)

---

#### `HassCancelAllTimers`
Cancel all running timers in an area.

**Parameters:**
- `area` (string, optional) - Area name. If omitted, cancels timers in all areas.

**Returns:** action_done with `speech_slots: { "canceled": N }` where N is the count of canceled timers.

---

#### `HassStopMoving`
Stop a moving cover or similar device.

**Parameters:**
- `area` (string, optional)
- `floor` (string, optional)
- `name` (string, optional)

---

#### `HassBroadcast`
Send an announcement through Assist satellite devices. Requires `cap_broadcast` flag.

**Parameters:**
- `message` (string, required) - Message to announce

**Returns:** action_done on success.

---

## Using Third-Party MCP Servers

Third-party MCP servers such as ha-mcp run as standalone processes and make calls directly to HA's native REST API (`/api/`). HA's authentication middleware only accepts Long-Lived Access Tokens - it has no knowledge of ATM tokens. As a result, ATM tokens cannot be used as a drop-in replacement for an LLAT with these servers.

If you need scoped, audited, revocable access for an AI client, point it at ATM's own MCP endpoint (`/api/atm/mcp`) instead. ATM's 20 native HA MCP tools cover the same everyday operations and apply your Permissions Tree on every call. For clients that specifically require a third-party server's extended tool set with no access restrictions, use a LLAT directly.

---

## The Permissions Panel

When you open a token in the ATM panel, the token detail page shows the **Permissions Tree** on the right side of the screen. Domains sit at the top level, devices nest underneath, and individual entities live at the leaves. You expand a domain to see its devices, expand a device to see its entities.

### The Permissions Tree

Each row in the tree has four colored buttons. Click one to set the permission state for that node:

- ⬜ **GREY** - no opinion. The node inherits its permission from its parent. This is the default for every node.
- 🟡 **YELLOW** - read-only. The token can read this entity's state but cannot call services that change it.
- 🟢 **GREEN** - read and write. The token can read state and call services.
- 🔴 **RED** - hard deny. Blocks this node and every entity underneath it, no matter what any other node says.

You do not have to set every node individually. The typical pattern is to set a whole domain or device to 🟡 YELLOW or 🟢 GREEN and leave everything below it at ⬜ GREY - child nodes will inherit the parent's color automatically. Then use 🔴 RED on specific devices or entities to carve out exceptions.

After granting permission to an entity, you can add an optional hint. Hints are surfaced to LLMs via the context endpoint to help them understand what an entity represents - e.g., "This lamp is on Rachel's desk, not the ceiling light".

### Select by Area

The **Select by Area** button at the top of the Permissions Tree card lets you bulk-apply a permission state to all entities in a HA area at once. Pick the area, pick the state (READ, WRITE, DENY, or remove grant), and ATM sets every entity in that area in one step. Useful for quickly scoping a token to a room without clicking through the tree manually.

### The Effective Permission Emulator

The **Effective Permission Emulator** shows you what ATM will actually decide for any entity. Type an entity ID (or click one in the tree or Permission Summary) and ATM runs the full two-pass resolver and shows you the result: WRITE, READ, or no access, which node in the ancestor chain determined the outcome, and the hint text if one is set.

This is the fastest way to verify your tree is doing what you intended. If the result surprises you, the path output tells you exactly which ancestor is overriding it.

### The Permission Summary

Below the Effective Permission Emulator is the **Permission Summary** - a compact table listing every node in the Permissions Tree that has been explicitly set to something other than ⬜ GREY. It shows the node type (Domain, Device, or Entity), the friendly name, the ID, and the current state.

You can sort the table by any column. Clicking an entity row in the Permission Summary populates the Effective Permission Emulator with that entity, just like clicking it in the tree.

If the table is empty, no explicit grants have been set and the token has no access to anything.

### Quick examples

**Read all lights, but only control the living room:**
Set the `light` domain to 🟡 YELLOW. Set `light.living_room` to 🟢 GREEN. Every light is readable; only the living room light is writable.

**Full access except the guest bedroom:**
Set a domain to 🟢 GREEN. Set a guest bedroom device to 🔴 RED. Every entity on that device becomes completely inaccessible, regardless of the domain setting.

**Block one noisy sensor inside an otherwise permitted device:**
Set the device to 🟢 GREEN. Set just that sensor entity to 🔴 RED.

For a detailed explanation of how ATM resolves permissions under the hood, see [The Permission System](#the-permission-system).

---

## The Permission System

Every token has a Permissions Tree organized into three levels: domains, devices, and entities. Each node carries one of four states.

### The four states

⬜ **GREY** - no opinion, inherit from parent. A ⬜ GREY entity under a 🟢 GREEN domain gets the domain's permission. ⬜ GREY at every level means no access.

🟡 **YELLOW** - read-only. The token can read the current state. It cannot call services that change state.

🟢 **GREEN** - read and write. The token can read state and call services.

🔴 **RED** - explicit deny. Blocks this node and everything beneath it. 🔴 RED cannot be overridden by any child node. It is a hard stop.

### How a permission is resolved

When a request arrives for an entity, ATM runs two checks:

**Pass 1 - 🔴 RED scan.** ATM walks the ancestor chain: entity, then device, then domain. If any node is 🔴 RED, the request is denied immediately. No other node matters.

**Pass 2 - most specific grant.** ATM walks the same chain looking for the most specific non-⬜ GREY node. That color becomes the effective permission. If no non-⬜ GREY node exists, the result is NO_ACCESS, which is indistinguishable from DENY to the caller.

### Examples

**Token with no grants.** All ⬜ GREY. The token can see and do nothing. This is the safe default. Add grants incrementally.

**Read all lights, control the living room.**
- Set the `light` domain to 🟡 YELLOW.
- Set `light.living_room` to 🟢 GREEN.

The token can read any light state. It can only call turn_on/turn_off on `light.living_room`.

**Full access to lights except the guest bedroom.**
- Set the `light` domain to 🟢 GREEN.
- Set the guest bedroom device to 🔴 RED.

All lights are writable. Every entity on the guest bedroom device is denied, even though the domain is 🟢 GREEN. 🔴 RED wins.

**Block one diagnostic sensor inside a permitted device.**
- Set the device to 🟢 GREEN.
- Set the specific diagnostic entity to 🔴 RED.

All entities on the device are writable except that one sensor, which is completely inaccessible.

### Indirect control risk

Three domains carry a specific risk that is easy to overlook: `automation`, `script`, and `scene`.

Granting WRITE (🟢 GREEN) to entities in these domains allows a client to:

- Trigger an automation (`automation.trigger`) - the automation runs under Home Assistant's full context, not ATM's
- Run a script (`script.turn_on`) - same
- Activate a scene (`scene.turn_on`) - applies a preset that can set state on any entity in the scene

ATM checks the token's permission for the automation, script, or scene entity itself. But the downstream effects - the lights that get turned on, the locks that get toggled, the climate changes - happen entirely outside ATM's scope. A token with NO_ACCESS to a door, but WRITE on `automation.*` can still unlock a door if a triggered automation does it.

Granting READ (🟡 YELLOW) to these domains is safe. It allows reading whether an automation is enabled or a scene exists, without the ability to trigger anything.

The Permissions Tree marks `automation`, `script`, and `scene` with a [!] badge as a reminder of this risk.

### What ATM always blocks

These apply to every token including pass-through tokens and are not configurable:

- The `atm` domain (all internal ATM sensors) is permanently blocked and invisible.
- Entity attributes that could expose security credentials (`access_token`, `entity_picture`, `stream_url`, `still_image_url`) are stripped from every state response.

---

## Capability Flags

Some operations require explicit opt-in even for tokens with 🟢 GREEN domain access:

| Flag | What it enables | Pass-through exempt |
|---|---|---|
| `cap_restart` | `homeassistant.restart` and `homeassistant.stop` | yes |
| `cap_physical_control` | Lock, alarm, and cover mutation services (e.g. `lock.unlock`, `alarm_control_panel.alarm_disarm`, `cover.open_cover`) | yes |
| `cap_automation_write` | Creating, editing, and deleting automations via the MCP tools. See security note below. | yes |
| `cap_script_write` | Creating, editing, and deleting scripts via the MCP tools. See security note below. | yes |
| `cap_config_read` | Reading HA configuration data and the event bus listener list | no |
| `cap_template_render` | Rendering Jinja2 templates (permission-scoped environment) | no |
| `cap_service_response` | Return response data from services that support it (e.g. `conversation.process`). Silently omitted for services that do not declare a response schema. | no |
| `cap_broadcast` | Sending announcements via the `HassBroadcast` MCP tool through assist satellite devices | no |
| `cap_log_read` | Reading HA system log entries via the `get_logs` MCP tool and `GET /api/atm/logs`. Logs may contain IP addresses and operational details. ATM's own entries are always excluded and token values are scrubbed from messages and tracebacks. | yes |
| `cap_search` | Discovery and comprehension reads: `search_entities`, `get_overview`, `describe_area`, `describe_entity`, `find_available_actions`, `get_relationships` | no |
| `cap_registry_read` | Registry enumeration: `list_areas`, `list_floors`, `list_zones`, `list_devices`, `get_device` | no |
| `cap_traces` | Reading automation execution traces via `get_automation_traces` | no |
| `cap_diagnostics` | `get_system_health`, `check_config` (and `validate_config` when added) | no |
| `cap_scene_write` | Creating, editing, and deleting scenes (Confirm-eligible) | yes |
| `cap_helper_write` | Creating, editing, and deleting helpers (Confirm-eligible) | yes |
| `cap_integration_write` | Enabling and disabling integrations (Confirm-eligible) | yes |
| `cap_lovelace_write` | Creating, editing, and deleting dashboards (Confirm-eligible) | yes |
| `cap_backup` | Creating and restoring backups (Confirm-eligible) | yes |
| `cap_filesystem` | Reading and writing files under `www/`, `themes/`, `custom_templates/` (Confirm-eligible) | yes |
| `cap_yaml_edit` | Editing `configuration.yaml` directly (Confirm-eligible) | yes |

The pass-through-exempt capabilities (the write, system, and irreversible caps plus `cap_log_read`) must be explicitly enabled even for pass-through tokens. The read-tier discovery caps and the other reads are bypassed (granted) by pass-through tokens. A cap set to `confirm` is gated even under pass-through. Capabilities marked Confirm-eligible can be set to `deny` / `allow` / `confirm`; the others are `deny` / `allow` only. Several of these caps gate tools that are still being rolled out; see the changelog in SPEC.md for current tool availability.

### Automation and script write flags

`cap_automation_write` and `cap_script_write` are elevated-trust capabilities. Enable them only for tokens held by clients you fully trust and control.

**These flags are all-or-nothing.** The automation and script write tools (`create_automation`, `edit_automation`, `delete_automation`, `create_script`, `edit_script`, `delete_script`) write directly to `automations.yaml` and `scripts.yaml`. They do not consult the token's Permissions Tree. A client with `cap_automation_write` enabled can write an automation referencing any entity in Home Assistant, regardless of what the token is permitted to access directly via `get_state` or `call_service`.

**The Permissions Tree cannot restrict automation/script write.** Setting the `automation` or `script` domain to READ or DENY in the Permissions Tree has no effect on these MCP tools. A DENY on `automation.*` only blocks entity-scoped operations (reading automation entity state, calling `automation.trigger`). It does not prevent the write tools from creating or modifying automation YAML.

**Triggered actions run outside ATM.** An automation or script created through ATM is triggered by HA's own automation engine, which runs under HA's own context, not ATM's. Permission checks do not apply to the actions taken when a triggered automation runs.

In practice, a token with a narrow entity scope but `cap_automation_write` enabled could - through a crafted automation - indirectly control entities it cannot access directly. Only enable these flags for clients you would trust with broad HA access.

---

## Pass-Through Mode

Pass-through tokens bypass the three-level permission check and have 🟢 GREEN access to all entities. They are intended for trusted tools where managing a full Permissions Tree is impractical or unnecessary.

Pass-through does NOT bypass:

- The `atm` domain blocklist
- Sensitive attribute scrubbing
- Rate limiting
- `cap_restart` - calling `homeassistant.restart` or `homeassistant.stop` still requires this flag.
- `cap_physical_control` - lock, alarm, and cover mutation services still require this flag.
- `cap_automation_write` - creating, editing, or deleting automations still requires this flag.
- `cap_script_write` - creating, editing, or deleting scripts still requires this flag.
- `cap_log_read` - reading HA system log entries still requires this flag.
- The newer write and risky caps (`cap_scene_write`, `cap_helper_write`, `cap_integration_write`, `cap_lovelace_write`, `cap_backup`, `cap_filesystem`, `cap_yaml_edit`) are likewise always evaluated.

These exempt caps must be explicitly enabled regardless of pass-through mode. All other capabilities (e.g. `cap_config_read`, `cap_template_render`, `cap_service_response`, `cap_broadcast`, `cap_search`, `cap_registry_read`, `cap_traces`, `cap_diagnostics`) are bypassed. A cap set to `confirm` is still gated even under pass-through.

The ATM panel shows a confirmation dialog before enabling pass-through on a token. When using the admin API directly, the PATCH request must include `"confirm_pass_through": true` alongside `"pass_through": true`. Omitting it returns a 400 error. Use pass-through only for tools you fully control. For anything externally hosted or shared, use the scoped Permissions Tree instead.

---

## Rate Limiting

Every token has a sliding window rate limit. The defaults are 60 requests per minute with a burst allowance of 10 per second. Both are configurable per token. Setting `rate_limit_requests` to 0 disables rate limiting entirely for that token.

When a request is rate limited, ATM returns HTTP 429 with a `Retry-After` header. Successful responses include rate limit headers:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 47
X-RateLimit-Reset: 1712345678
```

If `notify_on_rate_limit` is enabled in global settings, HA creates a persistent notification when a token hits its limit. This is throttled to once per token per minute.

---

## Security

### Token design

- Tokens are 68 characters with a fixed `atm_` prefix. Any value that does not match this exact format is rejected before any storage lookup.
- Only the SHA-256 hash of the token is stored. The raw value is never written to disk or logs.
- Token comparisons always use a constant-time algorithm (`hmac.compare_digest`). String equality (`==`) is never used for token validation.
- Tokens are only accepted in the `Authorization: Bearer` header. Query parameters are rejected with 401. No token value ever appears in HA logs.

### Permission enforcement

- Every request is validated against the full two-pass permission algorithm. No endpoint implements its own shortcut.
- Entity not found and entity inaccessible return identical response bodies. A caller cannot determine whether an entity exists or is simply blocked.
- Sensitive attributes are stripped from every state response, for every token type. See [Sensitive attributes stripping](#sensitive-attributes-stripping).
- Service calls that include `device_id` or `area_id` are always expanded to an explicit entity list before being passed to HA. Denied entities are silently excluded.
- If all entities in a service call resolve to denied, ATM returns 403 rather than calling HA with an empty list.
- Service response data is scanned for entity IDs. Any entity ID the token cannot access is replaced with `"<redacted>"`.
- If an entity ID in a service call does not exist in the HA entity registry, ATM returns 403. Entity creation via service calls is not permitted.
- Physical control services (`lock.unlock`, `alarm_control_panel.alarm_disarm`, `cover.open_cover`, and related services) require `cap_physical_control` in addition to entity-level WRITE permission. This applies even to pass-through tokens.
- Automation and script write MCP tools bypass the Permissions Tree. Setting the `automation` or `script` domain to RED or YELLOW does not prevent these tools from writing YAML. See [Automation and script write flags](#automation-and-script-write-flags).

### Sensitive attributes stripping

ATM removes four sensitive attributes from every state response, regardless of token type or permission level:

1. **`entity_picture`** - URLs to entity images and icons, often containing authentication tokens or private asset paths
2. **`stream_url`** - Direct stream URLs (e.g., from cameras), which may contain credentials or expose internal network topology
3. **`access_token`** - Authentication tokens embedded in entity state (e.g., from integrations that store temporary credentials)
4. **`still_image_url`** - Static image URLs that might contain sensitive identifiers or auth parameters

**Why all tokens, all the time:**

- Even pass-through tokens (which bypass entity permissions) still get these attributes scrubbed. A pass-through token that can call any service doesn't need access to embedded credentials; it already has the power to act.
- Even high-permission scoped tokens with WRITE access get these attributes scrubbed. Permission grants control what _actions_ a token can take, not what _secrets_ it can read.
- This prevents accidental credential leakage through state snapshots, audit logs, or third-party integrations that consume ATM responses.

**Where stripping happens:**

- Proxy view: `/api/atm/states`, `/api/atm/entities/{entity_id}`, `/api/atm/history`, etc.
- MCP tools: `GetLiveContext`, `get_history`, `get_state`, etc.
- Service response data filtering: if a service returns entity state in its response (e.g., a script fetching entity details), those attributes are redacted before returning to the caller.


### Token lifecycle

**Rotation** generates a new raw token value for an existing token while keeping all of its permissions, capability flags, rate limit settings, and audit history intact. The old value is invalidated the moment rotation is confirmed - there is no grace period. The new value is shown once and cannot be retrieved again. Use rotation when you suspect a token value has been exposed but do not want to rebuild the Permissions Tree from scratch.

**Revocation** permanently retires a token. When a token is revoked, ATM immediately archives it to storage, terminates all open SSE connections for that token, destroys its rate limiter state, removes its sensor entities, and fires an `atm_token_revoked` event. All of this happens before the revoke response is returned.

Expired tokens are treated identically to revoked tokens at validation time.

### Admin API isolation

The admin API (`/api/atm/admin/`) requires a valid HA session and HA admin privileges. An ATM token cannot authenticate an admin request, even a pass-through token.

### Kill switch

When the kill switch is enabled at startup, ATM registers no proxy or MCP routes. The endpoints do not exist - there is nothing to respond with 503. The admin panel remains accessible. Disabling the kill switch re-registers all routes immediately without an HA restart.

### Request limits

- Request bodies exceeding 1 MB are rejected with 413 before any processing.
- SSE connections are limited to 5 per token. A sixth connection is rejected with 429.
- History queries are capped at a 7-day time range. Requests spanning more than 7 days are silently clamped to the most recent 7 days. The actual queried range is returned in `X-ATM-History-Start` and `X-ATM-History-End` response headers. Passing a `start_time` after `end_time` returns 400.
- Every response includes an `X-ATM-Request-ID` header with a UUID that matches the corresponding audit log entry.

---

## Telemetry and Sensors

ATM creates six HA sensor entities for each active token. For a token named `claude-code`:

| Entity | Description |
|---|---|
| `sensor.atm_claude_code_status` | `active`, `expired`, or `revoked` |
| `sensor.atm_claude_code_request_count` | Total requests made with this token |
| `sensor.atm_claude_code_denied_count` | Requests blocked by permission rules |
| `sensor.atm_claude_code_rate_limit_hits` | Number of times this token has been rate limited |
| `sensor.atm_claude_code_last_access` | Timestamp of the most recent request |
| `sensor.atm_claude_code_expires_in` | Days until expiry, or `No expiry` if no expiry is set |

Sensors are removed automatically when a token is revoked. ATM sensors are blocked from all token access - external tools cannot read their own telemetry through ATM.

---

## Global Settings

| Setting | Default | Description |
|---|---|---|
| Kill switch | Off | When on, proxy and MCP routes are unregistered entirely |
| Disable all logging | Off | Suppresses all auditing |
| Log allowed requests | On | Record successful requests |
| Log denied requests | On | Record blocked requests and unsupported MCP method calls |
| Log rate-limited requests | On | Record rate-limited requests |
| Log entity names | On | Include entity IDs in audit entries |
| Log client IP | On | Include caller IP in audit entries |
| Notify on rate limit | Off | Create a HA notification when a token is rate limited |
| Audit log flush interval | 15 min | How often to snapshot the in-memory log to disk. Set to "Never" to disable persistence entirely. |
| Maximum log entries | 10,000 | Capacity of the in-memory buffer and the on-disk snapshot. Reducing this trims the oldest entries immediately. |

---

## Audit Log

ATM keeps a circular buffer of requests, queryable from the ATM panel or via the admin API. The default capacity is 10,000 entries, configurable in Global Settings. You can view the Audit Log in the AUDIT tab of the ATM panel - click on a row to get full information for an event.

Each entry records a unique request ID (matching the `X-ATM-Request-ID` response header), timestamp, token ID and name, HTTP method, resource path, outcome, and client IP.

Outcome values:

- `allowed` - request succeeded.
- `denied` - blocked by ATM permission rules, blocklist, or a RED/NO_ACCESS result. Includes permission-based 404s.
- `not_found` - entity is genuinely absent from both HA state and the entity registry. From the caller's perspective it looks identical to `denied`, but the audit log distinguishes them so you can tell whether a token is hitting a missing entity or a permission wall.
- `rate_limited` - token exceeded its rate limit.
- `not_implemented` - the MCP client called a method ATM does not support (for example, `resources/templates/list`). This is a protocol-level gap, not a permission block, and does not increment the token's denied counter.
- `invalid_request` - request was structurally malformed and rejected before it reached permission checks, for example a template render call with a syntax error in the template body.

### Persistence

The audit log is stored in `.storage/atm_audit.json` and survives HA restarts. It is included in HA full backups and in partial backups of the `.storage` directory.

ATM flushes the in-memory buffer to disk on the configured interval (default: every 15 minutes), and also automatically on HA stop, integration reload, and integration unload. Set the interval to "Never" to keep the log in memory only and disable all disk writes.

---

## HA Events

| Event | Fired when |
|---|---|
| `atm_token_revoked` | A token is revoked |
| `atm_token_expired` | A token's expiry time passes and it is first accessed |
| `atm_token_rotated` | A token's raw value is rotated |
| `atm_rate_limited` | A token exceeds its rate limit (once per token per minute) |

Event data includes `token_id`, `token_name`, and `timestamp`. Revocation and rotation events also include `revoked_by` / `rotated_by` (the HA user ID of the admin who performed the action).

---

## Route Reference

### Admin API (requires HA session + admin role)

```
GET/POST   /api/atm/admin/tokens                              List or create tokens
GET/PATCH  /api/atm/admin/tokens/{id}                         Get or update a token
DELETE     /api/atm/admin/tokens/{id}                         Revoke a token
GET/PUT    /api/atm/admin/tokens/{id}/permissions             Read or replace permissions tree
PATCH      /api/atm/admin/tokens/{id}/permissions/domains/{node}
PATCH      /api/atm/admin/tokens/{id}/permissions/devices/{node}
PATCH      /api/atm/admin/tokens/{id}/permissions/entities/{node}
GET        /api/atm/admin/tokens/{id}/resolve/{entity_id}     Explain effective permission
POST       /api/atm/admin/tokens/{id}/rotate                  Generate a new raw token value (old value immediately invalid)
GET        /api/atm/admin/tokens/{id}/scope                   List all readable/writable entities
GET        /api/atm/admin/tokens/{id}/stats                   Request counters
GET        /api/atm/admin/tokens/{id}/audit                   Audit log for one token
GET        /api/atm/admin/tokens/archived                     List archived tokens
DELETE     /api/atm/admin/tokens/archived/{id}                Delete an archived record
GET        /api/atm/admin/entities                            Entity tree
GET        /api/atm/admin/info                                Integration version info
GET        /api/atm/admin/audit                               Global audit log
GET/PATCH  /api/atm/admin/settings                            Global settings
DELETE     /api/atm/admin/wipe                                Wipe all tokens and settings
```

### Proxy API (requires ATM token in Authorization: Bearer header)

```
GET        /api/atm/states                                    All accessible entity states
GET        /api/atm/states/{entity_id}                        One entity state
POST       /api/atm/services/{domain}/{service}               Call a service
GET        /api/atm/history/period/{timestamp}                State history (max 7-day range)
GET        /api/atm/statistics                                Long-term statistics
POST       /api/atm/template                                  Render a Jinja2 template
GET        /api/atm/config                                    HA configuration
GET        /api/atm/events                                    HA event bus listeners
GET        /api/atm/services                                  Accessible service list
GET        /api/atm/logs                                      Recent HA system log entries
GET        /api/atm/mcp                                       MCP SSE endpoint
POST       /api/atm/mcp                                       MCP Streamable HTTP endpoint
POST       /api/atm/mcp/messages?session_id={id}              MCP SSE message endpoint
GET        /api/atm/mcp/context                               Token context summary
```

---

## Issues and Feedback

Report issues at https://github.com/sfox38/atm/issues.
