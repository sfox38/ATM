import React, { useState, useEffect, useCallback, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { TokenRecord, GlobalSettings } from "./types";
import { TokenListView } from "./views/TokenList";
import { TokenDetailView } from "./views/TokenDetail";
import { AuditView } from "./views/AuditView";
import { SettingsView } from "./views/SettingsView";
import { ApprovalsView } from "./views/ApprovalsView";
import { MesaView } from "./views/MesaView";
import { OnboardingWizard } from "./views/OnboardingWizard";
import { api, setHass } from "./api";
import PANEL_CSS from "./atm-panel.css?inline";

type Tab = "tokens" | "approvals" | "mesa" | "audit" | "settings";
type Theme = "light" | "dark" | "auto";

export { HIGH_RISK_DOMAINS } from "./utils";

function Loading() {
  return (
    <div className="loading-wrap">
      <div className="spinner" />
      <span>Loading...</span>
    </div>
  );
}

function ErrorMsg({ msg }: { msg: string }) {
  return <div className="banner banner-error">{msg}</div>;
}

export { Loading, ErrorMsg };

function RefreshIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ display: "block" }}>
      <polyline points="1 4 1 10 7 10" />
      <polyline points="23 20 23 14 17 14" />
      <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15" />
    </svg>
  );
}

export { RefreshIcon };

type View =
  | { name: "list" }
  | { name: "detail"; tokenId: string }
  | { name: "wizard" };

const TAB_LABELS: Record<Tab, string> = { tokens: "Tokens", approvals: "Approvals", mesa: "MESA", audit: "Audit Logs", settings: "Settings" };

function ATMApp({ hass, narrow, theme, onThemeChange }: { hass: unknown; narrow: boolean; theme: Theme; onThemeChange: (t: Theme) => void }) {
  const [tab, setTab] = useState<Tab>("tokens");
  const [view, setView] = useState<View>({ name: "list" });
  const [tokens, setTokens] = useState<TokenRecord[]>([]);
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [loadingTokens, setLoadingTokens] = useState(true);
  const [tokensError, setTokensError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [pendingCount, setPendingCount] = useState<number>(0);
  const [deepApprovalId, setDeepApprovalId] = useState<string | null>(null);
  const [mesaProfileTarget, setMesaProfileTarget] = useState<string | null>(null);
  const menuRef = useRef<HTMLElement | null>(null);

  // Jump from a token card to an entity's MESA profile (the MESA tab opens it,
  // creating a prefilled draft if none exists).
  const openMesaProfile = useCallback((entityId: string) => {
    setTab("mesa");
    setView({ name: "list" });
    setMesaProfileTarget(entityId);
  }, []);

  // Deep-link from a notification: /atm#approvals or /atm#approvals/{id} opens
  // the Approvals tab (and that specific approval's popup).
  useEffect(() => {
    function handleHash() {
      const m = window.location.hash.replace(/^#/, "").match(/^approvals(?:\/(.+))?$/);
      if (!m) return;
      setTab("approvals");
      setView({ name: "list" });
      if (m[1]) setDeepApprovalId(decodeURIComponent(m[1]));
    }
    handleHash();
    window.addEventListener("hashchange", handleHash);
    return () => window.removeEventListener("hashchange", handleHash);
  }, []);

  useEffect(() => {
    if (menuRef.current) {
      (menuRef.current as unknown as Record<string, unknown>).hass = hass;
      (menuRef.current as unknown as Record<string, unknown>).narrow = narrow;
    }
  }, [hass, narrow]);

  const refreshTokens = useCallback(async () => {
    setLoadingTokens(true);
    setTokensError(null);
    try {
      const data = await api.listTokens();
      setTokens(data);
    } catch (e: unknown) {
      setTokensError(e instanceof Error ? e.message : "Failed to load tokens.");
    } finally {
      setLoadingTokens(false);
    }
  }, []);

  useEffect(() => {
    refreshTokens();
    api.getSettings().then(setSettings).catch(() => null);
  }, [refreshTokens]);

  const refreshPendingCount = useCallback(async () => {
    try {
      const resp = await api.listApprovals({ status: "pending", limit: 1 });
      setPendingCount(resp.total);
    } catch {
      // Silent failure: badge just won't update. Don't surface in UI.
    }
  }, []);

  useEffect(() => {
    refreshPendingCount();
    // Poll briskly so the count appears within a few seconds of a request and
    // clears promptly after the admin resolves it.
    const id = setInterval(refreshPendingCount, 5_000);
    return () => clearInterval(id);
  }, [refreshPendingCount]);

  const openDetail = useCallback((id: string) => {
    setView({ name: "detail", tokenId: id });
    setTab("tokens");
  }, []);

  const openWizard = useCallback(() => {
    setTab("tokens");
    setView({ name: "wizard" });
  }, []);

  const goBack = useCallback(() => {
    setView({ name: "list" });
    refreshTokens();
  }, [refreshTokens]);

  const onTabClick = useCallback((t: Tab) => {
    setTab(t);
    setView({ name: "list" });
    if (t === "tokens") refreshTokens();
  }, [refreshTokens]);

  const TABS: Tab[] = ["tokens", "approvals", "mesa", "audit", "settings"];

  function handleTabKeyDown(e: React.KeyboardEvent) {
    const idx = TABS.indexOf(tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const next = e.key === "ArrowRight"
        ? TABS[(idx + 1) % TABS.length]
        : TABS[(idx - 1 + TABS.length) % TABS.length];
      onTabClick(next);
    }
  }

  return (
    <div className="atm-shell">
      <h1 className="sr-only">ATM Token Management</h1>
      {narrow && (
        <header className="atm-header">
          <ha-menu-button ref={menuRef as React.RefObject<HTMLElement>} />
          <span className="atm-header-title">ATM</span>
        </header>
      )}

      <nav className="atm-tabs" aria-label="ATM sections">
        <div role="tablist" aria-label="ATM sections" onKeyDown={handleTabKeyDown} style={{ display: "contents" }}>
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              id={`atm-tab-${t}`}
              aria-selected={tab === t}
              aria-controls={`atm-tabpanel-${t}`}
              tabIndex={tab === t ? 0 : -1}
              className={`atm-tab${tab === t ? " active" : ""}`}
              onClick={() => onTabClick(t)}
              aria-label={t === "approvals" && pendingCount > 0
                ? `Approvals (${pendingCount} pending)`
                : undefined}
            >
              {TAB_LABELS[t]}
              {t === "approvals" && pendingCount > 0 && (
                <span className="atm-tab-badge" aria-hidden="true">{pendingCount}</span>
              )}
            </button>
          ))}
        </div>

        <div className="atm-tab-spacer" />

        <div className="atm-header-actions">
          <button className="btn btn-primary btn-sm btn-header-create" onClick={() => { setTab("tokens"); setView({ name: "list" }); setShowCreate(true); }}>
            Create Token
          </button>
        </div>
      </nav>

      <main
        className="atm-content"
        id={`atm-tabpanel-${tab}`}
        role="tabpanel"
        aria-labelledby={`atm-tab-${tab}`}
      >
        <h2 className="sr-only">{TAB_LABELS[tab]}</h2>
        {tab === "tokens" && view.name === "list" && (
          <TokenListView
            tokens={tokens}
            loading={loadingTokens}
            error={tokensError}
            onRefresh={refreshTokens}
            onOpenDetail={openDetail}
            onLaunchWizard={openWizard}
            showCreate={showCreate}
            onCloseCreate={() => setShowCreate(false)}
          />
        )}
        {tab === "tokens" && view.name === "wizard" && (
          <OnboardingWizard onCancel={goBack} onFinish={openDetail} />
        )}
        {tab === "tokens" && view.name === "detail" && (
          <TokenDetailView
            tokenId={view.tokenId}
            onBack={goBack}
            onRefresh={refreshTokens}
            onOpenMesaProfile={openMesaProfile}
          />
        )}
        {tab === "approvals" && (
          <ApprovalsView
            onCountChange={refreshPendingCount}
            openApprovalId={deepApprovalId}
            onConsumedDeepLink={() => setDeepApprovalId(null)}
          />
        )}
        {tab === "mesa" && (
          <MesaView
            openProfileEntityId={mesaProfileTarget}
            onProfileOpened={() => setMesaProfileTarget(null)}
          />
        )}
        {tab === "audit" && <AuditView tokens={tokens} />}
        {tab === "settings" && (
          <SettingsView
            settings={settings}
            onSettingsChange={setSettings}
            theme={theme}
            onThemeChange={onThemeChange}
          />
        )}
      </main>
    </div>
  );
}

