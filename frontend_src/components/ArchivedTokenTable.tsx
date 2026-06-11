import React, { useState } from "react";
import type { ArchivedTokenRecord } from "../types";
import { api } from "../api";
import { formatDate } from "../utils";

type SortKey = "name" | "status" | "created" | "archived" | "last_used";
type SortDir = "asc" | "desc";

interface Props {
  tokens: ArchivedTokenRecord[];
  onDeleted: (id: string) => void;
}

function SortArrow({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  const active = col === sortKey;
  return <span className={`sort-arrow${active ? " active" : ""}`}>{active ? (sortDir === "asc" ? "↑" : "↓") : "↕"}</span>;
}

export function ArchivedTokenTable({ tokens, onDeleted }: Props) {
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("archived");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  }

  const sorted = [...tokens].sort((a, b) => {
    let va: string = "";
    let vb: string = "";
    switch (sortKey) {
      case "name":      va = a.name.toLowerCase();         vb = b.name.toLowerCase(); break;
      case "status":    va = a.revoked ? "revoked" : "expired"; vb = b.revoked ? "revoked" : "expired"; break;
      case "created":   va = a.created_at ?? "";            vb = b.created_at ?? ""; break;
      case "archived":  va = a.revoked_at ?? "";            vb = b.revoked_at ?? ""; break;
      case "last_used": va = a.last_used_at ?? "";          vb = b.last_used_at ?? ""; break;
    }
    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  async function deletePermanently(id: string) {
    setDeleting(id);
    setError(null);
    try {
      await api.deleteArchivedToken(id);
      onDeleted(id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    } finally {
      setDeleting(null);
      setConfirmId(null);
    }
  }

  if (tokens.length === 0) {
    return <p className="archived-empty">No archived tokens.</p>;
  }

  function th(key: SortKey, label: string) {
    return (
      <th onClick={() => handleSort(key)} style={{ cursor: "pointer" }}>
        {label} <SortArrow col={key} sortKey={sortKey} sortDir={sortDir} />
      </th>
    );
  }

  return (
    <div>
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <table className="data-table archived-table">
        <thead>
          <tr>
            {th("name", "Name")}
            {th("status", "Status")}
            {th("created", "Created")}
            {th("archived", "Archived On")}
            {th("last_used", "Last Used")}
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => {
            const status = t.revoked ? "Revoked" : "Expired";
            return (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>
                  <span className={`badge ${status === "Revoked" ? "badge-red" : "badge-grey"}`}>
                    {status}
                  </span>
                </td>
                <td>{formatDate(t.created_at)}</td>
                <td>{formatDate(t.revoked_at)}</td>
                <td>{formatDate(t.last_used_at)}</td>
                <td>
                  {confirmId === t.id ? (
                    <span className="row-actions">
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => deletePermanently(t.id)}
                        disabled={deleting === t.id}
                      >
                        {deleting === t.id ? "..." : "Confirm delete"}
                      </button>
                      <button
                        className="btn btn-text btn-sm"
                        onClick={() => setConfirmId(null)}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => setConfirmId(t.id)}
                    >
                      Delete permanently
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
