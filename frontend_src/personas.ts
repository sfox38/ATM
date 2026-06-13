// Persona presets shared by the PersonaPicker (Token Detail) and the onboarding
// wizard. PERSONA_CAP_DEFAULTS mirrors personas.py PERSONA_DEFINITIONS and is
// used to pre-fill cap values when applying a persona via PATCH so the matrix
// updates in one round-trip. If this drifts from the backend, the backend
// validates each value and the PATCH fails closed.
import type { Persona } from "./types";

export interface PersonaDef {
  key: Persona;
  label: string;
  description: string;
  // wizardOnly personas are shown only in the onboarding wizard's persona step,
  // not in the normal Token Detail persona picker.
  wizardOnly?: boolean;
}

export const PERSONAS: PersonaDef[] = [
  {
    key: "new_user",
    label: "New user (recommended)",
    description: "A safe starting point: the agent can read your home and control the devices you grant it. Locks, alarms, and covers ask for your confirmation. You can change this any time later.",
    wizardOnly: true,
  },
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

export const PERSONA_CAP_DEFAULTS: Record<Persona, Record<string, string> | null> = {
  new_user: {
    cap_config_read: "allow",
    cap_template_render: "allow",
    cap_log_read: "deny",
    cap_broadcast: "deny",
    cap_service_response: "allow",
    cap_automation_write: "deny",
    cap_script_write: "deny",
    cap_physical_control: "confirm",
    cap_restart: "deny",
  },
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
