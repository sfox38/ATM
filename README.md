# Advanced Token Management (ATM)

Why give your AI agent basically unrestricted access to your home? ATM is a drop-in replacement for Home Assistant's native MCP server. It implements all 20 native HA MCP tools, so your existing AI client setup works without changes, and adds the control layer the native system has no place for.

Each client gets its own token, scoped to exactly the entities you allow, with its own rate limit and optional expiry. Every request is logged. If a token is ever compromised, one click revokes it and terminates all open connections immediately. ATM runs entirely inside Home Assistant: no extra process, no cloud dependency, no configuration beyond the ATM panel.

## Documentation

**The full documentation lives at [sfox38.github.io/atm](https://sfox38.github.io/atm/).** It is a complete, browsable guide; this README is the short version.

| Guide | What it covers |
|---|---|
| [Installation](https://sfox38.github.io/atm/install.html) | HACS and manual install, first-time setup |
| [Connect an AI client](https://sfox38.github.io/atm/connect.html) | The MCP connection, the agent skill, third-party servers |
| [Permissions](https://sfox38.github.io/atm/permissions.html) | The four states, two-pass resolution, worked examples |
| [Capabilities & pass-through](https://sfox38.github.io/atm/capabilities.html) | The capability flags, pass-through mode, MESA |
| [Tools reference](https://sfox38.github.io/atm/tools.html) | Every tool, its parameters, and the capability it needs |
| [Security](https://sfox38.github.io/atm/security.html) | Token design, enforcement, scrubbing, the kill switch |
| [Operations & API](https://sfox38.github.io/atm/operations.html) | Rate limiting, sensors, settings, audit log, routes |

## Why ATM instead of a long-lived access token

A long-lived access token (LLAT) plus the native MCP server gives every client the same all-or-nothing view of your home. ATM keeps the same tools and adds per-client control.

| | LLAT + native MCP | ATM token |
|---|---|---|
| MCP tool compatibility | 20 native tools | Same 20 tools, identical names and responses, plus 16 more |
| Entity filtering | Binary: expose or hide, same for all clients | Four permission states, per token |
| Per-client control | No, all clients share one exposed set | Yes, independent permissions per token |
| Read-only access | No | Yes, READ allows reads and blocks writes |
| Audit trail | None | Every request logged with outcome and entity |
| Rate limiting | None | Per token, configurable |
| Expiry | None | Optional, auto-archived on expiry |
| Revocation | Revoke the LLAT on the HA profile page | Instant, terminates open connections immediately |
| Sensitive attribute scrubbing | None | Always applied |
| Client reconfiguration | `/api/mcp` | `/api/atm/mcp` (URL change only) |

If you are connecting Claude Code, Cursor, ChatGPT, Antigravity, or any other AI tool to Home Assistant, ATM gives you control the native system cannot.

## Requirements

- Home Assistant 2024.5.0 or later.
- One ATM instance per Home Assistant. No Python dependencies beyond what HA ships.

## Installation

### Via HACS

1. In HACS, open **Integrations**, then the top-right menu, and choose **Custom repositories**.
2. Enter `https://github.com/sfox38/atm` and select **Integration** as the category, then click **Add**.
3. Find ATM in the HACS integration list, install it, and restart Home Assistant.

### Manual

1. Copy the `custom_components/atm` folder into your Home Assistant config directory under `custom_components/atm`.
2. Restart Home Assistant.

### Setup

Go to **Settings > Devices & services > Add integration** and search for **Advanced Token Management**. Click through the single-step config flow, then open the **ATM** panel in your sidebar to manage tokens.

Full walkthrough: [Installation](https://sfox38.github.io/atm/install.html).

## Connect an AI client

ATM exposes an MCP endpoint at `/api/atm/mcp`.

1. In the ATM panel, create a token and copy its value. It is shown only once.
2. Scope it in the permission tree and enable any capability flags it needs.
3. Add the server to your client, for example with Claude Code:

```bash
claude mcp add --transport http home-assistant \
  http://your-ha-address:8123/api/atm/mcp \
  --header "Authorization: Bearer atm_your_token_here"
```

Start a new session, run `/mcp`, and confirm the `home-assistant` server is connected. Full walkthrough, including the optional agent skill: [Connect an AI client](https://sfox38.github.io/atm/connect.html).

## Key concepts

**The permission tree.** Every token has a tree of domains, devices, and entities. Each node is GREEN (read and write), YELLOW (read-only), RED (hard deny, which blocks everything beneath it), or GREY (inherit from the parent). A two-pass resolver scans the ancestor chain for RED first, then takes the nearest non-grey grant. See [Permissions](https://sfox38.github.io/atm/permissions.html).

**Capability flags.** Twenty opt-in flags gate the powerful operations: restarting HA, controlling locks and covers, writing automations, reading logs, editing YAML. They are off by default, and the riskier ones can be set to require human approval. See [Capabilities](https://sfox38.github.io/atm/capabilities.html).

**Pass-through mode.** Trusted tokens can skip the tree and see everything, but they still face the capability gates, the `atm` blocklist, attribute scrubbing, and rate limiting. Because pass-through ships the whole house into every read, the tree is also a cost control. See [Capabilities & pass-through](https://sfox38.github.io/atm/capabilities.html).

**MESA.** A per-entity safety layer runs last, on the flattened entity list. Some entities are read-only or require confirmation by nature, regardless of what a token allows. See [Capabilities & pass-through](https://sfox38.github.io/atm/capabilities.html#mesa).

## Security at a glance

- Tokens are 68 characters with a fixed `atm_` prefix. Only the SHA-256 hash is stored, comparisons are constant-time, and tokens are accepted only in the `Authorization: Bearer` header.
- "Not found" and "inaccessible" return identical responses, so a caller cannot probe for entities it lacks access to.
- Four sensitive attributes (`access_token`, `entity_picture`, `stream_url`, `still_image_url`) are stripped from every response, for every token.
- A kill switch can unregister all proxy and MCP routes at startup, leaving nothing on the network to answer. The admin panel stays available to switch it back off.

Details: [Security](https://sfox38.github.io/atm/security.html).

## Issues and feedback

Report issues at [github.com/sfox38/atm/issues](https://github.com/sfox38/atm/issues).
