/** Configuration version history (SPEC Section 16): a recent-changes feed that
 * drills into a resource's timeline, with a before/after diff and admin restore. */
import React, { useCallback, useEffect, useState } from "react";
import type { VersionRecord, VersionSummary } from "../types";
import { api } from "../api";
import { BeforeAfter } from "../components/DiffView";
import { Modal } from "../components/Modal";
import { Loading, ErrorMsg, RefreshIcon } from "../index";
import { formatDateTime } from "../utils";

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

function pretty(value: Record<string, unknown> | null): string | null {
  return value == null ? null : JSON.stringify(value, null, 2);
}

const LIST_STYLE: React.CSSProperties = { display: "flex", flexDirection: "column", gap: "4px" };

interface SelectedResource {
  resource_type: string;
  resource_id: string;
  label: string;
}

export function ChangesView() {
  const [feed, setFeed] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [resource, setResource] = useState<SelectedResource | null>(null);
  const [history, setHistory] = useState<VersionSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [detailId, setDetailId] = useState<string | null>(null);

  const loadFeed = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.listVersions({ limit: 100 });
      setFeed(resp.versions);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load changes.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadFeed(); }, [loadFeed]);

  const loadHistory = useCallback(async (r: SelectedResource) => {
    setHistoryLoading(true);
    try {
      const resp = await api.listVersions({ resource_type: r.resource_type, resource_id: r.resource_id });
      setHistory(resp.versions);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const openResource = useCallback((v: VersionSummary) => {
    const r = { resource_type: v.resource_type, resource_id: v.resource_id, label: label(v) };
    setResource(r);
    setHistory([]);
    loadHistory(r);
  }, [loadHistory]);

  const onRestored = useCallback(() => {
    setDetailId(null);
    if (resource) loadHistory(resource);
    loadFeed();
  }, [resource, loadHistory, loadFeed]);

  if (resource) {
    return (
      <div>
        <div className="filter-row">
          <button className="btn btn-text btn-sm" onClick={() => { setResource(null); loadFeed(); }}>
            &larr; Recent changes
          </button>
          <span><code>{resource.resource_type}</code> <strong>{resource.label}</strong></span>
          <div className="filter-row-right">
            <button className="btn btn-ghost btn-sm btn-icon" onClick={() => loadHistory(resource)} title="Refresh">
              <RefreshIcon />
            </button>
          </div>
        </div>
        {historyLoading ? <Loading /> : history.length === 0 ? (
          <div className="banner banner-info">No version history for this resource.</div>
        ) : (
          <div style={LIST_STYLE}>
            {history.map((v) => <VersionRow key={v.id} v={v} onClick={() => setDetailId(v.id)} />)}
          </div>
        )}
        {detailId && (
          <VersionDetailModal versionId={detailId} onClose={() => setDetailId(null)} onRestored={onRestored} />
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="filter-row">
        <strong>Recent configuration changes</strong>
        <div className="filter-row-right">
          <button className="btn btn-ghost btn-sm btn-icon" onClick={loadFeed} title="Refresh">
            <RefreshIcon />
          </button>
        </div>
      </div>
      {error && <ErrorMsg msg={error} />}
      {loading ? <Loading /> : feed.length === 0 ? (
        <div className="banner banner-info">
          No configuration changes recorded yet. Agent-made automations, scripts, scenes, and helpers appear here.
        </div>
      ) : (
        <div style={LIST_STYLE}>
          {feed.map((v) => <VersionRow key={v.id} v={v} showResource onClick={() => openResource(v)} />)}
        </div>
      )}
    </div>
  );
}

function VersionRow({ v, showResource, onClick }: { v: VersionSummary; showResource?: boolean; onClick: () => void }) {
  return (
    <button type="button" className="approval-history-row" onClick={onClick}>
      <ActionBadge action={v.action} />
      {showResource && <code className="approval-history-tool">{v.resource_type}</code>}
      <span className="approval-history-note">{label(v)}</span>
      <span className="approval-history-token">{v.token_name || (v.approved_by_user_id ? "admin" : "-")}</span>
      <span className="approval-history-time">{formatDateTime(v.timestamp)}</span>
    </button>
  );
}

function VersionDetailModal(
  { versionId, onClose, onRestored }: { versionId: string; onClose: () => void; onRestored: () => void },
) {
  const [record, setRecord] = useState<VersionRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.getVersion(versionId)
      .then((r) => { if (active) setRecord(r); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : "Failed to load version."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [versionId]);

  async function restore() {
    setBusy(true);
    setError(null);
    try {
      await api.restoreVersion(versionId);
      onRestored();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Restore failed.");
      setBusy(false);
    }
  }

  return (
    <Modal titleId="version-detail-title" onClose={onClose}>
      <h3 className="modal-title" id="version-detail-title">
        {record ? `${record.action} - ${label(record)}` : "Version"}
      </h3>
      {error && <div className="banner banner-error">{error}</div>}
      {loading || !record ? <Loading /> : (
        <>
          <div className="approval-detail-meta">
            <span className="stat-label">Resource</span>
            <span><code>{record.resource_type}</code> {record.resource_id}</span>
            <span className="stat-label">When</span>
            <span>{formatDateTime(record.timestamp)}</span>
            <span className="stat-label">By</span>
            <span>
              {record.token_name || "-"}
              {record.approved_by_user_id ? ` (restored by admin ${record.approved_by_user_id})` : ""}
            </span>
          </div>
          <BeforeAfter before={pretty(record.before)} after={pretty(record.after)} />
        </>
      )}
      <div className="modal-actions">
        {confirm ? (
          <>
            <span style={{ marginRight: "auto" }}>Re-apply this version's configuration?</span>
            <button className="btn btn-text" onClick={() => setConfirm(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" onClick={restore} disabled={busy}>
              {busy ? "Restoring..." : "Confirm restore"}
            </button>
          </>
        ) : (
          <>
            <button className="btn btn-text" onClick={onClose}>Close</button>
            <button className="btn btn-primary" onClick={() => setConfirm(true)} disabled={loading || !record}>
              Restore this version
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
