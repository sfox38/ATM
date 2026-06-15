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

ATM_SKILL_MARKDOWN = """# Using Home Assistant through ATM

You are connected to Home Assistant through ATM (Advanced Token Management), a
scoped gateway. Your access token sees only the entities and tools an operator
granted it, and some actions are gated behind human approval or a per-entity
safety layer. Working with the grain of that model gives the smoothest results.

## Orient yourself first

- Call `get_capability_summary` at the start of a session. It tells you this
  token's persona, which capabilities are allowed, which are gated behind admin
  approval ("confirm"), whether you can write at all, and your rate limits.
- Use `get_overview`, `search_entities`, `list_areas`, and `describe_area` to
  discover what exists. You only ever see entities within this token's scope;
  entities outside it are invisible, not merely hidden, so do not assume an
  entity exists just because it would in a default Home Assistant.
- `describe_entity` and `get_relationships` explain a single entity: its state,
  what services act on it, and which automations, scripts, and scenes touch it.

## Approval is normal, not an error

Some actions return `status: "pending_approval"` instead of running immediately.
This means a human must approve the action. It is a valid outcome, not a failure:

- Do not retry the call. Retrying creates duplicate approval requests and wastes
  your rate limit.
- Either tell the user the action is awaiting their approval, or call
  `get_approval_status` with the returned `approval_id` to check later. The
  status becomes `approved` (with the result), `rejected` (often with a reason),
  or `expired`.

## Respect the safety layer (MESA)

A per-entity safety layer may classify entities as read-only, confirm-before-act,
or prohibited, independent of your token's capabilities. `describe_entity` and
`find_available_actions` show an entity's `control_mode`. A door lock or alarm
will commonly require confirmation even when you otherwise have write access.

## Preview before you commit

- `dry_run_service` resolves and flattens a service call's targets and reports
  the safety verdict per entity, without changing anything. Use it before a bulk
  or risky `call_service`.
- `whatif` predicts which automations would fire if an entity changed to a given
  state, so you can reason about side effects before acting.
- `validate_config` checks an automation or script config (and whether the
  entities it references exist and are accessible) before you save it.

## If a tool is not listed, you do not have it

The tool list reflects this token's capabilities. If you do not see a tool (for
example automation editing, backups, or dashboard tools), this token cannot use
it. Do not attempt to call tools that are not advertised; ask the operator to
grant the capability instead.

## Home Assistant best practices

When you do author automations, scripts, or scenes:

- Prefer native Home Assistant constructs (helpers such as `input_boolean`,
  `input_number`, `timer`, `counter`; native triggers and conditions) over
  hand-written templates when a native option exists. Templates are powerful but
  harder to debug and easier to break on upgrades.
- Target entities explicitly. Prefer `entity_id` in service calls; ATM resolves
  areas and devices to explicit entity lists anyway.
- After editing automations or scripts, they are reloaded automatically; you do
  not need to restart Home Assistant for those changes.

For the full Home Assistant authoring best-practices guide, see the upstream
Agent Skill at https://github.com/homeassistant-ai/skills.
"""


class ATMSkillView(HomeAssistantView):
    """GET /api/atm/skill - the ATM usage guide as Markdown. Unauthenticated."""

    url = "/api/atm/skill"
    name = "api:atm:skill"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=ATM_SKILL_MARKDOWN, content_type="text/markdown")


ALL_SKILL_VIEWS = [ATMSkillView]
