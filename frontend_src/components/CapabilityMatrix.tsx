import React from "react";
import type { CapMode, CapName, CapTier, TokenRecord, PatchTokenBody } from "../types";
import { api } from "../api";

interface Props {
  token: TokenRecord;
  onUpdate: (updated: TokenRecord) => void;
}

interface CapDef {
  key: CapName;
  label: string;
  description: string;
  tier: CapTier;
  confirmAvailable: boolean;
}

const CAPS: CapDef[] = [
  {
    key: "cap_config_read",
    label: "Config read",
    description: "Read HA configuration data and event-listener counts.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_template_render",
    label: "Template render",
    description: "Render Jinja2 templates against the token's accessible entities.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_log_read",
    label: "Log read",
    description: "Read Home Assistant system log entries.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_search",
    label: "Search & discovery",
    description: "Search entities, get overviews, describe entities/areas, and inspect relationships within the token's scope.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_registry_read",
    label: "Registry read",
    description: "List areas, floors, zones, and devices that contain accessible entities.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_traces",
    label: "Automation traces",
    description: "Read automation execution traces to debug why an automation did or did not run.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_diagnostics",
    label: "Diagnostics",
    description: "Read system health, check configuration, and validate automation/script/scene YAML.",
    tier: "read",
    confirmAvailable: false,
  },
  {
    key: "cap_broadcast",
    label: "Broadcast",
    description: "Announce messages through assist satellite devices.",
    tier: "everyday",
    confirmAvailable: false,
  },
  {
    key: "cap_service_response",
    label: "Service response data",
    description: "Return response payloads from services that support them (e.g. conversation.process).",
    tier: "everyday",
    confirmAvailable: false,
  },
  {
    key: "cap_automation_write",
    label: "Automation write",
    description: "Create, edit, and delete automations. Bypasses entity-level access controls.",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_script_write",
    label: "Script write",
    description: "Create, edit, and delete scripts. Bypasses entity-level access controls.",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_scene_write",
    label: "Scene write",
    description: "Create, edit, and delete scenes. Referenced entities are still subject to MESA and entity access.",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_helper_write",
    label: "Helper write",
    description: "Create, edit, and delete helpers (input_*, counter, timer).",
    tier: "config_write",
    confirmAvailable: true,
  },
  {
    key: "cap_physical_control",
    label: "Physical control",
    description: "Lock, alarm, and cover mutation services (lock.unlock, alarm.disarm, cover.open_cover, etc).",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_restart",
    label: "HA restart / stop",
    description: "Permits homeassistant.restart and homeassistant.stop service calls.",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_integration_write",
    label: "Integration management",
    description: "Enable or disable Home Assistant integrations.",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_lovelace_write",
    label: "Dashboard write",
    description: "Create, edit, and delete Lovelace dashboards.",
    tier: "system",
    confirmAvailable: true,
  },
  {
    key: "cap_backup",
    label: "Backup",
    description: "Create and list Home Assistant backups. Restoring is not exposed through ATM; restore from the Home Assistant UI.",
    tier: "irreversible",
    confirmAvailable: true,
  },
  {
    key: "cap_filesystem",
    label: "Filesystem access",
    description: "Read and write files under www/, themes/, and custom_templates/.",
    tier: "irreversible",
    confirmAvailable: true,
  },
  {
    key: "cap_yaml_edit",
    label: "Raw YAML edit",
    description: "Read and edit configuration.yaml directly. High blast radius; Confirm strongly recommended. Reading returns the file verbatim, so inline secrets are exposed; keep secrets in secrets.yaml via !secret.",
    tier: "irreversible",
    confirmAvailable: true,
  },
];

// All capability keys, in matrix order. Exported so other views (e.g. the
// Token Detail summary) can tally allow/confirm/deny without re-listing caps.
export const CAP_NAMES: CapName[] = CAPS.map((c) => c.key);

const TIER_LABELS: Record<CapTier, string> = {
  read: "Reads",
  everyday: "Everyday actions",
  config_write: "Configuration writes",
  system: "System actions",
  irreversible: "Irreversible",
};

