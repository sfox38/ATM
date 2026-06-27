import React, { useEffect, useState } from "react";
import type { GlobalSettings } from "../types";
import { api } from "../api";
import { LoggingSettings } from "../components/LoggingSettings";
import { NotificationSettings } from "../components/NotificationSettings";
import { KillSwitch } from "../components/KillSwitch";
import { WipeConfirmModal } from "../components/WipeConfirmModal";
import { Loading } from "../index";
import { JS_BUILD } from "../version";

type Theme = "light" | "dark" | "auto";

interface Props {
  settings: GlobalSettings | null;
  onSettingsChange: (s: GlobalSettings) => void;
  theme: Theme;
  onThemeChange: (t: Theme) => void;
}

export function SettingsView({ settings, onSettingsChange, theme, onThemeChange }: Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showWipe, setShowWipe] = useState(false);
  const [atmVersion, setAtmVersion] = useState<string | null>(null);
  const [minHaVersion, setMinHaVersion] = useState<string | null>(null);
  const [githubUrl, setGithubUrl] = useState<string | null>(null);

  useEffect(() => {
    api.getInfo().then((info) => {
      setAtmVersion(info.version);
      setMinHaVersion(info.min_ha_version);
      setGithubUrl(info.github_url);
    }).catch(() => {});
  }, []);

  async function patchSetting(key: keyof GlobalSettings, value: boolean | number | string) {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.patchSettings({ [key]: value });
      onSettingsChange(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save setting.");
    } finally {
      setSaving(false);
    }
  }

  function handleWiped() {
    setShowWipe(false);
    window.location.reload();
  }

  if (!settings) return <Loading />;

  return (
    <div className="view-root">
      {error && <div className="banner banner-error">{error}</div>}

      <div className="settings-grid">
        {/* Left column: Logging */}
        <div>
          <div className="card">
            <div className="card-header">Logging</div>
            <LoggingSettings
              settings={settings}
              onToggle={patchSetting}
              saving={saving}
            />
            <hr className="settings-divider" />
            <div className={`toggle-row settings-toggle-mt${settings.disable_all_logging ? " toggle-row-greyed" : ""}`}>
              <div className="toggle-label">
                <span>Audit log flush interval</span>
                <small>How often to snapshot the audit log to disk. "Never" keeps the log in-memory only.</small>
              </div>
              <select
                className="input input-auto"
                value={settings.audit_flush_interval}
                disabled={saving || settings.disable_all_logging}
                onChange={(e) => patchSetting("audit_flush_interval", Number(e.target.value))}
              >
                <option value={0}>Never</option>
                <option value={5}>Every 5 minutes</option>
                <option value={10}>Every 10 minutes</option>
                <option value={15}>Every 15 minutes</option>
                <option value={30}>Every 30 minutes</option>
                <option value={60}>Every 60 minutes</option>
              </select>
            </div>
            <div className={`toggle-row${settings.disable_all_logging ? " toggle-row-greyed" : ""}`}>
              <div className="toggle-label">
                <span>Maximum log entries</span>
                <small>Capacity of the in-memory buffer and the on-disk snapshot. Reducing this trims the oldest entries immediately.</small>
              </div>
              <select
                className="input input-auto"
                value={settings.audit_log_maxlen}
                disabled={saving || settings.disable_all_logging}
                onChange={(e) => patchSetting("audit_log_maxlen", Number(e.target.value))}
              >
                <option value={100}>100</option>
                <option value={1000}>1,000</option>
                <option value={5000}>5,000</option>
                <option value={10000}>10,000</option>
              </select>
            </div>
          </div>
        </div>

        {/* Right column: Kill Switch, Notifications, Info, Data Management */}
        <div>
          <div className="card">
            <div className="card-header">Emergency Kill Switch</div>
            <KillSwitch
              settings={settings}
              onToggle={(value) => patchSetting("kill_switch", value)}
              saving={saving}
            />
          </div>

          <div className="card">
            <div className="card-header">Notifications</div>
            <NotificationSettings
              settings={settings}
              onToggle={patchSetting}
              saving={saving}
            />
          </div>

          <div className="card">
            <div className="card-header">MESA Semantic Safety</div>
            <div className="toggle-row">
              <div className="toggle-label">
                <span>Enforcement mode</span>
                <small>
                  MESA describes each entity's control mode, automation impact, and privacy.
                  Advisory surfaces warnings only; Enforced blocks unsafe calls and routes
                  confirm-mode entities through the Approvals queue.
                </small>
              </div>
              <select
                className="input input-auto"
                value={settings.mesa_mode}
                disabled={saving}
                onChange={(e) => patchSetting("mesa_mode", e.target.value)}
              >
                <option value="off">Off</option>
                <option value="advisory">Advisory</option>
                <option value="enforced">Enforced</option>
              </select>
            </div>
            {settings.mesa_mode === "enforced" && (
              <div className="banner banner-warn settings-toggle-mt">
                In Enforced mode, entities with no MESA profile fall back to the built-in
                domain safety baseline: locks and alarm panels are blocked, and most other
                domains require confirmation. Author profiles or set deployment defaults
                before relying on Enforced mode.
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-header">Experimental</div>
            <div className="toggle-row toggle-row-plain">
              <div className="toggle-label">
                <span>In-context profile buttons</span>
                <small>
                  Adds a (+) / MESA control to the rows of Home Assistant's native
                  Automations, Scripts, Helpers, and People pages (and integration detail
                  pages) so you can create a MESA entity profile without leaving the page.
                  This patches the HA frontend, so the buttons may stop appearing after a
                  Home Assistant update until ATM is updated to match; it never affects HA
                  itself. Reload Home Assistant after changing this. Admin only.
                </small>
              </div>
              <label className={`toggle-switch${saving ? " disabled" : ""}`}>
                <input
                  type="checkbox"
                  checked={settings.mesa_inject_enabled}
                  disabled={saving}
                  onChange={(e) => patchSetting("mesa_inject_enabled", e.target.checked)}
                />
                <span className="toggle-switch-track" />
              </label>
            </div>
          </div>

          <div className="card">
            <div className="card-header">Integration Info</div>
            <div className="settings-info-list">
              <div><strong>ATM Version:</strong> {atmVersion ?? "..."}</div>
              <div><strong>JS Build:</strong> {JS_BUILD}</div>
              <div><strong>Minimum HA Version:</strong> {minHaVersion ?? "..."}</div>
              <div>
                <a href={githubUrl ?? "#"} target="_blank" rel="noopener noreferrer"
                  className="settings-info-link">
                  GitHub Repository
                </a>
              </div>
              <div className="settings-info-note">
                ATM configuration is stored in <code>.storage/atm.json</code> and is included in all HA full backups and partial backups of the <code>.storage</code> directory.
              </div>
              <div className="toggle-row" style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--atm-border)" }}>
                <div className="toggle-label">
                  <span>Theme</span>
                  <small>Light, dark, or follow system preference.</small>
                </div>
                <div className="theme-toggle">
                  {(["light", "auto", "dark"] as Theme[]).map((t) => (
                    <button
                      key={t}
                      className={`theme-toggle-btn${theme === t ? " active" : ""}`}
                      onClick={() => onThemeChange(t)}
                    >
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header settings-danger-header">
              Data Management
            </div>
            <p className="clear-perms-body">
              Permanently deletes all active tokens, archived records, permission trees, capability flags, settings, the in-memory audit log, and the on-disk audit log snapshot. All tokens are immediately invalidated.
            </p>
            <button
              className="btn btn-danger"
              onClick={() => setShowWipe(true)}
            >
              Wipe All ATM Data
            </button>
          </div>
        </div>
      </div>

      {showWipe && (
        <WipeConfirmModal
          onWiped={handleWiped}
          onClose={() => setShowWipe(false)}
        />
      )}
    </div>
  );
}
