import React from "react";
import type { GlobalSettings } from "../types";

interface Props {
  settings: GlobalSettings;
  onToggle: (key: keyof GlobalSettings, value: boolean) => void;
  saving: boolean;
}

export function NotificationSettings({ settings, onToggle, saving }: Props) {
  return (
    <div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>Notify on approval request</span>
          <small>
            Sends a HA persistent notification when a token requests an action that needs admin approval. The in-panel Approvals count updates either way.
          </small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <input
            type="checkbox"
            checked={settings.notify_on_approval}
            disabled={saving}
            onChange={(e) => onToggle("notify_on_approval", e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>Notify on rate limit breach</span>
          <small>
            Sends a HA persistent notification when any token hits its rate limit. Throttled to one notification per token per minute.
          </small>
        </div>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <input
            type="checkbox"
            checked={settings.notify_on_rate_limit}
            disabled={saving}
            onChange={(e) => onToggle("notify_on_rate_limit", e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
    </div>
  );
}
