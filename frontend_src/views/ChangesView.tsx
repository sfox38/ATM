/** Configuration version history view. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { VersionRecord, VersionSummary } from "../types";
import { api } from "../api";
import { YamlView, toYaml } from "../components/YamlView";
import { diffLines, RawDiffPane } from "../components/DiffView";
import { Loading, ErrorMsg, RefreshIcon } from "../index";
import { formatDateTime } from "../utils";

// Resource types whose before/after payload is a raw text blob ({content, ...})
// rather than a structured config; these render as a line diff, not YAML.
const RAW_CONTENT_TYPES = new Set(["yaml_config", "file"]);

/** Extract the raw-text snapshot from a version side, or null. content is null
 * when the snapshot was too large to store (a non-restorable marker). */
function rawSide(value: Record<string, unknown> | null): { content: string | null; bytes?: number } | null {
  if (value == null) return null;
  return {
    content: typeof value.content === "string" ? (value.content as string) : null,
    bytes: typeof value.bytes === "number" ? (value.bytes as number) : undefined,
  };
}

const ACTION_BADGE: Record<string, string> = {
  create: "badge-green",
  edit: "badge-blue",
  delete: "badge-red",
  rollback: "badge-amber",
};

function ActionBadge({ action }: { action: string }) {
  return <span className={`badge ${ACTION_BADGE[action] ?? "badge-grey"}`}>{action}</span>;
}

function label(v: { alias: string | null; resource_id: string }): string {
  return v.alias || v.resource_id;
}

