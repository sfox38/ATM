import { describe, it, expect } from "vitest";
import { buildAgentTabs, buildBearerValue, buildClaudeCommand, buildCodexConfig, buildCursorJson, buildGeminiCommand, buildMcpJson, buildMcpUrl, buildSkillInstall, buildTestPrompt, firstGreenEntity, firstGreenTarget, skillUrlFromMcp } from "../wizard_helpers";
import { PERSONA_CAP_DEFAULTS } from "../personas";
import type { EntityTree, PermissionTree } from "../types";

describe("buildMcpUrl", () => {
  it("appends the ATM MCP path to the origin", () => {
    expect(buildMcpUrl("https://x.test")).toBe("https://x.test/api/atm/mcp");
  });
  it("does not double a trailing slash", () => {
    expect(buildMcpUrl("https://x.test/")).toBe("https://x.test/api/atm/mcp");
  });
});

describe("buildClaudeCommand", () => {
  it("uses --transport http and an Authorization: Bearer header", () => {
    const cmd = buildClaudeCommand("http://192.168.1.11:8123/api/atm/mcp", "atm_abc");
    expect(cmd).toContain("claude mcp add --transport http");
    expect(cmd).toContain("http://192.168.1.11:8123/api/atm/mcp");
    expect(cmd).toContain('--header "Authorization: Bearer atm_abc"');
    expect(cmd).not.toContain("X-API-Key");
  });
});

describe("buildMcpJson", () => {
  it("produces an http server config with a Bearer header", () => {
    const cfg = JSON.parse(buildMcpJson("http://ha/api/atm/mcp", "atm_xyz"));
    const server = cfg.mcpServers["atm-home-assistant"];
    expect(server.type).toBe("http");
    expect(server.url).toBe("http://ha/api/atm/mcp");
    expect(server.headers.Authorization).toBe("Bearer atm_xyz");
  });
});

describe("buildGeminiCommand", () => {
  it("uses gemini mcp add with --transport http and a Bearer header", () => {
    const cmd = buildGeminiCommand("http://ha/api/atm/mcp", "atm_abc");
    expect(cmd).toContain("gemini mcp add atm-home-assistant http://ha/api/atm/mcp");
    expect(cmd).toContain("--transport http");
    expect(cmd).toContain('--header "Authorization: Bearer atm_abc"');
  });
});

describe("buildCodexConfig", () => {
  it("emits a config.toml table with url and bearer_token_env_var", () => {
    const cfg = buildCodexConfig("http://ha/api/atm/mcp");
    expect(cfg).toContain("[mcp_servers.atm-home-assistant]");
    expect(cfg).toContain('url = "http://ha/api/atm/mcp"');
    expect(cfg).toContain('bearer_token_env_var = "ATM_TOKEN"');
  });
});

