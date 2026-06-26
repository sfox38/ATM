import React, { useState, useEffect, useCallback, useRef } from "react";
import type { TokenRecord, PatchTokenBody } from "../types";
import ATM_ICON from "../../custom_components/atm/brand/icon.png";
import { api } from "../api";
import { Loading, ErrorMsg } from "../index";
import { formatDateTime, tokenStatus } from "../utils";
import { Modal } from "../components/Modal";
import { RawTokenDisplay } from "../components/TokenCreateModal";
import { ConnectInstructions } from "../components/ConnectInstructions";
import { CapabilityMatrix, CAP_NAMES } from "../components/CapabilityMatrix";
import { PersonaPicker } from "../components/PersonaPicker";
import { PERSONAS } from "../personas";
import { RateLimitConfig } from "../components/RateLimitConfig";
import { CollapsibleCard } from "../components/CollapsibleCard";
import { PassThroughNotice } from "../components/PassThroughNotice";
import { EntityTree } from "../components/EntityTree";
import { PermissionSummary } from "../components/PermissionSummary";
import { PermissionSimulator } from "../components/PermissionSimulator";
import { SelectByPicker } from "../components/SelectByPicker";
import { ProfileEditor } from "./MesaView";

// Whether a token change alters which tools the MCP client is shown (tools/list),
// which is the only kind of change that requires a connected agent to reconnect.
// A capability only changes the announced set when it crosses the deny boundary: a
// cap-tied tool is announced whenever its cap is not "deny", so allow<->confirm
// (which only changes per-request gating, enforced live) does NOT need a reconnect.
// pass_through and announce_all_tools change the announced set wholesale. Persona
// changes surface here as capability changes.
function toolGatingChanged(a: TokenRecord, b: TokenRecord): boolean {
  const capCrossedDeny = CAP_NAMES.some((c) => (a[c] === "deny") !== (b[c] === "deny"));
  return capCrossedDeny || a.pass_through !== b.pass_through || a.announce_all_tools !== b.announce_all_tools;
}

interface Props {
  tokenId: string;
  onBack: () => void;
  onRefresh?: () => void;
}


