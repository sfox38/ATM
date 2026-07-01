import { describe, it, expect, vi } from "vitest";
import { formatDate, formatDateTime, tokenStatus, copyToClipboard, HIGH_RISK_DOMAINS } from "../utils";
import type { TokenRecord } from "../types";

function makeToken(overrides: Partial<TokenRecord> = {}): TokenRecord {
  return {
    id: "tok-1",
    name: "test-token",
    created_at: "2025-01-01T00:00:00Z",
    created_by: "user1",
    expires_at: null,
    revoked: false,
    last_used_at: null,
    updated_at: null,
    pass_through: false,
    persona: "custom",
    rate_limit_requests: 60,
    rate_limit_burst: 10,
    cap_automation_write: "deny",
    cap_script_write: "deny",
    cap_log_read: "deny",
    cap_config_read: "deny",
    cap_template_render: "deny",
    cap_restart: "deny",
    cap_physical_control: "deny",
    cap_service_response: "deny",
    cap_broadcast: "deny",
    cap_search: "deny",
    cap_registry_read: "deny",
    cap_traces: "deny",
    cap_diagnostics: "deny",
    cap_scene_write: "deny",
    cap_helper_write: "deny",
    cap_integration_write: "deny",
    cap_lovelace_write: "deny",
    cap_registry_write: "deny",
    cap_backup: "deny",
    cap_filesystem: "deny",
    cap_yaml_edit: "deny",
    permissions: { domains: {}, devices: {}, entities: {} },
    ...overrides,
  };
}

describe("formatDate", () => {
  it("returns 'Never' for null", () => {
    expect(formatDate(null)).toBe("Never");
  });

  it("returns a locale date string for a valid ISO string", () => {
    const result = formatDate("2025-01-15T12:00:00Z");
    expect(result).toBeTruthy();
    expect(result).not.toBe("Never");
  });
});

describe("formatDateTime", () => {
  it("returns 'Never' for null", () => {
    expect(formatDateTime(null)).toBe("Never");
  });

  it("returns a locale datetime string for a valid ISO string", () => {
    const result = formatDateTime("2025-01-15T12:30:00Z");
    expect(result).toBeTruthy();
    expect(result).not.toBe("Never");
  });
});

describe("tokenStatus", () => {
  it("returns 'Active' for a valid non-revoked token without expiry", () => {
    expect(tokenStatus(makeToken())).toBe("Active");
  });

  it("returns 'Revoked' for a revoked token", () => {
    expect(tokenStatus(makeToken({ revoked: true }))).toBe("Revoked");
  });

  it("returns 'Expired' for a token with a past expiry date", () => {
    expect(tokenStatus(makeToken({ expires_at: "2020-01-01T00:00:00Z" }))).toBe("Expired");
  });

  it("returns 'Active' for a token with a future expiry date", () => {
    expect(tokenStatus(makeToken({ expires_at: "2099-12-31T23:59:59Z" }))).toBe("Active");
  });

  it("returns 'Revoked' even if also expired (revoked takes priority)", () => {
    expect(tokenStatus(makeToken({ revoked: true, expires_at: "2020-01-01T00:00:00Z" }))).toBe("Revoked");
  });
});

describe("copyToClipboard", () => {
  it("uses navigator.clipboard.writeText when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    await copyToClipboard("test-value");
    expect(writeText).toHaveBeenCalledWith("test-value");
  });

  it("falls back to textarea when clipboard API is unavailable", async () => {
    Object.assign(navigator, { clipboard: undefined });
    const execCommand = vi.fn();
    document.execCommand = execCommand;

    await copyToClipboard("fallback-value");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });
});

describe("HIGH_RISK_DOMAINS", () => {
  it("contains expected domains", () => {
    expect(HIGH_RISK_DOMAINS.has("homeassistant")).toBe(true);
    expect(HIGH_RISK_DOMAINS.has("backup")).toBe(true);
    expect(HIGH_RISK_DOMAINS.has("mqtt")).toBe(true);
  });

  it("does not contain non-risky domains", () => {
    expect(HIGH_RISK_DOMAINS.has("light")).toBe(false);
    expect(HIGH_RISK_DOMAINS.has("sensor")).toBe(false);
  });
});
