import React, { useState, useEffect, useCallback } from "react";
import type { TokenRecord, ArchivedTokenRecord } from "../types";
import { TokenCreateModal } from "../components/TokenCreateModal";
import { ArchivedTokenTable } from "../components/ArchivedTokenTable";
import { api } from "../api";
import { Loading, ErrorMsg, RefreshIcon } from "../index";
import { formatDate, tokenStatus } from "../utils";

const MAX_ACTIVE_TOKENS_WARNING = 50;

function relativeTime(iso: string | null): string {
  if (!iso) return "Never";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}


type SortKey = "name" | "mode" | "status" | "created" | "updated" | "expires" | "last_used";
type SortDir = "asc" | "desc";

function SortArrow({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  const active = col === sortKey;
  return <span className={`sort-arrow${active ? " active" : ""}`}>{active ? (sortDir === "asc" ? "↑" : "↓") : "↕"}</span>;
}

interface Props {
  tokens: TokenRecord[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenDetail: (id: string) => void;
  onLaunchWizard: () => void;
  showCreate: boolean;
  onCloseCreate: () => void;
}

export function TokenListView({ tokens, loading, error, onRefresh, onOpenDetail, onLaunchWizard, showCreate, onCloseCreate }: Props) {
  const [filter, setFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<ArchivedTokenRecord[] | null>(null);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const refreshArchived = useCallback(async () => {
    setArchivedLoading(true);
    try {
      setArchived(await api.listArchivedTokens());
    } catch {
      setArchived([]);
    } finally {
      setArchivedLoading(false);
    }
  }, []);

  useEffect(() => {
    if (showArchived) refreshArchived();
  }, [showArchived, refreshArchived]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  }

  const filtered = tokens.filter((t) => {
    const q = filter.toLowerCase();
    if (!q) return true;
    return t.name.toLowerCase().includes(q) || tokenStatus(t).toLowerCase().includes(q);
  });

  const sorted = [...filtered].sort((a, b) => {
    let va: string | number = "";
    let vb: string | number = "";
    switch (sortKey) {
      case "name":     va = a.name.toLowerCase();   vb = b.name.toLowerCase(); break;
      case "mode":     va = a.pass_through ? "1" : "0"; vb = b.pass_through ? "1" : "0"; break;
      case "status":   va = tokenStatus(a);          vb = tokenStatus(b); break;
      case "created":  va = a.created_at ?? "";      vb = b.created_at ?? ""; break;
      case "updated":  va = a.updated_at ?? "";      vb = b.updated_at ?? ""; break;
      case "expires":  va = a.expires_at ?? "9999";  vb = b.expires_at ?? "9999"; break;
      case "last_used": va = a.last_used_at ?? "";   vb = b.last_used_at ?? ""; break;
    }
    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  function handleCreated(record: TokenRecord) {
    onRefresh();
    onCloseCreate();
    onOpenDetail(record.id);
  }

  function handleArchivedDeleted(id: string) {
    setArchived((prev) => prev?.filter((t) => t.id !== id) ?? null);
  }

  function th(label: string, key: SortKey, className?: string) {
    return (
      <th
        className={`sortable${sortKey === key ? " sort-active" : ""}${className ? ` ${className}` : ""}`}
        onClick={() => handleSort(key)}
      >
        {label}<SortArrow col={key} sortKey={sortKey} sortDir={sortDir} />
      </th>
    );
  }

  // First run: no tokens and no filter. Show an onboarding hero that launches
  // the wizard instead of the empty table. The create modal is still mounted
  // here so the header "Create Token" button works in the empty state.
  if (!loading && !error && tokens.length === 0 && !filter && !showArchived) {
    return (
      <div className="view-root">
        <div className="card wizard-hero">
          <div className="wizard-hero-title">Set up your first agent</div>
          <p className="wizard-hero-sub">
            ATM lets an AI agent control your Home Assistant through a scoped, revocable token. Let's walk through creating one and connecting your agent, step by step.
          </p>
          <div className="wizard-hero-actions">
            <button className="btn btn-primary" onClick={onLaunchWizard}>Set up your first agent</button>
          </div>
        </div>
        {showCreate && (
          <TokenCreateModal
            existingNames={tokens.map((t) => t.name)}
            onCreated={handleCreated}
            onClose={onCloseCreate}
          />
        )}
      </div>
    );
  }

  return (
    <div className="view-root">
      {tokens.length >= MAX_ACTIVE_TOKENS_WARNING && (
        <div className="banner banner-warn">
          You have reached the recommended maximum of {MAX_ACTIVE_TOKENS_WARNING} active tokens. Consider revoking unused tokens.
        </div>
      )}

      <div className="card">
        <div className="filter-row">
          <input
            className="input"
            placeholder="Filter by name or status..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="filter-row-right">
            <button
              className="btn btn-ghost btn-sm"
              onClick={onLaunchWizard}
            >
              Set up an agent
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setShowArchived((v) => !v)}
            >
              {showArchived ? "Hide Archived" : "Show Archived"}
            </button>
            <button
              className="btn btn-ghost btn-sm btn-icon"
              onClick={onRefresh}
              title="Refresh"
            >
              <RefreshIcon />
            </button>
          </div>
        </div>

        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorMsg msg={error} />
        ) : (
          <table className="data-table token-table">
            <thead>
              <tr>
                {th("Name", "name")}
                {th("Mode", "mode")}
                {th("Status", "status")}
                {th("Created", "created")}
                {th("Last Updated", "updated")}
                {th("Expires", "expires")}
                {th("Last Used", "last_used")}
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={8} className="token-table-empty">
                    {filter ? "No tokens match the filter." : "No tokens yet. Create one to get started."}
                  </td>
                </tr>
              )}
              {sorted.map((t) => {
                const status = tokenStatus(t);
                const statusClass = status === "Active" ? "badge-green" : status === "Expired" ? "badge-grey" : "badge-red";
                return (
                  <tr
                    key={t.id}
                    className={`clickable${t.pass_through ? " pass-through-row" : ""}`}
                    onClick={() => onOpenDetail(t.id)}
                  >
                    <td className="token-name">{t.name}</td>
                    <td>
                      {t.pass_through
                        ? <span className="badge badge-amber">Pass Through</span>
                        : <span className="badge badge-blue">Scoped</span>}
                    </td>
                    <td><span className={`badge ${statusClass}`}>{status}</span></td>
                    <td title={t.created_at ? new Date(t.created_at).toLocaleString() : undefined}>{formatDate(t.created_at)}</td>
                    <td title={t.updated_at ? new Date(t.updated_at).toLocaleString() : undefined}>{relativeTime(t.updated_at)}</td>
                    <td>{formatDate(t.expires_at)}</td>
                    <td>{relativeTime(t.last_used_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="row-actions">
                        <button className="btn btn-ghost btn-sm" onClick={() => onOpenDetail(t.id)}>Edit</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {showArchived && (
        <div className="card">
          <div className="card-header">
            <span>Archived Tokens</span>
          </div>
          {archivedLoading ? (
            <Loading />
          ) : (
            <ArchivedTokenTable
              tokens={archived ?? []}
              onDeleted={handleArchivedDeleted}
            />
          )}
        </div>
      )}

      {showCreate && (
        <TokenCreateModal
          existingNames={tokens.map((t) => t.name)}
          onCreated={handleCreated}
          onClose={onCloseCreate}
        />
      )}
    </div>
  );
}
