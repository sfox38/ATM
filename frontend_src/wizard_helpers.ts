// Pure helpers for the onboarding wizard, kept here so they can be unit-tested
// without rendering React components.
import type { EntityTree, PermissionTree } from "./types";

// The ATM MCP endpoint, built from the origin the admin is currently browsing.
export function buildMcpUrl(origin: string): string {
  return `${origin.replace(/\/+$/, "")}/api/atm/mcp`;
}

// The server name used in agent configs. Kept short and hyphen-safe.
export const MCP_SERVER_NAME = "atm-home-assistant";

// The verified `claude mcp add` command. ATM authenticates via
// `Authorization: Bearer atm_<token>` (NOT X-API-Key, NOT OAuth) and the modern
// Streamable HTTP transport is `--transport http`.
export function buildClaudeCommand(url: string, token: string): string {
  return `claude mcp add --transport http ${MCP_SERVER_NAME} ${url} --header "Authorization: Bearer ${token}"`;
}

// A generic MCP server config block for clients that use a JSON file
// (the MCP spec calls this transport streamable-http; "http" is the common alias).
export function buildMcpJson(url: string, token: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        [MCP_SERVER_NAME]: {
          type: "http",
          url,
          headers: { Authorization: `Bearer ${token}` },
        },
      },
    },
    null,
    2,
  );
}

// Gemini CLI: `gemini mcp add <name> <url> --transport http --header ...`.
export function buildGeminiCommand(url: string, token: string): string {
  return `gemini mcp add ${MCP_SERVER_NAME} ${url} --transport http --header "Authorization: Bearer ${token}"`;
}

// The value for an Authorization header (for GUI clients that take a header
// key/value, like Codex's "Connect to a custom MCP" form).
export function buildBearerValue(token: string): string {
  return `Bearer ${token}`;
}

// Codex's `codex mcp add` CLI is stdio-only; remote HTTP servers are added via
// its GUI or ~/.codex/config.toml, which reads the token from an env var.
export const CODEX_TOKEN_ENV = "ATM_TOKEN";
export function buildCodexEnv(token: string): string {
  return `export ${CODEX_TOKEN_ENV}="${token}"`;
}
export function buildCodexConfig(url: string): string {
  return `[mcp_servers.${MCP_SERVER_NAME}]\nurl = "${url}"\nbearer_token_env_var = "${CODEX_TOKEN_ENV}"`;
}

// Cursor reads remote servers from ~/.cursor/mcp.json using url + headers
// (no "type" field), so it gets its own builder.
export function buildCursorJson(url: string, token: string): string {
  return JSON.stringify(
    { mcpServers: { [MCP_SERVER_NAME]: { url, headers: { Authorization: `Bearer ${token}` } } } },
    null,
    2,
  );
}

export interface AgentBlock {
  title?: string;
  hint?: string;
  code?: string;
  // Labeled key/value pairs to copy individually (e.g. a GUI header key + value).
  fields?: { label: string; value: string }[];
}
export interface AgentTab {
  key: string;
  label: string;
  href: string;
  intro?: string;
  blocks: AgentBlock[];
}

