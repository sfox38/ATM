# Advanced Token Management (ATM)

[![HACS](https://github.com/sfox38/ATM/actions/workflows/hacs.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml)
[![Tests](https://github.com/sfox38/ATM/actions/workflows/tests.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

<p align="center">
  <a href="https://sfox38.github.io/atm-roadtest/">
    <img src="https://sfox38.github.io/atm-roadtest/infographic.png" alt="ATM road test scorecard: cheapest per completed task, most successful, and the only server that left every off-limits device untouched" width="820">
  </a>
</p>

<p align="center"><em>One model, one synthetic home, the same tasks. <a href="https://sfox38.github.io/atm-roadtest/">See the full road test.</a></em></p>

ATM gives your AI agents scoped, least-privilege access to Home Assistant. Each client gets its own token, limited to exactly the entities you allow, with its own capabilities, rate limit, and optional expiry. Every request is audited, any token can be revoked instantly, and the per-entity semantic safety layer (MESA) can make a device confirm-only or off-limits by its nature, no matter what permissions a token is granted.

ATM runs entirely inside Home Assistant, with no extra process, no cloud dependency, and no setup beyond the ATM panel. It works with the MCP clients you already use (Claude Code, Cursor, ChatGPT/Codex, Gemini, and others), and a connect wizard takes you from a new token to a working agent in minutes, backed by a catalog of 86 tools for reading, controlling, and authoring your configuration.

## Documentation

The full documentation, including the tool reference, permissions, capabilities, MESA, and the admin API, lives at **[sfox38.github.io/ATM](https://sfox38.github.io/ATM/)**.

New here? Start with the **[Quick start](https://sfox38.github.io/ATM/quickstart.html)**: it creates your first token, connects your agent, and tests the connection in a few minutes.

## How ATM compares

We road-tested ATM against the built-in and community MCP servers, one agent model, one synthetic home, the same tasks. ATM was the cheapest per completed task, the only server that left every off-limits device untouched, and the most capable. See the **[road test report](https://sfox38.github.io/atm-roadtest/)**, or reproduce it from the **[benchmark repository](https://github.com/sfox38/atm-roadtest)**.

## Requirements

- Home Assistant 2024.5.0 or later.
- One ATM instance per Home Assistant. No Python dependencies beyond what HA ships.

## Install via HACS

1. In HACS, open **Integrations**, then the top-right menu, and choose **Custom repositories**.
2. Enter `https://github.com/sfox38/ATM` and select **Integration** as the category, then click **Add**.
3. Find ATM in the HACS integration list, install it, and restart Home Assistant.

Prefer to install by hand? Copy the `custom_components/atm` folder into your Home Assistant config directory under `custom_components/atm`, then restart.

### Set up

Go to **Settings > Devices & services > Add integration** and search for **Advanced Token Management**. Click through the single-step config flow, then open the **ATM** panel in your sidebar. The [Quick start](https://sfox38.github.io/ATM/quickstart.html) takes it from there.

## Changelog

The complete release history is on the [GitHub releases page](https://github.com/sfox38/ATM/releases). Recent changes:

### 2.1.0

- Hardening: the MCP batch endpoint now runs its items sequentially instead of concurrently, so a single batch can never execute many write tools at once and interleave them. The registry-write tools (`set_entity`/`delete_entity`) now reject a disabled capability with a uniform "forbidden" before doing any entity lookup, and validate the target's scope before creating an approval. The REST single-state endpoint resolves a registry id / alias consistently (no false "not found"), and the entity list endpoint clamps `limit` to 1..500. In the panel, the permission editor now shows an error if a change fails to save instead of silently doing nothing, and switching theme no longer errors when browser storage is unavailable.
- Security fix: `validate_config` no longer reveals whether an out-of-scope entity exists. It previously reported `exists` from the registry independently of accessibility, which let a token with `cap_diagnostics` probe for hidden entities by referencing guessed IDs in a config. Inaccessible entities now report `exists: false` (collapsed to the accessible flag), so a hidden real entity is indistinguishable from a typo, consistent with the rest of ATM's no-existence-oracle rule.
- Refined MCP tool descriptions so agents pick the right tool on the first try. The raw `set_yaml_config` and `write_file` tools now steer toward the dedicated automation, script, scene, helper, and dashboard tools, and `get_states`, `get_statistics`, `get_config`, and `render_template` clarify how they differ from neighboring tools and how token scoping affects them.
- `get_state` and `get_states` now return a compact, domain-aware view by default (key attributes only) to cut token cost, with `detailed: true` for the full state and `fields: [...]` to select exact fields. `describe_entity` remains the full single-entity tool, and sensitive attributes are always scrubbed first.
- Raw `configuration.yaml` and file writes (`set_yaml_config`, `write_file`) are now captured in the configuration version history with admin rollback, the same before/after snapshots and Restore the structured authoring tools already had. Oversized snapshots (over 100 KB) are kept as a non-restorable marker to bound storage.
- Orphaned MESA profiles can be cleared in bulk: a **Clear all orphaned profiles** button on the MESA tab (backed by `POST /api/atm/admin/mesa/orphans/clear`) removes every profile whose entity, area, or integration no longer exists in one step, instead of deleting them one at a time. It recomputes orphans against the live registries first; profiles are still never deleted automatically.
- The agent skill guide (`/api/atm/skill`) now includes modular domain-authoring recipes (automations, scripts and scenes, dashboards and cards, conditional/visibility, and climate), so connected agents produce better Home Assistant configuration. Guidance only, no new tools.
- Two new read tools: `get_logbook` (the human-readable event history, gated on `cap_log_read`, scoped to your entities) and `get_calendar_events` (events from an accessible calendar entity within a time window). Both are read-only and scoped to what the token can access.
- The Changes tab now renders raw `configuration.yaml` and file-write history as a line-highlighted text diff (added/removed lines), instead of YAML-quoting the file contents; oversized snapshots show a clear "too large" notice and are marked non-restorable.
- `search_entities` now ranks results by relevance and supports multi-word queries (every word must match), so the best matches lead instead of being cut off by the result limit. Previously it was an unranked substring filter.
- New `list_blueprints` tool lists installed automation and script blueprints with their inputs (gated on `cap_config_read`). To build from a blueprint, create the automation or script with a `use_blueprint` config, which the existing authoring tools already accept, so there is no extra tool to learn.
- New entity-registry tools `set_entity` (friendly name / icon / area) and `delete_entity` (remove a stale or duplicate registry entry), behind a new `cap_registry_write` capability. It defaults to admin-confirmation for the power-user, home-admin, and maintenance personas and is off elsewhere. Both require write access to the entity and are captured in version history; renaming an `entity_id` is intentionally not exposed. The admin approval review shows the before/after of the change.

### 2.0.0

See the [v2.0.0 release notes](https://github.com/sfox38/ATM/releases/tag/v2.0.0).

## Issues and feedback

Report issues at [github.com/sfox38/ATM/issues](https://github.com/sfox38/ATM/issues).
