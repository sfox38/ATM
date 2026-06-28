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

## Issues and feedback

Report issues at [github.com/sfox38/ATM/issues](https://github.com/sfox38/ATM/issues).
