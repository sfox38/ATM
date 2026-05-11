import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { ApprovalRecord, ApprovalStatus } from "../types";
import { api } from "../api";
import { Loading, ErrorMsg } from "../index";
import { Modal } from "../components/Modal";
import { formatDateTime } from "../utils";

interface Props {
  /** Called when an approval resolves so the parent can refresh the badge count. */
  onCountChange?: () => void;
}

const POLL_INTERVAL_MS = 10_000;

export function ApprovalsView({ onCountChange }: Props) {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const [records, setRecords] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ApprovalRecord | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const status: ApprovalStatus | undefined = tab === "pending" ? "pending" : undefined;
      const resp = await api.listApprovals(status ? { status } : {});
      const list = tab === "pending"
        ? resp.approvals
        : resp.approvals.filter((r) => r.status !== "pending");
      setRecords(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load approvals.");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Poll while pending tab is open.
  useEffect(() => {
    if (tab !== "pending") return;
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [tab, load]);

  function handleResolved(updated: ApprovalRecord) {
    setSelected(null);
    setRecords((prev) => prev.filter((r) => r.id !== updated.id));
    // Refresh in case the resolution affects history view.
    load();
    onCountChange?.();
  }

  if (loading) return <Loading />;

  return (
    <div className="approvals-view">
      <div className="approvals-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "pending"}
          className={`approvals-tab${tab === "pending" ? " active" : ""}`}
          onClick={() => setTab("pending")}
        >
          Pending {tab === "pending" && records.length > 0 ? `(${records.length})` : ""}
        </button>
        <button
          role="tab"
          aria-selected={tab === "history"}
          className={`approvals-tab${tab === "history" ? " active" : ""}`}
          onClick={() => setTab("history")}
        >
          History
        </button>
      </div>

      {error && <ErrorMsg msg={error} />}

      {records.length === 0 && !error && (
        <div className="approvals-empty">
          {tab === "pending"
            ? "No pending approvals. Tokens with Confirm-mode capabilities will create requests here."
            : "No resolved approvals yet."}
        </div>
      )}

      <div className="approvals-list">
        {records.map((r) => (
          <ApprovalCard
            key={r.id}
            record={r}
            onClick={() => setSelected(r)}
          />
        ))}
      </div>

      {selected && (
        <ApprovalDetailModal
          record={selected}
          onClose={() => setSelected(null)}
          onResolved={handleResolved}
        />
      )}
    </div>
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
        {record.rejected_reason && (<>
          <span className="stat-label">Reason</span>
          <span>{record.rejected_reason}</span>
        </>)}
      </div>

      <div className="approval-detail-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={activeTab === "diff"}
          className={`approval-detail-tab${activeTab === "diff" ? " active" : ""}`}
          onClick={() => setActiveTab("diff")}
        >
          Diff
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "args"}
          className={`approval-detail-tab${activeTab === "args" ? " active" : ""}`}
          onClick={() => setActiveTab("args")}
        >
          Raw args
        </button>
        {!isPending && (
          <button
            role="tab"
            aria-selected={activeTab === "result"}
            className={`approval-detail-tab${activeTab === "result" ? " active" : ""}`}
            onClick={() => setActiveTab("result")}
          >
            Result
          </button>
        )}
      </div>

      <div className="approval-detail-body">
        {activeTab === "diff" && <DiffView record={record} />}
        {activeTab === "args" && (
          <pre className="approval-pre">{JSON.stringify(record.args, null, 2)}</pre>
        )}
        {activeTab === "result" && (
          <pre className="approval-pre">{JSON.stringify(record.result, null, 2)}</pre>
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

function BeforeAfter({ before, after }: { before: string | null; after: string | null }) {
  return (
    <div className="approval-diff-cols">
      <div className="approval-diff-col">
        <div className="approval-diff-label">Before</div>
        <pre className="approval-pre">{before ?? "(none)"}</pre>
      </div>
      <div className="approval-diff-col">
        <div className="approval-diff-label">After</div>
        <pre className="approval-pre">{after ?? "(none)"}</pre>
      </div>
    </div>
  );
}

function ServicePreview({ preview }: { preview: Record<string, unknown> }) {
  return (
    <div>
      <div className="approval-detail-meta">
        {Object.entries(preview).map(([k, v]) => (
          <React.Fragment key={k}>
            <span className="stat-label">{k}</span>
            <span><code>{Array.isArray(v) ? v.join(", ") : v == null ? "(none)" : String(v)}</code></span>
          </React.Fragment>
        ))}
      </div>
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
