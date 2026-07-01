import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { ApprovalRecord, ApprovalStatus } from "../types";
import { api } from "../api";
import { Loading, ErrorMsg } from "../index";
import { Modal } from "../components/Modal";
import { BeforeAfter } from "../components/DiffView";
import { formatDateTime } from "../utils";

interface Props {
  /** Called when an approval resolves so the parent can refresh the badge count. */
  onCountChange?: () => void;
  /** Deep-link target from a notification (/atm#approvals/{id}); opens that approval. */
  openApprovalId?: string | null;
  /** Called once the deep-link has been consumed so the parent can clear it. */
  onConsumedDeepLink?: () => void;
}

const POLL_INTERVAL_MS = 10_000;
const HISTORY_PAGE = 50;
const HISTORY_FILTERS: (ApprovalStatus | "all")[] = ["all", "approved", "rejected", "expired", "cancelled"];
const FILTER_LABEL: Record<string, string> = {
  all: "All", approved: "Approved", rejected: "Rejected", expired: "Expired", cancelled: "Cancelled",
};

export function ApprovalsView({ onCountChange, openApprovalId, onConsumedDeepLink }: Props) {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const [records, setRecords] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ApprovalRecord | null>(null);
  const [histFilter, setHistFilter] = useState<ApprovalStatus | "all">("all");
  const [search, setSearch] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [rawOffset, setRawOffset] = useState(0);  // raw records fetched (drives pagination)

  const loadPending = useCallback(async () => {
    setError(null);
    try {
      const resp = await api.listApprovals({ status: "pending" });
      setRecords(resp.approvals);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load approvals.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async (offset: number) => {
    setError(null);
    try {
      const resp = await api.listApprovals({
        status: histFilter === "all" ? undefined : histFilter,
        limit: HISTORY_PAGE,
        offset,
      });
      const page = histFilter === "all"
        ? resp.approvals.filter((r) => r.status !== "pending")
        : resp.approvals;
      setRecords((prev) => {
        if (offset === 0) return page;
        const seen = new Set(prev.map((r) => r.id));
        return [...prev, ...page.filter((r) => !seen.has(r.id))];
      });
      setRawOffset(offset + resp.approvals.length);
      setHasMore(offset + resp.approvals.length < resp.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load approvals.");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [histFilter]);

  // (Re)load when the tab or the history filter changes.
  useEffect(() => {
    setLoading(true);
    setRecords([]);
    if (tab === "pending") loadPending();
    else loadHistory(0);
  }, [tab, histFilter, loadPending, loadHistory]);

  // Poll while the pending tab is open.
  useEffect(() => {
    if (tab !== "pending") return;
    const id = setInterval(loadPending, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [tab, loadPending]);

  // Consume a notification deep-link: fetch the approval and open it.
  useEffect(() => {
    if (!openApprovalId) return;
    let cancelled = false;
    api.getApproval(openApprovalId)
      .then((rec) => {
        if (cancelled) return;
        setTab(rec.status === "pending" ? "pending" : "history");
        setSelected(rec);
      })
      .catch(() => { /* stale/unknown id: ignore */ })
      .finally(() => onConsumedDeepLink?.());
    return () => { cancelled = true; };
  }, [openApprovalId, onConsumedDeepLink]);

  function handleResolved(updated: ApprovalRecord) {
    setSelected(null);
    setRecords((prev) => prev.filter((r) => r.id !== updated.id));
    if (tab === "pending") loadPending();
    onCountChange?.();
  }

  function switchTopTab(next: "pending" | "history", tablist?: EventTarget & HTMLDivElement) {
    setTab(next);
    window.requestAnimationFrame(() => {
      tablist?.querySelector<HTMLButtonElement>(`#approval-tab-${next}`)?.focus();
    });
  }

  function handleTopTabKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const next = e.key === "Home"
      ? "pending"
      : e.key === "End"
        ? "history"
        : tab === "pending" ? "history" : "pending";
    switchTopTab(next, e.currentTarget);
  }

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q || tab === "pending") return records;
    return records.filter((r) =>
      `${r.token_name} ${r.tool_name} ${r.diff?.summary ?? ""} ${r.rejected_reason ?? ""}`.toLowerCase().includes(q),
    );
  }, [records, search, tab]);

  if (loading) return <Loading />;

  return (
    <div className="approvals-view">
      <div className="approvals-tabs" role="tablist" aria-label="Approval views" onKeyDown={handleTopTabKeyDown}>
        <button
          id="approval-tab-pending"
          role="tab"
          aria-selected={tab === "pending"}
          aria-controls="approval-panel-pending"
          tabIndex={tab === "pending" ? 0 : -1}
          className={`approvals-tab${tab === "pending" ? " active" : ""}`}
          onClick={() => switchTopTab("pending")}
        >
          Pending {tab === "pending" && records.length > 0 ? `(${records.length})` : ""}
        </button>
        <button
          id="approval-tab-history"
          role="tab"
          aria-selected={tab === "history"}
          aria-controls="approval-panel-history"
          tabIndex={tab === "history" ? 0 : -1}
          className={`approvals-tab${tab === "history" ? " active" : ""}`}
          onClick={() => switchTopTab("history")}
        >
          History
        </button>
      </div>

      <div
        id={`approval-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`approval-tab-${tab}`}
      >
      {tab === "history" && (
        <div className="mesa-controls">
          <div className="mesa-summary" role="group" aria-label="Filter by status">
            {HISTORY_FILTERS.map((f) => (
              <button key={f}
                className={`mesa-chip${histFilter === f ? " mesa-chip-active" : ""}`}
                aria-pressed={histFilter === f}
                onClick={() => setHistFilter(f)}>
                {FILTER_LABEL[f]}
              </button>
            ))}
          </div>
          <input className="input mesa-search" placeholder="Search token, tool, or reason..."
            value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search approvals" />
        </div>
      )}

      {error && <ErrorMsg msg={error} />}

      {shown.length === 0 && !error && (
        <div className="approvals-empty">
          {tab === "pending"
            ? "No pending approvals. Tokens with Confirm-mode capabilities will create requests here."
            : search.trim() ? "No approvals match your search." : "No resolved approvals yet."}
        </div>
      )}

      {tab === "pending" ? (
        <div className="approvals-list">
          {shown.map((r) => (
            <ApprovalCard key={r.id} record={r} onClick={() => setSelected(r)} />
          ))}
        </div>
      ) : (
        shown.length > 0 && (
          <div className="card approval-history">
            {shown.map((r) => (
              <HistoryRow key={r.id} record={r} onClick={() => setSelected(r)} />
            ))}
          </div>
        )
      )}

      {tab === "history" && hasMore && !search.trim() && (
        <div className="approval-history-more">
          <button className="btn btn-ghost btn-sm" disabled={loadingMore}
            onClick={() => { setLoadingMore(true); loadHistory(rawOffset); }}>
            {loadingMore ? "Loading..." : "Load more"}
          </button>
        </div>
      )}

      {selected && (
        <ApprovalDetailModal
          record={selected}
          onClose={() => setSelected(null)}
          onResolved={handleResolved}
        />
      )}
      </div>
    </div>
  );
}

function HistoryRow({ record, onClick }: { record: ApprovalRecord; onClick: () => void }) {
  const note = record.diff?.summary || (record.rejected_reason ? `Reason: ${record.rejected_reason}` : record.tool_name);
  return (
    <button type="button" className="approval-history-row" onClick={onClick}>
      <StatusBadge status={record.status} />
      <code className="approval-history-tool">{record.tool_name}</code>
      <span className="approval-history-token">{record.token_name}</span>
      <span className="approval-history-note">{note}</span>
      <span className="approval-history-time">{formatDateTime(record.resolved_at || record.created_at)}</span>
    </button>
  );
}

function StatusBadge({ status }: { status: ApprovalStatus }) {
  const label: Record<ApprovalStatus, string> = {
    pending: "Pending",
    approved: "Approved",
    rejected: "Rejected",
    expired: "Expired",
    cancelled: "Cancelled",
  };
  const cls: Record<ApprovalStatus, string> = {
    pending: "badge-amber",
    approved: "badge-green",
    rejected: "badge-red",
    expired: "badge-grey",
    cancelled: "badge-grey",
  };
  return <span className={`badge ${cls[status]}`}>{label[status]}</span>;
}

function ApprovalCard({ record, onClick }: { record: ApprovalRecord; onClick: () => void }) {
  const expiresIn = useExpiresLabel(record.expires_at, record.status);
  return (
    <button type="button" className="approval-card" onClick={onClick}>
      <div className="approval-card-header">
        <div className="approval-card-title">
          <span className="approval-card-token">{record.token_name}</span>
          <span className="approval-card-tool">{record.tool_name}</span>
        </div>
        <div className="approval-card-meta">
          <StatusBadge status={record.status} />
          <span className="approval-card-time">
            {formatDateTime(record.created_at)} {expiresIn ? `· ${expiresIn}` : ""}
          </span>
        </div>
      </div>
      <div className="approval-card-summary">
        {record.diff?.summary || record.tool_name}
      </div>
      {record.rejected_reason && (
        <div className="approval-card-reason">Reason: {record.rejected_reason}</div>
      )}
    </button>
  );
}

function useExpiresLabel(expiresAt: string, status: ApprovalStatus): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== "pending") return;
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [status]);
  if (status !== "pending") return null;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return null;
  const remaining = t - now;
  if (remaining <= 0) return "expired";
  const mins = Math.round(remaining / 60_000);
  if (mins < 60) return `${mins}m left`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m left`;
}

interface DetailProps {
  record: ApprovalRecord;
  onClose: () => void;
  onResolved: (updated: ApprovalRecord) => void;
}

function ApprovalDetailModal({ record, onClose, onResolved }: DetailProps) {
  const [activeTab, setActiveTab] = useState<"diff" | "args" | "result">("diff");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const isPending = record.status === "pending";
  const detailTabs: Array<"diff" | "args" | "result"> = isPending ? ["diff", "args"] : ["diff", "args", "result"];

  function switchDetailTab(next: "diff" | "args" | "result", tablist?: EventTarget & HTMLDivElement) {
    setActiveTab(next);
    window.requestAnimationFrame(() => {
      tablist?.querySelector<HTMLButtonElement>(`#approval-detail-tab-${next}`)?.focus();
    });
  }

  function handleDetailTabKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const i = detailTabs.indexOf(activeTab);
    const next = e.key === "Home"
      ? detailTabs[0]
      : e.key === "End"
        ? detailTabs[detailTabs.length - 1]
        : e.key === "ArrowRight"
          ? detailTabs[(i + 1) % detailTabs.length]
          : detailTabs[(i - 1 + detailTabs.length) % detailTabs.length];
    switchDetailTab(next, e.currentTarget);
  }

  async function approve() {
    setBusy("approve");
    setError(null);
    try {
      const updated = await api.approveApproval(record.id);
      onResolved(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Approve failed.");
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      const updated = await api.rejectApproval(record.id, reason ? { reason } : {});
      onResolved(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Reject failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal titleId="approval-detail-title" onClose={busy ? undefined : onClose}>
      <h3 className="modal-title" id="approval-detail-title">
        {record.diff?.summary || record.tool_name}
      </h3>
      <div className="approval-detail-meta">
        <span className="stat-label">Token</span>
        <span>{record.token_name}</span>
        <span className="stat-label">Tool</span>
        <span><code>{record.tool_name}</code></span>
        <span className="stat-label">Capability</span>
        <span><code>{record.cap_name}</code></span>
        <span className="stat-label">Created</span>
        <span>{formatDateTime(record.created_at)}</span>
        <span className="stat-label">Expires</span>
        <span>{formatDateTime(record.expires_at)}</span>
        <span className="stat-label">Status</span>
        <span><StatusBadge status={record.status} /></span>
      </div>

      {!isPending && record.rejected_reason && (
        <div className="banner banner-error">
          <strong>
            {record.status === "rejected" ? "Rejected" : record.status === "cancelled" ? "Cancelled" : "Reason"}:
          </strong>{" "}
          {record.rejected_reason}
        </div>
      )}

      <div className="approval-detail-tabs" role="tablist" aria-label="Approval detail" onKeyDown={handleDetailTabKeyDown}>
        <button
          id="approval-detail-tab-diff"
          role="tab"
          aria-selected={activeTab === "diff"}
          aria-controls="approval-detail-panel"
          tabIndex={activeTab === "diff" ? 0 : -1}
          className={`approval-detail-tab${activeTab === "diff" ? " active" : ""}`}
          onClick={() => switchDetailTab("diff")}
        >
          Diff
        </button>
        <button
          id="approval-detail-tab-args"
          role="tab"
          aria-selected={activeTab === "args"}
          aria-controls="approval-detail-panel"
          tabIndex={activeTab === "args" ? 0 : -1}
          className={`approval-detail-tab${activeTab === "args" ? " active" : ""}`}
          onClick={() => switchDetailTab("args")}
        >
          Raw args
        </button>
        {!isPending && (
          <button
            id="approval-detail-tab-result"
            role="tab"
            aria-selected={activeTab === "result"}
            aria-controls="approval-detail-panel"
            tabIndex={activeTab === "result" ? 0 : -1}
            className={`approval-detail-tab${activeTab === "result" ? " active" : ""}`}
            onClick={() => switchDetailTab("result")}
          >
            Result
          </button>
        )}
      </div>

      <div
        id="approval-detail-panel"
        className="approval-detail-body"
        role="tabpanel"
        aria-labelledby={`approval-detail-tab-${activeTab}`}
      >
        {activeTab === "diff" && <DiffView record={record} />}
        {activeTab === "args" && (
          <pre className="approval-pre">{JSON.stringify(record.args, null, 2)}</pre>
        )}
        {activeTab === "result" && (
          record.result == null ? (
            <p className="approvals-empty">
              {record.rejected_reason
                ? `No result. ${record.status === "rejected" ? "Rejected" : "Cancelled"}: ${record.rejected_reason}`
                : `No result recorded (status: ${record.status}).`}
            </p>
          ) : (
            <pre className="approval-pre">{JSON.stringify(record.result, null, 2)}</pre>
          )
        )}
      </div>

      {error && <ErrorMsg msg={error} />}

      {isPending && (
        <>
          <div className="approval-reject-row">
            <label htmlFor="approval-reason" className="approval-reason-label">
              Optional rejection reason
            </label>
            <input
              id="approval-reason"
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="approval-reason-input"
              placeholder="Shown to the requesting token"
              disabled={busy !== null}
            />
          </div>
          <div className="modal-actions">
            <button
              className="btn btn-primary"
              onClick={approve}
              disabled={busy !== null}
            >
              {busy === "approve" ? "Approving..." : "Approve and execute"}
            </button>
            <button
              className="btn btn-danger"
              onClick={reject}
              disabled={busy !== null}
            >
              {busy === "reject" ? "Rejecting..." : "Reject"}
            </button>
            <button
              className="btn btn-text"
              onClick={onClose}
              disabled={busy !== null}
            >
              Close
            </button>
          </div>
        </>
      )}
      {!isPending && (
        <div className="modal-actions">
          <button className="btn btn-text" onClick={onClose}>Close</button>
        </div>
      )}
    </Modal>
  );
}

function DiffView({ record }: { record: ApprovalRecord }) {
  const diff = record.diff || {};
  const kind = diff.kind || "system_action";
  if (kind === "yaml_diff" || kind === "config_diff" || kind === "file_write") {
    return <BeforeAfter before={diff.before ?? null} after={diff.after ?? null} />;
  }
  if (kind === "service_preview") {
    return <ServicePreview preview={diff.preview || {}} />;
  }
  return <SystemActionPreview summary={diff.summary} preview={diff.preview || {}} />;
}

// Render a preview value for the review UI. Nested objects (e.g. service_data)
// are flattened to "key: value" pairs so they never show as "[object Object]".
function renderPreviewValue(v: unknown): string {
  if (v == null) return "(none)";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(none)";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return "(none)";
    return entries
      .map(([k, val]) => `${k}: ${val !== null && typeof val === "object" ? JSON.stringify(val) : String(val)}`)
      .join(", ");
  }
  return String(v);
}

function ServicePreview({ preview }: { preview: Record<string, unknown> }) {
  const mesa = preview.mesa as Record<string, unknown> | undefined;
  return (
    <div>
      <div className="approval-detail-meta">
        {Object.entries(preview).filter(([k]) => k !== "mesa").map(([k, v]) => (
          <React.Fragment key={k}>
            <span className="stat-label">{k}</span>
            <span><code>{renderPreviewValue(v)}</code></span>
          </React.Fragment>
        ))}
      </div>
      {mesa && <MesaPreviewBlock mesa={mesa} />}
    </div>
  );
}

function MesaPreviewBlock({ mesa }: { mesa: Record<string, unknown> }) {
  const confirm = (mesa.confirm_entities as string[]) ?? [];
  const allowed = (mesa.allowed_entities as string[]) ?? [];
  const blocked = (mesa.blocked as Array<{ entity_id: string; rule: string }>) ?? [];
  const warnings = (mesa.warnings as string[]) ?? [];
  return (
    <div className="mesa-preview-block">
      <div className="approval-detail-meta">
        <span className="stat-label">MESA confirm</span>
        <span><code>{confirm.length ? confirm.join(", ") : "(none)"}</code></span>
        <span className="stat-label">Also allowed</span>
        <span><code>{allowed.length ? allowed.join(", ") : "(none)"}</code></span>
        {blocked.length > 0 && (
          <>
            <span className="stat-label">Blocked</span>
            <span><code>{blocked.map((b) => `${b.entity_id} (${b.rule})`).join(", ")}</code></span>
          </>
        )}
      </div>
      {warnings.length > 0 && (
        <ul className="mesa-preview-warnings">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

function SystemActionPreview({ summary, preview }: { summary?: string; preview: Record<string, unknown> }) {
  return (
    <div>
      {summary && <p className="approval-summary-line"><strong>{summary}</strong></p>}
      {Object.keys(preview).length > 0 && (
        <pre className="approval-pre">{JSON.stringify(preview, null, 2)}</pre>
      )}
    </div>
  );
}