// Per-agent connection instructions, default first (Claude Code). Verified
// against each tool's current docs; DeepSeek is a model used via an MCP client,
// so it gets the generic config rather than a CLI command.
export function buildAgentTabs(url: string, token: string): AgentTab[] {
  return [
    {
      key: "claude",
      label: "Claude Code",
      href: "https://docs.claude.com/en/docs/claude-code/mcp",
      blocks: [
        { hint: "Run in your terminal, then verify with: claude mcp list", code: buildClaudeCommand(url, token) },
      ],
    },
    {
      key: "gemini",
      label: "Gemini CLI",
      href: "https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html",
      blocks: [{ hint: "Run in your terminal.", code: buildGeminiCommand(url, token) }],
    },
    {
      key: "codex",
      label: "Codex",
      href: "https://developers.openai.com/codex/mcp",
      intro: "Codex's codex mcp add CLI only adds stdio servers, so use the GUI (easiest) or the config file below.",
      blocks: [
        {
          title: "Easiest: the Codex app GUI",
          hint: `Open Settings > MCP Servers > Add Server. Set Name to ${MCP_SERVER_NAME}, Transport to Streamable HTTP, paste the URL from above, and leave "Bearer token env var" empty. Then under Headers click Add header and enter these two values exactly (key on the left, value on the right), and Save.`,
          fields: [
            { label: "Server name", value: MCP_SERVER_NAME },
            { label: "Header key", value: "Authorization" },
            { label: "Header value", value: buildBearerValue(token) },
          ],
        },
        {
          title: "Alternative: add to ~/.codex/config.toml",
          hint: "Put this block in that file.",
          code: buildCodexConfig(url),
        },
        {
          title: "...and set ATM_TOKEN in your shell (NOT in config.toml)",
          hint: "macOS/Linux: run the line below, and add it to ~/.zshrc or ~/.bashrc to make it persist. Windows PowerShell: setx ATM_TOKEN \"<your token>\". This sets the environment variable the config file reads.",
          code: buildCodexEnv(token),
        },
      ],
    },
    {
      key: "cursor",
      label: "Cursor",
      href: "https://cursor.com/docs/mcp",
      intro: "Add this to ~/.cursor/mcp.json (applies everywhere) or .cursor/mcp.json in a project, then enable the server under Cursor Settings > MCP.",
      blocks: [{ code: buildCursorJson(url, token) }],
    },
    {
      key: "other",
      label: "Other (MCP spec)",
      href: "https://modelcontextprotocol.io/",
      intro: "Standard MCP server config. The spec calls this transport streamable-http.",
      blocks: [{ code: buildMcpJson(url, token) }],
    },
  ];
}

export interface TestPrompts {
  // A benign read that works in any MESA mode and reliably trips connection
  // detection (any authenticated MCP call counts).
  read: string;
  // A control action, suppressed under MESA enforced mode where it may require
  // admin confirmation and would not cleanly demonstrate the connection.
  action: string | null;
}

export function buildTestPrompt(friendlyName: string, mesaEnforced: boolean): TestPrompts {
  return {
    read: "List my Home Assistant lights",
    action: mesaEnforced ? null : `Please toggle the ${friendlyName}`,
  };
}

// The first entity granted full (GREEN = WRITE) access in a permission tree, or
// null. The wizard grants exactly one, so this identifies the chosen entity.
export function firstGreenEntity(tree: PermissionTree): string | null {
  for (const [entityId, node] of Object.entries(tree.entities)) {
    if (node.state === "GREEN") return entityId;
  }
  return null;
}

// Resolve the granted target to a concrete entity ID, accepting a grant made at
// the entity, device, or domain level (a device or domain grant cascades to its
// entities). Returns the first matching entity so the wizard always has a real
// entity to build its test prompt from, no matter which node the user clicked.
export function firstGreenTarget(tree: PermissionTree, entityTree: EntityTree | null): string | null {
  const direct = firstGreenEntity(tree);
  if (direct) return direct;
  if (!entityTree) return null;

  const greenDevices = new Set(
    Object.entries(tree.devices).filter(([, n]) => n.state === "GREEN").map(([d]) => d),
  );
  const greenDomains = new Set(
    Object.entries(tree.domains).filter(([, n]) => n.state === "GREEN").map(([d]) => d),
  );
  if (greenDevices.size === 0 && greenDomains.size === 0) return null;

  for (const [domain, dt] of Object.entries(entityTree)) {
    if (greenDomains.has(domain)) {
      const ids = Object.keys(dt.entity_details);
      if (ids.length) return ids[0];
    }
    for (const [eid, info] of Object.entries(dt.entity_details)) {
      if (info.device_id && greenDevices.has(info.device_id)) return eid;
    }
  }
  return null;
}
