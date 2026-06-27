import React, { useState, useEffect, useCallback, useRef } from "react";
import type { ResolveResult } from "../types";
import { api } from "../api";
import { MesaProfileLink } from "./MesaProfileLink";

type ResolveDepth = "entity" | "device" | "domain";

interface Props {
  tokenId: string;
  externalEntityId?: string;
  resolveDepth?: ResolveDepth;
  triggerVersion?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function filterPath(
  path: Array<{ level: string; state: string }>,
  depth: ResolveDepth,
): Array<{ level: string; state: string }> {
  if (depth === "entity") return path;
  if (depth === "device") return path.filter((s) => !s.level.startsWith("entity:"));
  return path.filter((s) => !s.level.startsWith("device:") && !s.level.startsWith("entity:"));
}

function effectiveFromPath(steps: Array<{ level: string; state: string }>): string {
  if (steps.some((s) => s.state === "RED")) return "DENY";
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].state === "GREEN") return "WRITE";
    if (steps[i].state === "YELLOW") return "READ";
  }
  return "NO_ACCESS";
}

const EFFECTIVE_CLASS: Record<string, string> = {
  WRITE: "state-GREEN",
  READ: "state-YELLOW",
  DENY: "state-RED",
  NO_ACCESS: "state-GREY",
  NOT_FOUND: "state-GREY",
};

export function PermissionSimulator({ tokenId, externalEntityId, resolveDepth = "entity", triggerVersion, mesaProfileEntities, onOpenMesa }: Props) {
  const [entityInput, setEntityInput] = useState(externalEntityId ?? "");
  const [result, setResult] = useState<ResolveResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const entityInputRef = useRef(entityInput);
  entityInputRef.current = entityInput;
  const externalUpdateRef = useRef(false);
  const genRef = useRef(0);

  const simulate = useCallback(async (eid: string) => {
    if (!eid.trim()) return;
    const gen = ++genRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await api.resolve(tokenId, eid.trim());
      if (gen !== genRef.current) return;
      setResult(data);
    } catch (e: unknown) {
      if (gen !== genRef.current) return;
      setError(e instanceof Error ? e.message : "Simulation failed.");
      setResult(null);
    } finally {
      if (gen !== genRef.current) return;
      setLoading(false);
    }
  }, [tokenId]);

  // External entity set: update input and suppress debounce (simulation handled by Effect 2)
  useEffect(() => {
    if (externalEntityId) {
      externalUpdateRef.current = true;
      setEntityInput(externalEntityId);
    }
  }, [externalEntityId]);

  // Simulate when entity selection or permissions version changes.
  // Uses externalEntityId directly (not stale entityInputRef) when it just changed,
  // preventing the race where entityInputRef still holds the previous entity.
  useEffect(() => {
    const entity = externalEntityId?.trim() || entityInputRef.current.trim();
    if (!entity) return;
    if (!triggerVersion && !externalEntityId) return;
    simulate(entity);
  }, [triggerVersion, externalEntityId, simulate]);

  // Manual typing: debounce 600ms (skip if input was set externally)
  useEffect(() => {
    if (!entityInput.trim()) return;
    if (externalUpdateRef.current) {
      externalUpdateRef.current = false;
      return;
    }
    const timer = setTimeout(() => simulate(entityInput), 600);
    return () => clearTimeout(timer);
  }, [entityInput, simulate]);

  return (
    <div>
      <input
        className="input"
        placeholder="entity_id (e.g. light.kitchen)"
        value={entityInput}
        onChange={(e) => setEntityInput(e.target.value)}
      />
      {loading && !result && <div className="sim-loading">Simulating...</div>}
      {error && <div className="banner banner-error mt-6">{error}</div>}
      {result && (() => {
        const visiblePath = filterPath(result.resolution_path, resolveDepth);
        const effective = effectiveFromPath(visiblePath);
        return (
          <div className="sim-path">
            {visiblePath.map((step, i) => (
              <div key={i} className="sim-step">
                <span className="sim-resolution-level">{step.level}</span>
                {" -> "}
                <span className={`state-${step.state}`}>
                  {step.state}
                </span>
              </div>
            ))}
            <div className="sim-result">
              {"Result: "}
              <span className={EFFECTIVE_CLASS[effective] ?? "state-GREY"}>
                {effective}
              </span>
            </div>
            {onOpenMesa && entityInput.includes(".") && (
              <div className="sim-mesa-row">
                <MesaProfileLink
                  entityId={entityInput.trim()}
                  exists={!!mesaProfileEntities?.has(entityInput.trim())}
                  onOpen={onOpenMesa}
                />
                <span className="sim-mesa-label">
                  {mesaProfileEntities?.has(entityInput.trim()) ? "View MESA profile" : "No MESA profile - create one"}
                </span>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