describe("buildAgentTabs", () => {
  it("returns the five agents with Claude Code first and Cursor (not DeepSeek)", () => {
    const tabs = buildAgentTabs("http://ha/api/atm/mcp", "atm_abc");
    expect(tabs.map((t) => t.key)).toEqual(["claude", "gemini", "codex", "cursor", "other"]);
    expect(tabs[0].blocks[0].code).toContain("claude mcp add");
    // Every tab carries a docs link and at least one block.
    for (const t of tabs) {
      expect(t.href).toMatch(/^https:\/\//);
      expect(t.blocks.length).toBeGreaterThan(0);
    }
  });

  it("Codex tab gives the exact Authorization header key/value as fields", () => {
    const codex = buildAgentTabs("http://ha/api/atm/mcp", "atm_abc").find((t) => t.key === "codex")!;
    const guiFields = codex.blocks[0].fields!;
    expect(guiFields).toContainEqual({ label: "Header key", value: "Authorization" });
    expect(guiFields).toContainEqual({ label: "Header value", value: "Bearer atm_abc" });
  });
});

describe("buildCursorJson", () => {
  it("uses url + headers without a type field (Cursor format)", () => {
    const cfg = JSON.parse(buildCursorJson("http://ha/api/atm/mcp", "atm_abc"));
    const server = cfg.mcpServers["atm-home-assistant"];
    expect(server.url).toBe("http://ha/api/atm/mcp");
    expect(server.headers.Authorization).toBe("Bearer atm_abc");
    expect(server.type).toBeUndefined();
  });
});

describe("buildTestPrompt", () => {
  it("includes a read prompt and an action prompt when not enforced", () => {
    const p = buildTestPrompt("Desk Lamp", false);
    expect(p.read).toBe("List my Home Assistant lights");
    expect(p.action).toBe("Please toggle the Desk Lamp");
  });
  it("suppresses the action prompt under MESA enforced mode", () => {
    const p = buildTestPrompt("Desk Lamp", true);
    expect(p.read).toBe("List my Home Assistant lights");
    expect(p.action).toBeNull();
  });
});

describe("firstGreenEntity", () => {
  const tree = (entities: Record<string, string>): PermissionTree => ({
    domains: {},
    devices: {},
    entities: Object.fromEntries(
      Object.entries(entities).map(([k, v]) => [k, { state: v as never, hint: null }]),
    ),
  });

  it("returns the GREEN entity", () => {
    expect(firstGreenEntity(tree({ "light.a": "GREY", "light.b": "GREEN" }))).toBe("light.b");
  });
  it("returns null when no entity is GREEN", () => {
    expect(firstGreenEntity(tree({ "light.a": "YELLOW" }))).toBeNull();
  });
});

describe("firstGreenTarget", () => {
  const entityTree: EntityTree = {
    light: {
      devices: {},
      deviceless_entities: [],
      entity_details: {
        "light.lamp": { entity_id: "light.lamp", friendly_name: "Lamp", device_id: "dev1", area_id: null, area_name: null, labels: [] },
        "light.ceiling": { entity_id: "light.ceiling", friendly_name: "Ceiling", device_id: null, area_id: null, area_name: null, labels: [] },
      },
    },
  };
  const tree = (over: Partial<PermissionTree>): PermissionTree => ({
    domains: {}, devices: {}, entities: {}, ...over,
  });

  it("prefers a directly granted entity", () => {
    const t = tree({ entities: { "light.ceiling": { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).toBe("light.ceiling");
  });
  it("resolves a granted device to an entity under it", () => {
    const t = tree({ devices: { dev1: { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).toBe("light.lamp");
  });
  it("resolves a granted domain to an entity in it", () => {
    const t = tree({ domains: { light: { state: "GREEN", hint: null } } });
    expect(firstGreenTarget(t, entityTree)).not.toBeNull();
  });
  it("returns null when nothing is granted", () => {
    expect(firstGreenTarget(tree({}), entityTree)).toBeNull();
  });
});

describe("buildBearerValue", () => {
  it("prefixes the token with Bearer", () => {
    expect(buildBearerValue("atm_x")).toBe("Bearer atm_x");
  });
});

describe("skillUrlFromMcp", () => {
  it("swaps the MCP path for the skill path on the same host", () => {
    expect(skillUrlFromMcp("http://192.168.1.11:8123/api/atm/mcp")).toBe(
      "http://192.168.1.11:8123/api/atm/skill",
    );
  });
  it("tolerates a trailing slash on the MCP path", () => {
    expect(skillUrlFromMcp("https://ha.test/api/atm/mcp/")).toBe("https://ha.test/api/atm/skill");
  });
  it("appends the skill path when the URL is not the MCP path", () => {
    expect(skillUrlFromMcp("https://ha.test")).toBe("https://ha.test/api/atm/skill");
  });
});

describe("buildSkillInstall", () => {
  const url = "http://ha.test/api/atm/skill";

  it("installs a real Claude Code skill at ~/.claude/skills/atm-home-assistant/SKILL.md", () => {
    const blocks = buildSkillInstall(url, "claude");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].code).toContain("~/.claude/skills/atm-home-assistant/SKILL.md");
    expect(blocks[0].code).toContain(url);
  });
  it("writes a Cursor project rule under .cursor/rules", () => {
    const blocks = buildSkillInstall(url, "cursor");
    expect(blocks[0].code).toContain(".cursor/rules/atm-home-assistant.md");
  });
  it("gives non-Claude agents the skill URL as a copyable field plus a download", () => {
    for (const key of ["gemini", "codex", "other"]) {
      const blocks = buildSkillInstall(url, key);
      expect(blocks[0].fields).toContainEqual({ label: "Skill guide URL", value: url });
      expect(blocks[0].code).toContain(url);
    }
  });
  it("names the agent's own context file for Gemini and Codex", () => {
    expect(buildSkillInstall(url, "gemini")[0].hint).toContain("GEMINI.md");
    expect(buildSkillInstall(url, "codex")[0].hint).toContain("Codex project instructions");
  });
});

describe("PERSONA_CAP_DEFAULTS drift guard", () => {
  // Must mirror const.py CAPABILITY_NAMES exactly.
  const CAP_KEYS = [
    "cap_config_read", "cap_template_render", "cap_log_read",
    "cap_search", "cap_registry_read", "cap_traces", "cap_diagnostics",
    "cap_broadcast", "cap_service_response",
    "cap_automation_write", "cap_script_write", "cap_scene_write", "cap_helper_write",
    "cap_physical_control", "cap_restart",
    "cap_integration_write", "cap_lovelace_write",
    "cap_backup", "cap_filesystem", "cap_yaml_edit",
  ];
  it("custom has no preset", () => {
    expect(PERSONA_CAP_DEFAULTS.custom).toBeNull();
  });
  it("every non-custom persona defines exactly the full cap set", () => {
    for (const [key, caps] of Object.entries(PERSONA_CAP_DEFAULTS)) {
      if (key === "custom") continue;
      expect(Object.keys(caps!).sort()).toEqual([...CAP_KEYS].sort());
    }
  });
});
