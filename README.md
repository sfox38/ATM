# Advanced Token Management (ATM)

[![HACS](https://github.com/sfox38/ATM/actions/workflows/hacs.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/hassfest.yml)
[![Tests](https://github.com/sfox38/ATM/actions/workflows/tests.yml/badge.svg)](https://github.com/sfox38/ATM/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

Why give your AI agent unrestricted access to your home? ATM is more than a drop-in replacement for Home Assistant's native MCP server. It implements all 20 native HA MCP tools, so an existing AI client works without changes, and adds the control layer the native system has no place for: each client gets its own token, scoped to exactly the entities you allow, with its own rate limit, optional expiry, and a full audit trail. If a token is ever compromised, revoking it takes effect immediately, and its next request is rejected. ATM runs entirely inside Home Assistant: no extra process, no cloud dependency, and no configuration beyond the ATM panel.

## Documentation

The full documentation, including the tool reference, permissions, capabilities, MESA, and the admin API, lives at **[sfox38.github.io/atm](https://sfox38.github.io/ATM/)**.

New here? Start with the **[Quick start](https://sfox38.github.io/ATM/quickstart.html)**: it creates your first token, connects your agent, and tests the connection in a few minutes.

## How ATM compares

We road-tested ATM against Home Assistant's built-in MCP server and the popular community server, one agent model, one synthetic home, the same tasks. ATM was the cheapest per completed task, the only server that left every off-limits device untouched, and the most capable. See the **[road test report](https://sfox38.github.io/atm-roadtest/)**, or reproduce it from the **[benchmark repository](https://github.com/sfox38/atm-roadtest)**.

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