interface ConfirmModalProps {
  title: string;
  body: React.ReactNode;
  checkLabel: string;
  confirmLabel: string;
  confirmClass: string;
  loading: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

function ConfirmModal({ title, body, checkLabel, confirmLabel, confirmClass, loading, onConfirm, onClose }: ConfirmModalProps) {
  const [checked, setChecked] = useState(false);
  const titleId = `confirm-modal-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <Modal titleId={titleId} onClose={loading ? undefined : onClose}>
      <h3 className="modal-title" id={titleId}>{title}</h3>
      {body}
      <div className="toggle-row mt-12" style={{ borderTop: "1px solid var(--atm-border)", paddingTop: 12 }}>
        <div className="toggle-label"><span>{checkLabel}</span></div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="modal-actions">
        <button className={`btn ${confirmClass}`} onClick={onConfirm} disabled={!checked || loading}>
          {loading ? "Please wait..." : confirmLabel}
        </button>
        <button className="btn btn-text" onClick={onClose} disabled={loading}>Cancel</button>
      </div>
    </Modal>
  );
}

function RotatedTokenModal({ rawToken, tokenName, onClose }: { rawToken: string; tokenName: string; onClose: () => void }) {
  const [closeEnabled, setCloseEnabled] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => setCloseEnabled(true), 3000);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  return (
    <Modal titleId="rotated-token-title" onClose={closeEnabled ? onClose : undefined}>
      <h3 className="modal-title" id="rotated-token-title">Token Rotated: {tokenName}</h3>
      <RawTokenDisplay
        rawToken={rawToken}
        note={<p><strong>The old token value is now invalid.</strong> Copy the new token before closing; it will not be shown again.</p>}
      />
      <div className="banner banner-info">
        The old token no longer works. Update this new token in your agent's MCP server config so it keeps working.
      </div>
      <details className="connect-details">
        <summary>Help me connect this token to an agent</summary>
        <ConnectInstructions token={rawToken} />
      </details>
      <div className="modal-actions">
        <button
          className="btn btn-text"
          onClick={onClose}
          disabled={!closeEnabled}
          title={closeEnabled ? undefined : "Wait 3 seconds before closing"}
        >
          {closeEnabled ? "Close" : "Close (3s)"}
        </button>
      </div>
    </Modal>
  );
}

function ToolAnnouncementToggle({ token, onUpdate }: { token: TokenRecord; onUpdate: (t: TokenRecord) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function patch(body: PatchTokenBody) {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.patchToken(token.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to update token.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <div className="toggle-row">
        <div className="toggle-label">
          <span>Always announce all tools</span>
          <small>By default the agent is only shown the tools this token can actually use (its capabilities and write access), which limits token spend and keeps the agent from attempting actions it cannot perform. Enable this to advertise the full tool set, for example while troubleshooting.</small>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={!!token.announce_all_tools}
            disabled={saving}
            onChange={(e) => patch({ announce_all_tools: e.target.checked })}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
      <div className="toggle-row">
        <div className="toggle-label">
          <span>Limit to Assist-exposed entities</span>
          <small>Only applies to pass-through tokens. A pass-through token normally sees every entity in Home Assistant; turn this on to narrow it to only the entities you have exposed to Home Assistant Assist. Scoped tokens use the permission tree instead, so this is disabled unless pass-through mode is enabled.</small>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={!!token.use_assist_exposure}
            disabled={saving || !token.pass_through}
            onChange={(e) => patch({ use_assist_exposure: e.target.checked })}
          />
          <span className="toggle-switch-track" />
        </label>
      </div>
    </>
  );
}

// Inline-editable token name shown as the detail heading. Click to select/edit;
// it auto-saves on blur (and on Enter), validating format client-side and letting
// the server reject a name that clashes with another token. Escape cancels.
const TOKEN_NAME_RE = /^[A-Za-z0-9_-]{3,32}$/;

function EditableTokenName({ token, onRenamed }: { token: TokenRecord; onRenamed: (t: TokenRecord) => void }) {
  const [value, setValue] = useState(token.name);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { setValue(token.name); setErr(null); }, [token.name]);

  async function commit() {
    const next = value.trim();
    if (next === token.name) { setValue(token.name); setErr(null); return; }
    if (!TOKEN_NAME_RE.test(next)) {
      setErr("3-32 characters: letters, numbers, hyphens, or underscores.");
      setValue(token.name);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.patchToken(token.id, { name: next });
      onRenamed(updated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Rename failed.");
      setValue(token.name);
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="token-name-edit">
      <input
        className="token-card-name token-name-input"
        value={value}
        disabled={saving}
        spellCheck={false}
        maxLength={32}
        aria-label="Token name (editable)"
        title="Rename this token; saves when you click away"
        onChange={(e) => setValue(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
          else if (e.key === "Escape") { setValue(token.name); setErr(null); (e.target as HTMLInputElement).blur(); }
        }}
      />
      {err && <span className="token-name-error" role="alert">{err}</span>}
    </span>
  );
}

export function TokenDetailView({ tokenId, onBack, onRefresh }: Props) {
  const [token, setToken] = useState<TokenRecord | null>(null);
  const [mesaProfileEntities, setMesaProfileEntities] = useState<Set<string>>(new Set());
  // The entity whose MESA profile is being edited inline (overlaid on this tab),
  // and the canonical tag vocabulary the editor needs.
  const [mesaEdit, setMesaEdit] = useState<{ entityId: string; isNew: boolean } | null>(null);
  const [canonicalTags, setCanonicalTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [showRevokeModal, setShowRevokeModal] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [rotatedRawToken, setRotatedRawToken] = useState<string | null>(null);
  const [showSelectByPicker, setShowSelectByPicker] = useState(false);
  const [showClearPerms, setShowClearPerms] = useState(false);
  const [clearingPerms, setClearingPerms] = useState(false);
  const [entityTree, setEntityTree] = useState<import("../types").EntityTree | null>(null);
  const [ptToggling, setPtToggling] = useState(false);
  const [showPtModal, setShowPtModal] = useState(false);
  // Set when a change alters the announced tool list, so we can remind the operator
  // to reconnect the agent. Only surfaced when the token has actually been used.
  const [reconnectNeeded, setReconnectNeeded] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState("");
  const [selectedDepth, setSelectedDepth] = useState<"entity" | "device" | "domain">("entity");
  // Bumped on every reveal request so clicking the same row re-triggers the
  // tree expand/scroll (selecting the same id/depth alone is a no-op in React).
  const [revealNonce, setRevealNonce] = useState(0);
  const revealNode = (eid: string, depth: "entity" | "device" | "domain" = "entity") => {
    setSelectedEntityId(eid);
    setSelectedDepth(depth);
    setRevealNonce((n) => n + 1);
  };
  const [permissionsVersion, setPermissionsVersion] = useState(0);
  const [collapseTreeKey, setCollapseTreeKey] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getToken(tokenId);
      setToken(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load token.");
    } finally {
      setLoading(false);
    }
  }, [tokenId]);

  useEffect(() => { load(); }, [load]);

  // setToken wrapper for the config editors (persona, capabilities, tool
  // announcement, pass-through). If the change alters the announced tool list and
  // the token has been used by a client, raise the reconnect reminder.
  const applyTokenUpdate = useCallback((updated: TokenRecord) => {
    if (token && updated.last_used_at && toolGatingChanged(token, updated)) {
      setReconnectNeeded(true);
    }
    setToken(updated);
  }, [token]);

  useEffect(() => {
    api.getEntityTree().then(setEntityTree).catch(() => null);
  }, []);

  // Which entities already have a MESA profile, so the cards can show
  // "view" (MESA) vs "create" (+). Reloaded after the inline editor saves so a
  // newly created profile flips its "+" to "MESA" without leaving the tab.
  const loadMesaProfiles = useCallback(() => {
    api.listMesaProfiles({ limit: 500 })
      .then((r) => setMesaProfileEntities(new Set(r.profiles.map((p) => p.entity_id))))
      .catch(() => null);
  }, []);
  useEffect(() => { loadMesaProfiles(); }, [loadMesaProfiles]);

  // The canonical MESA tag vocabulary powers the inline editor's tag autocomplete.
  useEffect(() => { api.getMesaVocabulary().then((v) => setCanonicalTags(v.canonical_tags)).catch(() => null); }, []);

  // Open the MESA profile editor as an overlay on this tab (no tab switch). isNew
  // mirrors the +/MESA affordance the user clicked (driven by the same set).
  const openMesaInline = useCallback((entityId: string) => {
    setMesaEdit({ entityId, isNew: !mesaProfileEntities.has(entityId) });
  }, [mesaProfileEntities]);

  async function revoke() {
    setRevoking(true);
    try {
      await api.revokeToken(tokenId);
      onBack();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to revoke token.");
      setRevoking(false);
      setShowRevokeModal(false);
    }
  }

  async function rotate() {
    setRotating(true);
    try {
      const resp = await api.rotateToken(tokenId);
      const { token: rawToken } = resp as { token: string };
      setRotatedRawToken(rawToken);
      setShowRotateModal(false);
      onRefresh?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to rotate token.");
      setShowRotateModal(false);
    } finally {
      setRotating(false);
    }
  }

  async function clearPermissions() {
    setClearingPerms(true);
    try {
      const updatedTree = await api.setPermissions(tokenId, { domains: {}, devices: {}, entities: {} });
      setToken((t) => t ? { ...t, permissions: updatedTree } : t);
      setPermissionsVersion((v) => v + 1);
      setCollapseTreeKey((k) => k + 1);
      setShowClearPerms(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to clear permissions.");
    } finally {
      setClearingPerms(false);
    }
  }

  async function enablePassThrough() {
    setPtToggling(true);
    try {
      const body: PatchTokenBody = { pass_through: true, confirm_pass_through: true };
      const updated = await api.patchToken(tokenId, body);
      applyTokenUpdate(updated);
      setShowPtModal(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to enable pass-through.");
      setShowPtModal(false);
    } finally {
      setPtToggling(false);
    }
  }

  if (loading) return <Loading />;
  if (error && !token) return <div><button className="btn btn-text" onClick={onBack}>Back</button><ErrorMsg msg={error} /></div>;
  if (!token) return null;

  if (rotatedRawToken) {
    return <RotatedTokenModal rawToken={rotatedRawToken} tokenName={token.name} onClose={() => setRotatedRawToken(null)} />;
  }

  const status = tokenStatus(token);
  const statusClass = status === "Active" ? "badge-green" : status === "Expired" ? "badge-grey" : "badge-red";

  // One-line summaries shown on the collapsed Advanced cards.
  const personaLabel = PERSONAS.find((p) => p.key === token.persona)?.label ?? "Custom";
  const capCounts = CAP_NAMES.reduce(
    (acc, k) => { acc[token[k]] = (acc[token[k]] ?? 0) + 1; return acc; },
    { allow: 0, confirm: 0, deny: 0 } as Record<string, number>,
  );
  const capsSummary = `${personaLabel} · ${capCounts.allow} allow / ${capCounts.confirm} confirm / ${capCounts.deny} deny`;
  const announceSummary = token.announce_all_tools ? "All tools announced" : "Scoped to capabilities";
  const rateSummary = token.rate_limit_requests > 0
    ? `${token.rate_limit_requests}/min · burst ${token.rate_limit_burst}`
    : "No limit";

  return (
    <div className="token-detail-wrap">

      {/* Modals */}
      {showRotateModal && (
        <ConfirmModal
          title="Rotate Token"
          body={
            <div className="amber-block">
              <p>Rotating will <strong>immediately invalidate the current token value.</strong> A new token value will be generated and shown once. Any clients using the old value will be rejected immediately.</p>
            </div>
          }
          checkLabel="I understand the current token value will be invalidated immediately"
          confirmLabel="Rotate Token"
          confirmClass="btn-primary"
          loading={rotating}
          onConfirm={rotate}
          onClose={() => setShowRotateModal(false)}
        />
      )}

      {showRevokeModal && (
        <ConfirmModal
          title="Revoke Token"
          body={
            <div className="amber-block">
              <p>Revoking <strong>permanently deactivates this token.</strong> It cannot be re-enabled. All clients using this token will lose access immediately.</p>
            </div>
          }
          checkLabel="I understand this token will be permanently deactivated"
          confirmLabel="Revoke Token"
          confirmClass="btn-danger"
          loading={revoking}
          onConfirm={revoke}
          onClose={() => setShowRevokeModal(false)}
        />
      )}

      {showPtModal && (
        <ConfirmModal
          title="Enable Pass-Through Mode"
          body={
            <div className="amber-block">
              <p><strong>Pass-through gives this token full unrestricted access</strong> to every entity, service, and system operation in Home Assistant. It is equivalent to a Long-Lived Access Token. The ATM domain blocklist and sensitive attribute scrubbing still apply.</p>
            </div>
          }
          checkLabel="I understand this token will have full Home Assistant access"
          confirmLabel="Enable Pass-Through"
          confirmClass="btn-warning"
          loading={ptToggling}
          onConfirm={enablePassThrough}
          onClose={() => setShowPtModal(false)}
        />
      )}

      {/* Sticky top section */}
      <div className="token-detail-sticky">
        {error && <ErrorMsg msg={error} />}

        {reconnectNeeded && (
          <div className="banner banner-info reconnect-banner" role="status">
            <span className="reconnect-banner-text">
              This token's capabilities have changed. <strong>You must reconnect your AI agent to ATM</strong> in order to use the current tools.
            </span>
            <button
              className="reconnect-banner-dismiss"
              onClick={() => setReconnectNeeded(false)}
              aria-label="Dismiss reconnect reminder"
            >&times;</button>
          </div>
        )}

        {token.pass_through && (
          <div className="pass-through-header-banner">
            <p>
              <strong className="text-warning">This is a Pass Through token.</strong> It bypasses the permission tree and has unrestricted access to Home Assistant entities and services. Sensitive attributes are still scrubbed, and the exempt capabilities still apply (write, system, and irreversible caps, plus log reading, stay enforced as set). The ATM domain is always blocked regardless of token configuration.
            </p>
          </div>
        )}

        <div className="two-col">
          {/* Left: Token info card */}
          <div className="card token-info-card">
            <div className="token-card-header">
              <div className="token-card-name-row">
                <img src={ATM_ICON} className="token-card-icon" alt="" />
                <EditableTokenName token={token} onRenamed={(u) => { applyTokenUpdate(u); onRefresh?.(); }} />
              </div>
              <div className="token-card-badges">
                <span className={`badge ${statusClass}`}>{status}</span>
                {token.pass_through
                  ? <span className="badge badge-amber">Pass Through</span>
                  : <span className="badge badge-blue">Scoped</span>}
              </div>
            </div>

            <div className="token-card-body">
              <div className="token-card-meta">
                <div className="token-meta-table">
                  <span className="stat-label">Created</span>
                  <span title={token.created_at ? new Date(token.created_at).toLocaleString() : undefined}>{formatDateTime(token.created_at)}</span>
                  <span className="stat-label">Last Updated</span>
                  <span title={token.updated_at ? new Date(token.updated_at).toLocaleString() : undefined}>{formatDateTime(token.updated_at)}</span>
                  <span className="stat-label">Expires</span>
                  <span>{formatDateTime(token.expires_at)}</span>
                  <span className="stat-label">Last Used</span>
                  <span>{formatDateTime(token.last_used_at)}</span>
                </div>
              </div>

              <div className="token-card-actions">
                <button className="btn btn-outline btn-sm token-action-btn" onClick={() => setShowRotateModal(true)}>
                  Rotate
                </button>
                {!token.pass_through && (
                  <button className="btn btn-warning btn-sm token-action-btn" onClick={() => setShowPtModal(true)}>
                    Enable Pass-Through
                  </button>
                )}
                <button className="btn btn-danger btn-sm token-action-btn" onClick={() => setShowRevokeModal(true)}>
                  Revoke
                </button>
              </div>
            </div>
          </div>

          {/* Right: Permission emulator */}
          <div className="card epe-card">
            <div className="card-header">Effective Permission Emulator</div>
            {token.pass_through ? (
              <p style={{ fontSize: 13, color: "var(--atm-text-2)", margin: 0 }}>
                Pass Through tokens have unrestricted access to all non-ATM entities. No simulation needed.
              </p>
            ) : (
              <PermissionSimulator
                tokenId={tokenId}
                externalEntityId={selectedEntityId || undefined}
                resolveDepth={selectedDepth}
                triggerVersion={permissionsVersion}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={openMesaInline}
              />
            )}
          </div>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="token-detail-body">
        <div className="two-col">
          <div>
            <CollapsibleCard title="Persona" summary={personaLabel} defaultOpen persistKey="atm:fold:persona">
              <PersonaPicker token={token} onUpdate={applyTokenUpdate} />
            </CollapsibleCard>

            <div className="advanced-section-label">Advanced</div>
            <CollapsibleCard title="Capabilities" summary={capsSummary} persistKey="atm:fold:capabilities">
              <CapabilityMatrix token={token} onUpdate={applyTokenUpdate} />
            </CollapsibleCard>
            <CollapsibleCard title="Tool Announcement" summary={announceSummary} persistKey="atm:fold:announce">
              <ToolAnnouncementToggle token={token} onUpdate={applyTokenUpdate} />
            </CollapsibleCard>
            <CollapsibleCard title="Rate Limiting" summary={rateSummary} persistKey="atm:fold:ratelimit">
              <RateLimitConfig token={token} onUpdate={setToken} />
            </CollapsibleCard>

            {!token.pass_through && (
              <div className="card">
                <div className="card-header">Permission Summary</div>
                <PermissionSummary
                  permissions={token.permissions}
                  entityTree={entityTree}
                  onEntityClick={revealNode}
                  mesaProfileEntities={mesaProfileEntities}
                  onOpenMesa={openMesaInline}
                />
              </div>
            )}
          </div>

          <div>
            {token.pass_through ? (
              <div className="card">
                <div className="card-header">Permissions Tree</div>
                <PassThroughNotice token={token} onUpdate={applyTokenUpdate} />
              </div>
            ) : (
              <div className="card">
                <div className="card-header">
                  <span>Permissions Tree</span>
                  <div className="tree-header-actions">
                    {entityTree && (
                      <button className="btn btn-outline btn-sm" onClick={() => setShowSelectByPicker(true)}>
                        Select by Area or Label
                      </button>
                    )}
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => setShowClearPerms(true)}
                    >
                      Clear All
                    </button>
                  </div>
                </div>
                <EntityTree
                  tokenId={tokenId}
                  permissions={token.permissions}
                  onPermissionsChange={(tree) => {
                    setToken({ ...token, permissions: tree });
                    setPermissionsVersion((v) => v + 1);
                  }}
                  onEntityClick={revealNode}
                  collapseKey={collapseTreeKey}
                  revealEntity={selectedEntityId || undefined}
                  revealDepth={selectedDepth}
                  revealNonce={revealNonce}
                  mesaProfileEntities={mesaProfileEntities}
                  onOpenMesa={openMesaInline}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {showSelectByPicker && entityTree && (
        <SelectByPicker
          tokenId={tokenId}
          entityTree={entityTree}
          onDone={() => {
            setShowSelectByPicker(false);
            load();
          }}
          onClose={() => setShowSelectByPicker(false)}
        />
      )}

      {showClearPerms && (
        <Modal titleId="clear-perms-title" onClose={clearingPerms ? undefined : () => setShowClearPerms(false)}>
          <h3 className="modal-title" id="clear-perms-title">Clear all permissions?</h3>
          <p className="clear-perms-body">
            This will reset every domain, device, and entity permission to [N] (no explicit grant). The token will have no access to any entities until new permissions are assigned.
          </p>
          <div className="modal-actions">
            <button className="btn btn-danger" onClick={clearPermissions} disabled={clearingPerms}>
              {clearingPerms ? "Clearing..." : "Clear All"}
            </button>
            <button className="btn btn-text" onClick={() => setShowClearPerms(false)} disabled={clearingPerms}>
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {mesaEdit && (
        <ProfileEditor
          scope="entity"
          profileKey={mesaEdit.entityId}
          isNew={mesaEdit.isNew}
          entityTree={entityTree}
          canonicalTags={canonicalTags}
          onClose={() => setMesaEdit(null)}
          onSaved={loadMesaProfiles}
        />
      )}
    </div>
  );
}
