# Advanced Token Management (ATM)

[![HACS](https://github.com/sfox38/ATM/actions/workflows/hacs.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml)
[![Tests](https://github.com/sfox38/ATM/actions/workflows/tests.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

<p align="center">
  <a href="https://sfox38.github.io/atm-roadtest/">
    <img src="https://sfox38.github.io/atm-roadtest/infographic_25-June-2026.png" alt="ATM road test scorecard: cheapest per completed task, most successful, and the only server that left every off-limits device untouched" width="820">
  </a>
</p>

<p align="center"><em>One model, one synthetic home, the same tasks. <a href="https://sfox38.github.io/atm-roadtest/">See the latest road test results in full.</a></em></p>

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

- New: Raw `configuration.yaml` and file writes (`set_yaml_config`, `write_file`) are now captured in the configuration version history with admin rollback, the same before/after snapshots and Restore the structured authoring tools already had.

- New: Orphaned MESA profiles can be cleared in bulk: a **Clear all orphaned profiles** button on the MESA tab removes every profile whose entity, area, or integration no longer exists in one step

- New: The agent skill guide now includes modular domain-authoring recipes (automations, scripts and scenes, dashboards and cards, conditional/visibility, and climate), so connected agents produce better Home Assistant configuration.

- New: `get_logbook` (the human-readable event history)

- New: `get_calendar_events` (events from an accessible calendar entity within a time window)

- New: `list_blueprints` tool lists installed automation and script blueprints with their inputs

- New: entity-registry tools `set_entity` (friendly name / icon / area) and `delete_entity` (remove a stale or duplicate registry entry; renaming an `entity_id` is intentionally not exposed.

- Security fix: `get_system_health` now scrubs secret-keyed values and URL-embedded credentials from the per-integration health data before returning it, and also network-topology details (LAN/loopback/link-local IP addresses, hostnames inside URLs, and filesystem paths), consistent with what `get_config` already withholds. The topology scrub is conservative (private IP ranges only, so a public-IP-shaped version string is untouched). 

- Security fix: `validate_config` no longer reveals whether an out-of-scope entity exists.

- Fix: The MCP batch endpoint now runs its items sequentially instead of concurrently, so a single batch can never execute many write tools at once and interleave them.

- Fix: `get_config` now returns a curated subset of Home Assistant's configuration (version, time zone, units, location name, loaded components) instead of the full config object.

- Updated: The Changes tab now renders raw `configuration.yaml` and file-write history as a line-highlighted text diff, instead of YAML-quoting the file contents.

- Updated: Better MCP tool descriptions so agents can pick the right tool on the first try.

- Updated: `get_state` and `get_states` now return a compact, domain-aware view by default (key attributes only) to cut token cost.

- Updated: `search_entities` now ranks results by relevance and supports multi-word queries.

- Tests: Hardening

- Docs: Documented `get_yaml_config` returns `configuration.yaml` verbatim (inline secrets included; prefer `!secret` references), and that a saved dashboard layout carries the same indirect-control caveat as authored automations.

- Docs: Explained the authoring indirect-control boundary more precisely: granting `cap_automation_write` or `cap_script_write` is broad HA write access, because a stored automation's actions run under Home Assistant natively and are not re-gated by ATM capabilities or MESA when it fires.

### 2.0.0

See the [v2.0.0 release notes](https://github.com/sfox38/ATM/releases/tag/v2.0.0).

## Issues and feedback

Report issues at [github.com/sfox38/ATM/issues](https://github.com/sfox38/ATM/issues).