const TIER_ORDER: CapTier[] = ["read", "everyday", "config_write", "system", "irreversible"];

const MODE_LABEL: Record<CapMode, string> = {
  deny: "Deny",
  allow: "Allow",
  confirm: "Confirm",
};

const MODE_DESC: Record<CapMode, string> = {
  deny: "Capability is blocked.",
  allow: "Capability is granted; tool calls execute immediately.",
  confirm: "Each call queues a pending approval that an admin must approve from the panel.",
};

export function CapabilityMatrix({ token, onUpdate }: Props) {
  const [saving, setSaving] = React.useState<CapName | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function setMode(cap: CapName, mode: CapMode) {
    if (token[cap] === mode) return;
    setSaving(cap);
    setError(null);
    try {
      const body = { [cap]: mode } as unknown as PatchTokenBody;
      const updated = await api.patchToken(token.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to update capability.");
    } finally {
      setSaving(null);
    }
  }

  // Group caps by tier and preserve order.
  const grouped: Record<CapTier, CapDef[]> = {
    read: [],
    everyday: [],
    config_write: [],
    system: [],
    irreversible: [],
  };
  for (const def of CAPS) grouped[def.tier].push(def);

  return (
    <div className="capability-matrix">
      {error && <div className="banner banner-error mb-8">{error}</div>}
      {token.pass_through && (
        <div className="amber-block mb-8">
          <p>
            <strong>Pass-through is enabled.</strong> Reads and everyday actions below are bypassed (treated as Allow). All write, system, and irreversible caps, plus log read, remain enforced as set. Caps set to <em>Confirm</em> are still gated even under pass-through.
          </p>
        </div>
      )}
      {TIER_ORDER.map((tier) => {
        const items = grouped[tier];
        if (items.length === 0) return null;
        return (
          <div key={tier} className="cap-tier-group">
            <div className="cap-tier-header">{TIER_LABELS[tier]}</div>
            {items.map((cap) => {
              const current = token[cap.key];
              const isSaving = saving === cap.key;
              return (
                <div key={cap.key} className="cap-row">
                  <div className="cap-row-label">
                    <div className="cap-row-name">{cap.label}</div>
                    <div className="cap-row-desc">{cap.description}</div>
                  </div>
                  <div className="cap-row-modes" role="radiogroup" aria-label={cap.label}>
                    <ModeRadio
                      cap={cap.key}
                      mode="deny"
                      current={current}
                      onSelect={setMode}
                      disabled={isSaving}
                    />
                    <ModeRadio
                      cap={cap.key}
                      mode="allow"
                      current={current}
                      onSelect={setMode}
                      disabled={isSaving}
                    />
                    <ModeRadio
                      cap={cap.key}
                      mode="confirm"
                      current={current}
                      onSelect={setMode}
                      disabled={isSaving || !cap.confirmAvailable}
                      unavailable={!cap.confirmAvailable}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

interface ModeRadioProps {
  cap: CapName;
  mode: CapMode;
  current: CapMode;
  onSelect: (cap: CapName, mode: CapMode) => void;
  disabled: boolean;
  unavailable?: boolean;
}

function ModeRadio({ cap, mode, current, onSelect, disabled, unavailable }: ModeRadioProps) {
  const checked = current === mode;
  const id = `${cap}-${mode}`;
  return (
    <label
      htmlFor={id}
      className={`mode-radio mode-${mode}${checked ? " mode-radio-checked" : ""}${unavailable ? " mode-radio-unavailable" : ""}`}
      title={unavailable ? "Confirm gating is not meaningful for this capability." : MODE_DESC[mode]}
    >
      <input
        id={id}
        type="radio"
        name={cap}
        value={mode}
        checked={checked}
        disabled={disabled}
        onChange={() => onSelect(cap, mode)}
      />
      <span className="mode-radio-dot" />
      <span className="mode-radio-label">{MODE_LABEL[mode]}</span>
    </label>
  );
}
