import React, { useState, useEffect, useCallback, useMemo } from "react";
import type { AuditEntry, TokenRecord, Outcome } from "../types";
import { api } from "../api";
import { AuditTable } from "../components/AuditTable";
import { RefreshIcon } from "../index";

interface Props {
  tokens: TokenRecord[];
}

type TimeWindow = "" | "5m" | "1h" | "24h" | "1w";

const TIME_WINDOW_MS: Record<Exclude<TimeWindow, "">, number> = {
  "5m": 5 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "1w": 7 * 24 * 60 * 60 * 1000,
};

const PAGE_SIZE = 100;

export function AuditView({ tokens }: Props) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [outcomeFilter, setOutcomeFilter] = useState<Outcome | "">("");
  const [tokenFilter, setTokenFilter] = useState("");
  const [timeWindow, setTimeWindow] = useState<TimeWindow>("");
  const [methodFilter, setMethodFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [ipFilter, setIpFilter] = useState("");

  // Current id -> name map so renamed tokens show their current name on existing
  // audit rows (falls back to the row's stored name for archived/admin entries).
  const tokenNames = useMemo(
    () => Object.fromEntries(tokens.map((t) => [t.id, t.name])),
    [tokens],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getAudit({ limit: 500 });
      setEntries(data);
      setPage(0);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = entries.filter((e) => {
    if (outcomeFilter && e.outcome !== outcomeFilter) return false;
    if (tokenFilter && e.token_id !== tokenFilter) return false;
    if (timeWindow) {
      const threshold = Date.now() - TIME_WINDOW_MS[timeWindow];
      if (new Date(e.timestamp).getTime() < threshold) return false;
    }
    if (methodFilter && !e.method.toLowerCase().includes(methodFilter.toLowerCase())) return false;
    if (resourceFilter && !e.resource.toLowerCase().includes(resourceFilter.toLowerCase())) return false;
    if (ipFilter && !e.client_ip.includes(ipFilter)) return false;
    return true;
  });

  return (
    <div className="view-root">
      <div className="card">
        <div className="filter-row">
          <select
            aria-label="Filter by outcome"
            className="input input-auto"
            value={outcomeFilter}
            onChange={(e) => { setOutcomeFilter(e.target.value as Outcome | ""); setPage(0); }}
          >
            <option value="">All outcomes</option>
            <option value="allowed">Allowed</option>
            <option value="denied">Denied</option>
            <option value="not_found">Not Found</option>
            <option value="rate_limited">Rate Limited</option>
            <option value="not_implemented">Not Implemented</option>
          </select>
          <select
            aria-label="Filter by token"
            className="input input-auto"
            value={tokenFilter}
            onChange={(e) => { setTokenFilter(e.target.value); setPage(0); }}
          >
            <option value="">All tokens</option>
            {tokens.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <select
            aria-label="Filter by time window"
            className="input input-auto"
            value={timeWindow}
            onChange={(e) => { setTimeWindow(e.target.value as TimeWindow); setPage(0); }}
          >
            <option value="">All time</option>
            <option value="1w">Past week</option>
            <option value="24h">Past 24 hours</option>
            <option value="1h">Past hour</option>
            <option value="5m">Past 5 minutes</option>
          </select>
          <input
            className="input"
            aria-label="Filter by method"
            placeholder="Method..."
            value={methodFilter}
            onChange={(e) => { setMethodFilter(e.target.value); setPage(0); }}
          />
          <input
            className="input"
            aria-label="Filter by resource"
            placeholder="Resource..."
            value={resourceFilter}
            onChange={(e) => { setResourceFilter(e.target.value); setPage(0); }}
          />
          <input
            className="input"
            aria-label="Filter by IP address"
            placeholder="IP..."
            value={ipFilter}
            onChange={(e) => { setIpFilter(e.target.value); setPage(0); }}
          />
          <div className="filter-row-right">
            <button
              className="btn btn-ghost btn-sm btn-icon"
              onClick={load}
              title="Refresh"
              aria-label="Refresh audit log"
            >
              <RefreshIcon />
            </button>
          </div>
        </div>

        <AuditTable
          entries={filtered}
          loading={loading}
          page={page}
          pageSize={PAGE_SIZE}
          onPageChange={setPage}
          tokenNames={tokenNames}
        />
      </div>
    </div>
  );
}
