import React from "react";
import type { Persona, TokenRecord } from "../types";
import { api } from "../api";

interface Props {
  token: TokenRecord;
  onUpdate: (updated: TokenRecord) => void;
}

interface PersonaDef {
  key: Persona;
  label: string;
  description: string;
}

const PERSONAS: PersonaDef[] = [
  {
    key: "read_only",
    label: "Read-only observer",
    description: "Reads state, history, logs, templates. No actions, no broadcast.",
  },
  {
    key: "voice_assistant",
    label: "Voice assistant",
    description: "Reads + service calls + broadcast. Locks, alarms, and covers require admin confirmation.",
  },
  {
    key: "automation_builder",
    label: "Automation builder",
    description: "Voice assistant + automation/script CRUD. Restart and physical actions require confirmation.",
  },
  {
    key: "power_user",
    label: "Power user",
    description: "Full reads and writes, restart allowed. Physical actions still require confirmation.",
  },
  {
    key: "custom",
    label: "Custom",
    description: "Configure each capability individually below.",
  },
];

export function PersonaPicker({ token, onUpdate }: Props) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function applyPersona(persona: Persona) {
    setSaving(true);
    setError(null);
    try {
      // Fetch the persona's cap defaults from a hardcoded mirror of personas.py.
      // Backend validation is the source of truth; this is a UX shortcut so we
      // can patch all caps in one request.
      const caps = PERSONA_CAP_DEFAULTS[persona];
      const body: Record<string, unknown> = { persona };
      if (caps) {
        Object.assign(body, caps);
      }
      const updated = await api.patchToken(token.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to apply persona.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="persona-picker">
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <p className="persona-helper">
        A persona seeds the capability matrix below. After applying, you may override individual capabilities; the persona label will show "(modified)" if any value diverges from the preset.
      </p>
      <div className="persona-grid">
        {PERSONAS.map((p) => {
          const active = token.persona === p.key;
          return (
            <button
              key={p.key}
              type="button"
              className={`persona-card${active ? " persona-card-active" : ""}`}
              onClick={() => !saving && !active && applyPersona(p.key)}
              disabled={saving}
              aria-pressed={active}
            >
              <div className="persona-card-label">{p.label}</div>
              <div className="persona-card-desc">{p.description}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Mirror of personas.py PERSONA_DEFINITIONS. Used to pre-fill cap values when
// applying a persona via PATCH so the matrix updates in one round-trip.
// If this drifts from the backend, the backend validates each value and the
// PATCH fails closed.
const PERSONA_CAP_DEFAULTS: Record<Persona, Record<string, string> | null> = {
  read_only: {
    cap_config_read: "allow",
    cap_template_render: "allow",
    cap_log_read: "allow",
    cap_broadcast: "deny",
    cap_service_response: "allow",
    cap_automation_write: "deny",
    cap_script_write: "deny",
    cap_physical_control: "deny",
    cap_restart: "deny",
  },
  voice_assistant: {
    cap_config_read: "allow",
    cap_template_render: "allow",
    cap_log_read: "allow",
    cap_broadcast: "allow",
    cap_service_response: "allow",
    cap_automation_write: "deny",
    cap_script_write: "deny",
    cap_physical_control: "confirm",
    cap_restart: "deny",
  },
  automation_builder: {
    cap_config_read: "allow",
    cap_template_render: "allow",
    cap_log_read: "allow",
    cap_broadcast: "allow",
    cap_service_response: "allow",
    cap_automation_write: "allow",
    cap_script_write: "allow",
    cap_physical_control: "confirm",
    cap_restart: "confirm",
  },
  power_user: {
    cap_config_read: "allow",
    cap_template_render: "allow",
    cap_log_read: "allow",
    cap_broadcast: "allow",
    cap_service_response: "allow",
    cap_automation_write: "allow",
    cap_script_write: "allow",
    cap_physical_control: "confirm",
    cap_restart: "allow",
  },
  custom: null,
};
