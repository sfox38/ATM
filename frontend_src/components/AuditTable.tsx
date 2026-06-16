import React, { useState } from "react";
import type { AuditEntry, Outcome } from "../types";
import { Modal } from "./Modal";

interface Props {
  entries: AuditEntry[];
  loading?: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

function formatTokenName(name: string): string {
  return name.replace(/^(admin):(.+)$/, "$1 ($2)");
}

function formatTokenNameShort(name: string): string {
  return name.replace(/^(admin):(.{4}).+(.{4})$/, "$1 ($2...$3)");
}

function formatTs(iso: string): string {
  return new Date(iso).toLocaleString();
}

function formatTsShort(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const OUTCOME_LABEL: Record<Outcome, string> = {
  allowed: "Allowed",
  denied: "Denied",
  not_found: "Not Found",
  rate_limited: "Rate Limited",
  not_implemented: "Not Implemented",
  invalid_request: "Invalid Request",
  pending_approval: "Pending Approval",
};

const OUTCOME_CLASS: Record<Outcome, string> = {
  allowed: "outcome-allowed",
  denied: "outcome-denied",
  not_found: "outcome-not_found",
  rate_limited: "outcome-rate_limited",
  not_implemented: "outcome-not_implemented",
  invalid_request: "outcome-invalid_request",
  pending_approval: "outcome-pending_approval",
};

type SortKey = "timestamp" | "token_name" | "method" | "resource" | "outcome" | "client_ip";
type SortDir = "asc" | "desc";

function SortArrow({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  const active = col === sortKey;
  return <span className={`sort-arrow${active ? " active" : ""}`}>{active ? (sortDir === "asc" ? "↑" : "↓") : "↕"}</span>;
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <span className={mono ? "detail-value-mono" : "detail-value"}>{value}</span>
    </div>
  );
}

function EntryDetailModal({ entry, onClose }: { entry: AuditEntry; onClose: () => void }) {
  const prettyPayload = entry.payload
    ? (() => { try { return JSON.stringify(JSON.parse(entry.payload), null, 2); } catch { return entry.payload; } })()
    : null;
  return (
    <Modal titleId="audit-detail-title" onClose={onClose}>
      <h3 className="modal-title audit-section-title" id="audit-detail-title">Audit Entry</h3>
      <DetailRow label="Time" value={formatTs(entry.timestamp)} />
      <DetailRow label="Token" value={formatTokenName(entry.token_name)} />
      <DetailRow label="Mode" value={entry.pass_through ? "Pass Through" : "Scoped"} />
      <DetailRow label="Method" value={entry.method} mono />
      <DetailRow label="Resource" value={entry.resource} mono />
      <DetailRow label="Outcome" value={OUTCOME_LABEL[entry.outcome] ?? entry.outcome} />
      {entry.mesa_advisory && <DetailRow label="MESA" value="Advisory: proceeded with a warning (would require approval if enforced)" />}
      <DetailRow label="IP" value={entry.client_ip} mono />
      <DetailRow label="Request ID" value={entry.request_id} mono />
      {prettyPayload && (
        <div className="audit-payload-section">
          <span className="detail-label">Payload</span>
          <pre className="audit-payload-pre">{prettyPayload}</pre>
        </div>
      )}
      <div className="modal-actions">
        <button className="btn btn-text" onClick={onClose}>Close</button>
      </div>
    </Modal>
  );
}

export function AuditTable({ entries, loading, page, pageSize, onPageChange }: Props) {
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  if (loading) {
    return <div className="loading-wrap"><div className="spinner" /><span>Loading...</span></div>;
  }

  if (entries.length === 0) {
    return <p className="audit-empty">No audit entries found.</p>;
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  }

  const sorted = [...entries].sort((a, b) => {
    const va = a[sortKey] ?? "";
    const vb = b[sortKey] ?? "";
    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  const totalPages = Math.ceil(sorted.length / pageSize);
  const slice = sorted.slice(page * pageSize, page * pageSize + pageSize);

  function th(label: string, key: SortKey) {
    return (
      <th
        className={`sortable${sortKey === key ? " sort-active" : ""}`}
        onClick={() => handleSort(key)}
      >
        {label}<SortArrow col={key} sortKey={sortKey} sortDir={sortDir} />
      </th>
    );
  }

  return (
    <div>
      {selected && <EntryDetailModal entry={selected} onClose={() => setSelected(null)} />}
      <table className="data-table audit-table">
        <thead>
          <tr>
            {th("Outcome", "outcome")}
            {th("Token", "token_name")}
            {th("Time", "timestamp")}
            {th("Method", "method")}
            {th("Resource", "resource")}
            {th("IP", "client_ip")}
          </tr>
        </thead>
        <tbody>
          {slice.map((entry) => (
            <tr
              key={entry.request_id}
              className={`clickable${entry.pass_through ? " pass-through-row" : ""}`}
              onClick={() => setSelected(entry)}
            >
              <td>
                <span className={`outcome-badge ${OUTCOME_CLASS[entry.outcome]}`}>
                  {OUTCOME_LABEL[entry.outcome] ?? entry.outcome}
                </span>
                {entry.mesa_advisory && (
                  <span className="outcome-badge mesa-advisory-badge" title="MESA advisory: proceeded with a warning">MESA</span>
                )}
              </td>
              <td title={formatTokenName(entry.token_name)}>{formatTokenNameShort(entry.token_name)}</td>
              <td>
                <span className="audit-time-full">{formatTs(entry.timestamp)}</span>
                <span className="audit-time-short">{formatTsShort(entry.timestamp)}</span>
              </td>
              <td className="audit-cell-method">{entry.method}</td>
              <td className="audit-cell-resource" title={entry.resource}>{entry.resource}</td>
              <td className="audit-cell-ip" title={entry.client_ip}>{entry.client_ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {totalPages > 1 && (
        <div className="pagination">
          <button
            className="btn btn-text btn-sm"
            onClick={() => onPageChange(page - 1)}
            disabled={page === 0}
          >
            Prev
          </button>
          <span>Page {page + 1} of {totalPages}</span>
          <button
            className="btn btn-text btn-sm"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages - 1}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