class ATMPanelElement extends HTMLElement {
  private _root: Root | null = null;
  private _hass: unknown = null;
  private _narrow: boolean = false;
  private _prevUserId: string | undefined = undefined;
  private _theme: Theme = "auto";

  connectedCallback() {
    this.style.touchAction = "pan-y";

    const saved = localStorage.getItem("atm-theme");
    if (saved === "light" || saved === "dark" || saved === "auto") {
      this._theme = saved;
    }
    this._applyThemeClass();

    const shadow = this.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = PANEL_CSS;
    shadow.appendChild(style);

    const mount = document.createElement("div");
    mount.style.height = "100%";
    shadow.appendChild(mount);

    this._root = createRoot(mount);
    this._render();
  }

  disconnectedCallback() {
    this._root?.unmount();
    this._root = null;
  }

  set hass(hass: unknown) {
    this._hass = hass;
    setHass(hass);
    const uid = (hass as Record<string, Record<string, string>> | null)?.user?.id;
    if (uid !== this._prevUserId) {
      this._prevUserId = uid;
      this._render();
    }
    if (this._theme === "auto") this._applyThemeClass();
  }

  set narrow(value: boolean) {
    if (this._narrow !== value) {
      this._narrow = value;
      this._render();
    }
  }

  private _applyThemeClass() {
    this.classList.remove("atm-theme-light", "atm-theme-dark");
    if (this._theme === "light") {
      this.classList.add("atm-theme-light");
    } else if (this._theme === "dark") {
      this.classList.add("atm-theme-dark");
    } else {
      // Auto: follow HA's dark mode preference when available
      const hassThemes = (this._hass as { themes?: { darkMode?: boolean } } | null)?.themes;
      if (hassThemes?.darkMode === true) {
        this.classList.add("atm-theme-dark");
      } else if (hassThemes?.darkMode === false) {
        this.classList.add("atm-theme-light");
      }
      // If darkMode is undefined, no class - CSS prefers-color-scheme handles it
    }
  }

  private _setTheme(t: Theme) {
    this._theme = t;
    localStorage.setItem("atm-theme", t);
    this._applyThemeClass();
    this._render();
  }

  private _render() {
    if (this._root && this._hass) {
      this._root.render(
        <ATMApp
          hass={this._hass}
          narrow={this._narrow}
          theme={this._theme}
          onThemeChange={(t) => this._setTheme(t)}
        />
      );
    }
  }
}

if (!customElements.get("atm-panel")) {
  customElements.define("atm-panel", ATMPanelElement);
}
