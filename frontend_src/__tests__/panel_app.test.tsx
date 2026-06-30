import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { waitFor } from "@testing-library/react";

// Companion to panel_shell.test.tsx. That test stubs react-dom/client to isolate
// the custom-element shell; this one lets the REAL ATMApp tree render (with a
// mocked api) so the app-level effects are covered: token loading propagating
// into the list, the pending-approval count badge, and #approvals deep-linking.

vi.mock("../atm-panel.css?inline", () => ({ default: "" }));

const { apiMock } = vi.hoisted(() => {
  const CAPS = [
    "cap_config_read", "cap_template_render", "cap_log_read", "cap_search",
    "cap_registry_read", "cap_traces", "cap_diagnostics", "cap_broadcast",
    "cap_service_response", "cap_automation_write", "cap_script_write",
    "cap_scene_write", "cap_helper_write", "cap_physical_control", "cap_restart",
    "cap_integration_write", "cap_lovelace_write", "cap_registry_write",
    "cap_backup", "cap_filesystem", "cap_yaml_edit",
  ];
  const token = {
    id: "tok-1", name: "shell-token", created_at: new Date().toISOString(),
    created_by: "admin", expires_at: null, revoked: false, last_used_at: null,
    updated_at: null, pass_through: false, announce_all_tools: false,
    persona: "custom", rate_limit_requests: 60, rate_limit_burst: 10,
    permissions: { domains: {}, devices: {}, entities: {} },
    ...Object.fromEntries(CAPS.map((c) => [c, "deny"])),
  };
  const settings = {
    kill_switch: false, disable_all_logging: false, log_allowed: true,
    log_denied: true, log_rate_limited: true, log_entity_names: true,
    log_client_ip: true, notify_on_rate_limit: false, notify_on_approval: true,
    audit_flush_interval: 15, audit_log_maxlen: 10000, mesa_mode: "advisory",
  };
  // Specific returns for the app-mount calls; everything else gets a permissive
  // empty shape so any child effect resolves without throwing.
  const overrides: Record<string, (...a: unknown[]) => Promise<unknown>> = {
    listTokens: async () => [token],
    getSettings: async () => settings,
    listApprovals: async () => ({ approvals: [], total: 3 }),
    getEntityHints: async () => ({ entity_hints: {} }),
  };
  const permissive = {
    approvals: [], tokens: [], items: [], versions: [], total: 0,
    entity_hints: {}, count: 0,
  };
  const apiMock = new Proxy({}, {
    get: (_t, prop: string) => overrides[prop] ?? (async () => permissive),
  });
  return { apiMock };
});

vi.mock("../api", () => ({
  api: apiMock,
  setHass: () => {},
  ApiError: class extends Error {},
}));

await import("../index");

function mountPanel(): HTMLElement & { hass: unknown } {
  const el = document.createElement("atm-panel") as HTMLElement & { hass: unknown };
  document.body.appendChild(el);
  el.hass = { user: { id: "u1" } };  // render is a no-op until hass is set
  return el;
}

describe("atm-panel full app shell", () => {
  beforeEach(() => {
    try { localStorage.clear(); } catch { /* ignore */ }
    window.location.hash = "";
  });

  afterEach(() => {
    document.querySelectorAll("atm-panel").forEach((el) => el.remove());
  });

  it("loads tokens through the real tree and shows them in the list", async () => {
    const el = mountPanel();
    await waitFor(() => {
      expect(el.shadowRoot!.textContent).toContain("shell-token");
    });
  });

  it("renders the pending-approval count badge from listApprovals", async () => {
    const el = mountPanel();
    await waitFor(() => {
      const badge = el.shadowRoot!.querySelector(".atm-tab-badge");
      expect(badge?.textContent).toBe("3");
    });
  });

  it("deep-links to the Approvals tab from the #approvals hash", async () => {
    window.location.hash = "#approvals";
    const el = mountPanel();
    await waitFor(() => {
      // The content panel id tracks the active tab; the hash handler selects it.
      expect(el.shadowRoot!.querySelector("#atm-tabpanel-approvals")).toBeTruthy();
    });
  });
});