// Author display name. Prefer the token's CURRENT name (it may have been renamed
// since the change was recorded), falling back to the name captured at the time,
// then to "admin" for admin-driven restores.
function whoNow(
  v: { token_id?: string | null; token_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.token_id ? current.get(v.token_id) : undefined;
  return cur || v.token_name || (v.approved_by_user_id ? "admin" : "-");
}

// Detail view: when the token has since been renamed, show "current (original)".
function whoDetail(
  v: { token_id: string | null; token_name: string | null; approved_by_user_id: string | null },
  current: Map<string, string>,
): string {
  const cur = v.token_id ? current.get(v.token_id) : undefined;
  if (cur && v.token_name && cur !== v.token_name) return `${cur} (${v.token_name})`;
  return cur || v.token_name || (v.approved_by_user_id ? "admin" : "-");
}

const FEED_POLL_MS = 5_000;

// hass.connection.subscribeEvents, typed loosely (the panel receives an untyped
// hass). Returns an unsubscribe function, or null if unavailable.
type Unsub = () => void;
async function subscribeConfigChanged(hass: unknown, cb: () => void): Promise<Unsub | null> {
  const conn = (hass as { connection?: { subscribeEvents?: (cb: () => void, ev: string) => Promise<Unsub> } } | null)?.connection;
  if (!conn?.subscribeEvents) return null;
  try {
    return await conn.subscribeEvents(cb, "atm_config_changed");
  } catch {
    return null;
  }
}

export function ChangesView({ hass }: { hass: unknown }) {
  const [feed, setFeed] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tokenNames, setTokenNames] = useState<Map<string, string>>(new Map());
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selected;

  // token_id -> latest known name, so a renamed token shows its new name here.
  // Covers archived tokens too, so a renamed-then-revoked token resolves to its
  // final name rather than the one captured with the change.
  const loadTokens = useCallback(async () => {
    try {
      const [active, archived] = await Promise.all([
        api.listTokens(),
        api.listArchivedTokens().catch(() => []),
      ]);
      const map = new Map<string, string>();
      for (const t of archived) map.set(t.id, t.name);
      for (const t of active) map.set(t.id, t.name);
      setTokenNames(map);
    } catch {
      // Names fall back to the value captured with each change.
    }
  }, []);

  useEffect(() => { loadTokens(); }, [loadTokens]);

  const loadFeed = useCallback(async (background = false) => {
    if (!background) setLoading(true);
    setError(null);
    try {
      const resp = await api.listVersions({ limit: 100 });
      setFeed(resp.versions);
    } catch (e: unknown) {
      if (!background) setError(e instanceof Error ? e.message : "Failed to load changes.");
    } finally {
      if (!background) setLoading(false);
    }
  }, []);

  useEffect(() => { loadFeed(); }, [loadFeed]);

  // Refresh instantly when ATM fires atm_config_changed (an agent or a restore
  // recorded a version), so the feed is live without waiting for the poll.
  useEffect(() => {
    let unsub: Unsub | null = null;
    let cancelled = false;
    subscribeConfigChanged(hass, () => { if (!selectedRef.current) loadFeed(true); })
      .then((u) => { if (cancelled) u?.(); else unsub = u; });
    return () => { cancelled = true; unsub?.(); };
  }, [hass, loadFeed]);

  // Poll as a fallback while the feed is the active view (covers a dropped event
  // or a reconnect). Paused while viewing a detail.
  useEffect(() => {
    if (selected) return;
    const id = setInterval(() => loadFeed(true), FEED_POLL_MS);
    return () => clearInterval(id);
  }, [selected, loadFeed]);

  if (selected) {
    return (
      <ChangeDetail
        versionId={selected}
        tokenNames={tokenNames}
        onSelectVersion={setSelected}
        onBack={() => { setSelected(null); loadFeed(); }}
        onRestored={() => loadFeed(true)}
      />
    );
  }

  return (
    <div className="view-root">
      <div className="changes-header">
        <div className="changes-header-text">
          <h3 className="changes-title">Configuration changes</h3>
          <p className="changes-subtitle">
            Versions captured when an agent creates, edits, or deletes automations, scripts, scenes, helpers, and dashboards.
          </p>
        </div>
        <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { loadFeed(); loadTokens(); }} title="Refresh" aria-label="Refresh changes">
          <RefreshIcon />
        </button>
      </div>
      {error && <ErrorMsg msg={error} />}
      {loading ? <Loading /> : feed.length === 0 ? (
        <div className="banner banner-info">
          No configuration changes recorded yet. Agent-made automations, scripts, scenes, helpers, and dashboards appear here.
        </div>
      ) : (
        <div className="changes-table" aria-label="Configuration changes">
          <div className="changes-row changes-row-head" aria-hidden="true">
            <span>Action</span>
            <span className="changes-col-type">Type</span>
            <span>Name</span>
            <span className="changes-col-who">By</span>
            <span className="changes-col-when">When</span>
          </div>
          {feed.map((v) => (
            <button key={v.id} type="button" className="changes-row" onClick={() => setSelected(v.id)}>
              <span><ActionBadge action={v.action} /></span>
              <span className="changes-col-type"><code>{v.resource_type}</code></span>
              <span className="changes-name">{label(v)}</span>
              <span className="changes-col-who">{whoNow(v, tokenNames)}</span>
              <span className="changes-col-when">{formatDateTime(v.timestamp)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ChangeDetail(
  { versionId, tokenNames, onSelectVersion, onBack, onRestored }: {
    versionId: string;
    tokenNames: Map<string, string>;
    onSelectVersion: (id: string) => void;
    onBack: () => void;
    onRestored: () => void;
  },
) {
  const [record, setRecord] = useState<VersionRecord | null>(null);
  const [history, setHistory] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which snapshot the admin is confirming a restore of: the prior config
  // ("before") or the config this version produced ("after"). null = no prompt.
  const [confirmSide, setConfirmSide] = useState<"before" | "after" | null>(null);
  const [busy, setBusy] = useState(false);
  // Default to stacked on narrow viewports (matches the 800px CSS breakpoint
  // that forces a single column there) and side-by-side on wider screens.
  const [stacked, setStacked] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 800px)").matches,
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setConfirmSide(null);
    setError(null);
    api.getVersion(versionId)
      .then((r) => {
        if (!active) return;
        setRecord(r);
        return api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id })
          .then((resp) => { if (active) setHistory(resp.versions); })
          .catch(() => { if (active) setHistory([]); });
      })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : "Failed to load version."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [versionId]);

  async function restore(side: "before" | "after") {
    setBusy(true);
    setError(null);
    try {
      await api.restoreVersion(versionId, side);
      onRestored();
      onBack();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Restore failed.");
      setBusy(false);
      setConfirmSide(null);
    }
  }

  const resourceLabel = record ? label(record) : "";
  // The newest version's "after" is the resource's current config, so restoring
  // it is a no-op; hide that button (the Before stays useful as an undo).
  const isLatest = history.length > 0 && history[0].id === versionId;
  const isRaw = !!record && RAW_CONTENT_TYPES.has(record.resource_type);
  const beforeRaw = isRaw && record ? rawSide(record.before) : null;
  const afterRaw = isRaw && record ? rawSide(record.after) : null;
  // A raw snapshot stored as a too-large marker (content null) is not restorable.
  const beforeRestorable = isRaw ? beforeRaw?.content != null : !!record && record.before != null;
  const afterRestorable = isRaw ? afterRaw?.content != null : !!record && record.after != null;
  const showBefore = beforeRestorable;
  const showAfter = afterRestorable && !isLatest;
  const rawDiff = isRaw ? diffLines(beforeRaw?.content ?? "", afterRaw?.content ?? "") : null;

  return (
    <div className="view-root change-detail">
      <div className="change-detail-bar">
        <button className="btn btn-text btn-sm" onClick={onBack}>&larr; Back to changes</button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {loading || !record ? <Loading /> : (
        <>
          <div className="change-detail-head">
            <ActionBadge action={record.action} />
            <strong className="change-detail-name">{resourceLabel}</strong>
            <code className="change-detail-type">{record.resource_type}</code>
            <span className="change-detail-when">{formatDateTime(record.timestamp)}</span>
            <span className="change-detail-by">by {whoDetail(record, tokenNames)}</span>
          </div>

          <div className="change-detail-body">
            <aside className="change-timeline" aria-label="Version timeline">
              {history.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className={`change-timeline-row${h.id === versionId ? " active" : ""}`}
                  onClick={() => h.id !== versionId && onSelectVersion(h.id)}
                >
                  <ActionBadge action={h.action} />
                  <span className="change-timeline-when">{formatDateTime(h.timestamp)}</span>
                </button>
              ))}
            </aside>

            <div className="change-diff-wrap">
              <div className="change-diff-toolbar">
                <span className="change-diff-hint">
                  {showBefore || showAfter
                    ? <>Use <strong>Restore</strong> on a pane to re-apply that configuration to <strong>{resourceLabel}</strong>.</>
                    : <>This is the current configuration of <strong>{resourceLabel}</strong>; there is nothing to restore.</>}
                </span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setStacked((s) => !s)}
                  title="Switch the before/after layout"
                >
                  {stacked ? "Side-by-side view" : "Stacked view"}
                </button>
              </div>

              {confirmSide && (
                <div className="banner banner-warn change-restore-note">
                  Re-applies the <strong>{confirmSide === "before" ? "Before" : "After"}</strong> configuration to{" "}
                  <code>{record.resource_type}</code> <strong>{resourceLabel}</strong>, overwriting its current config
                  (creating it if it no longer exists), and records a new <em>rollback</em> entry you can restore to undo it.
                  <div className="change-restore-actions">
                    <button className="btn btn-text btn-sm" onClick={() => setConfirmSide(null)} disabled={busy}>Cancel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => restore(confirmSide)} disabled={busy}>
                      {busy ? "Restoring..." : `Confirm restore of ${confirmSide === "before" ? "Before" : "After"}`}
                    </button>
                  </div>
                </div>
              )}

              <div className={`yaml-diff-cols${stacked ? " stacked" : ""}`}>
                <div className="yaml-diff-col">
                  <div className="yaml-pane-head">
                    <span className="approval-diff-label">Before</span>
                    {showBefore && (
                      <button className="btn btn-primary btn-sm" onClick={() => setConfirmSide("before")} disabled={busy || confirmSide === "before"}>
                        Restore this configuration
                      </button>
                    )}
                  </div>
                  {isRaw
                    ? (beforeRaw?.content != null
                        ? <RawDiffPane rows={rawDiff!.beforeRows} tone="remove" />
                        : <pre className="yaml-pre yaml-pre-empty">{beforeRaw ? `(snapshot too large to display${beforeRaw.bytes ? `, ${beforeRaw.bytes} bytes` : ""})` : "(none)"}</pre>)
                    : <YamlView value={toYaml(record.before)} />}
                </div>
                <div className="yaml-diff-col">
                  <div className="yaml-pane-head">
                    <span className="approval-diff-label">After</span>
                    {showAfter && (
                      <button className="btn btn-primary btn-sm" onClick={() => setConfirmSide("after")} disabled={busy || confirmSide === "after"}>
                        Restore this configuration
                      </button>
                    )}
                  </div>
                  {isRaw
                    ? (afterRaw?.content != null
                        ? <RawDiffPane rows={rawDiff!.afterRows} tone="add" />
                        : <pre className="yaml-pre yaml-pre-empty">{afterRaw ? `(snapshot too large to display${afterRaw.bytes ? `, ${afterRaw.bytes} bytes` : ""})` : "(none)"}</pre>)
                    : <YamlView value={toYaml(record.after)} />}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
