"""ATM Agent Skill endpoint (skills workstream, Channel B host).

Serves a plain Markdown usage guide for AI agents connected through ATM. The
content is generic, non-sensitive guidance with no token or entity data, so the
endpoint is unauthenticated and any agent that followed the link in the MCP
`initialize` instructions (Channel A) can fetch it. It is registered with the
kill-switch-gated client routes, so it is unreachable when ATM is disabled.

Token-personalized guidance (which capabilities are gated for this token, etc.)
is delivered separately in the MCP `initialize` `instructions` field (Channel A),
which also links here.
"""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

ATM_SKILL_MARKDOWN = """---
name: atm-home-assistant
description: >-
  Use when controlling, inspecting, or configuring Home Assistant through an ATM
  (Advanced Token Management) MCP connection. Covers ATM's scoped permission
  model, the human-approval ("confirm") gate, the MESA per-entity safety layer,
  and the recommended discover, preview, act, verify workflow for reads, service
  calls, and authoring automations, scripts, scenes, helpers, and dashboards.
---

# Using Home Assistant through ATM

You are connected to Home Assistant through ATM (Advanced Token Management), a
scoped gateway. Your access token sees only the entities and tools an operator
granted it, and some actions are gated behind human approval or a per-entity
safety layer. ATM is the enforcement: this guide is advisory. Working with the
grain of the model gives the smoothest results and avoids dead ends.

## The three rules that explain almost every surprise

1. You see only what this token is scoped to. Entities and tools outside the
   scope are invisible, not merely hidden. Missing means inaccessible; never
   infer that an entity exists because it would in a default Home Assistant.
2. Some actions need a human. They return `status: "pending_approval"`. That is
   a normal outcome, not an error.
3. A separate safety layer (MESA) can make an entity read-only,
   confirm-before-act, or prohibited regardless of your capabilities.

## Recommended workflow: orient, discover, preview, act, verify

### 1. Orient
- `get_capability_summary` (no special capability needed): your persona, which
  capabilities are allowed, which are gated behind admin approval ("confirm"),
  whether you can write at all, and your rate limits. Call it once at the start.
- `mesa_get_caller_context`: who this token is from MESA's perspective.
- `get_audit_summary`: your own recent calls, useful to avoid repeating work.

### 2. Discover (read-only; results are always scoped to your token)
- `get_overview`: a compact home summary to get your bearings.
- `search_entities`: find entities by name, state, `device_class`, area, or
  filters like unavailable or "stale > N". This is keyword/attribute search.
- `mesa_query_profiles`: a different search, by MESA's semantic profile (an
  entity's nature/role), not by name. Reach for it when you care about what an
  entity is, not what it is called.
- `list_areas`, `list_floors`, `list_zones`, `list_devices`, `get_device`:
  registry enumeration. Only areas and devices with at least one accessible
  entity are returned.
- `describe_entity`: one entity's state, the services that act on it, its MESA
  profile and `control_mode`, and what references it.
- `describe_area`: a registry, state, and MESA rollup for one area.
- `find_available_actions`: the services you may actually invoke on an entity or
  area, already filtered by your capabilities and MESA's control mode.
- `get_relationships`: which automations, scripts, and scenes touch an entity.
- `get_history` (transitions by default), `get_statistics`, `recent_activity`,
  `compare_state`: what changed and when. Use relative time strings like `24h`,
  `7d`, `2w`, `1m`.

### 3. Preview before you commit
- `dry_run_service` (or `dry_run: true` on `call_service`): resolves and flattens
  the targets and returns the per-entity MESA verdict without changing anything.
  Use it before any bulk or risky call.
- `whatif`: predicts which automations would fire if an entity became a given
  state, so you can reason about side effects first.
- `validate_config`: structurally checks an automation or script config, and
  whether the entities it references exist and are accessible, before you save.

### 4. Act
- Prefer the native intent tools (`HassTurnOn`, `HassTurnOff`, `HassLightSet`,
  `HassSetPosition`, climate/media/fan tools) for everyday control; fall back to
  `call_service` for anything they do not cover.
- Target entities explicitly. ATM resolves areas and devices to explicit entity
  lists anyway, and an area or device target silently drops members you cannot
  access, so naming entities makes the result predictable.
- Authoring tools, when granted: automations and scripts (`cap_automation_write`
  / `cap_script_write`), scenes (`cap_scene_write`), helpers such as
  `input_boolean`, `input_number`, `timer`, `counter` (`cap_helper_write`),
  dashboards (`cap_lovelace_write`).

### 5. Verify
- Re-read state (`get_state`, `describe_entity`) or `get_automation_traces` after
  a change. Do not assume success; confirm it.

## Approval is normal, not an error

When a capability is set to "confirm", the action returns
`status: "pending_approval"` with an `approval_id` instead of running. A human
must approve it. Handle it like this:

- Do not retry. Retrying creates duplicate approval requests and burns your rate
  limit.
- Either tell the user the action is awaiting their approval, or poll
  `get_approval_status` with the `approval_id`. It resolves to `approved` (with
  the result), `rejected` (often with a reason), or `expired`.
- Approval is the operator's intent to stay in the loop. Respect it; do not look
  for a way around it.

## Respect the safety layer (MESA)

MESA classifies entities by their real-world nature, independent of your token's
capabilities. An entity's `control_mode` (shown by `describe_entity` and
`find_available_actions`) may be:

- read-only: you can observe it but not change it.
- confirm: changing it routes through the approval gate above, even if your
  capability is "allow". Door locks, alarms, and covers commonly behave this way.
- prohibited: it cannot be changed through ATM at all.

In advisory mode MESA lets a call through but attaches a warning (a
`mesa_advisory` array on the response, or the `speech` field on native action
results). Read those warnings; they explain risk you should relay to the user.

## Risky and irreversible actions

Backups (`create_backup`), integration enable/disable
(`set_integration_enabled`), dashboard edits, scoped filesystem writes
(`www/`, `themes/`, `custom_templates/` only), and raw `configuration.yaml`
edits are the most consequential tools and are almost always behind the confirm
gate. Before requesting one: state plainly what it will change, prefer a backup
first for config edits, and never use a filesystem or YAML tool to reach outside
the allowed directories. There is no restore-backup tool by design.

## Bounded subscriptions

`watch_entity` and `subscribe_event` open short, time-boxed streams (capped at a
few tens of seconds). They are for catching an imminent change, not long-lived
monitoring. Expect them to end on their own; re-subscribe if you still need to
watch.

## If a tool is not listed, you do not have it

The advertised tool list reflects this token's capabilities. If a tool is absent
(automation editing, backups, dashboards, filesystem, and so on), this token
cannot use it. Do not attempt unadvertised tools; ask the operator to grant the
capability instead. A `forbidden` result means the same thing.

## Home Assistant authoring best practices

When you author automations, scripts, or scenes:

- Prefer native Home Assistant constructs (helpers such as `input_boolean`,
  `input_number`, `timer`, `counter`; native triggers and conditions) over
  hand-written templates when a native option exists. Templates are powerful but
  harder to debug and easier to break across upgrades.
- Validate first (`validate_config`), then write, then verify with a trace.
- Reference only entities you can actually access; a config that points at
  out-of-scope entities will not behave as written.
- After editing automations or scripts they are reloaded automatically; you do
  not need to restart Home Assistant for those changes.

## Further reading

For a deeper Home Assistant authoring guide (blueprints, YAML-only integrations,
helper selection), see the community Agent Skill at
https://github.com/homeassistant-ai/skills. ATM's permission tree, approval
gate, and MESA remain the enforcement regardless of any external guidance.
"""


class ATMSkillView(HomeAssistantView):
    """GET /api/atm/skill - the ATM usage guide as Markdown. Unauthenticated."""

    url = "/api/atm/skill"
    name = "api:atm:skill"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=ATM_SKILL_MARKDOWN, content_type="text/markdown")


ALL_SKILL_VIEWS = [ATMSkillView]
