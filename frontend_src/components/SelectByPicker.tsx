import React, { useState, useMemo } from "react";
import type { EntityTree, NodeState } from "../types";
import { api } from "../api";
import { Modal } from "./Modal";

interface Props {
  tokenId: string;
  entityTree: EntityTree;
  onDone: () => void;
  onClose: () => void;
}

type Mode = "area" | "label";

const STATES: { state: NodeState; label: string }[] = [
  { state: "YELLOW", label: "Read" },
  { state: "GREEN", label: "Write" },
  { state: "RED", label: "Deny" },
  { state: "GREY", label: "Remove grant" },
];

export function SelectByPicker({ tokenId, entityTree, onDone, onClose }: Props) {
  const [mode, setMode] = useState<Mode>("area");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [selectedState, setSelectedState] = useState<NodeState>("GREEN");
  const [applying, setApplying] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Groups of (id, name, entity count) for the current mode, sorted by name.
  const groups = useMemo(() => {
    const map = new Map<string, { id: string; name: string; count: number }>();
    for (const domain of Object.values(entityTree)) {
      for (const detail of Object.values(domain.entity_details)) {
        if (mode === "area") {
          if (detail.area_id && detail.area_name) {
            const existing = map.get(detail.area_id);
            if (existing) existing.count++;
            else map.set(detail.area_id, { id: detail.area_id, name: detail.area_name, count: 1 });
          }
        } else {
          for (const label of detail.labels) {
            const existing = map.get(label.id);
            if (existing) existing.count++;
            else map.set(label.id, { id: label.id, name: label.name, count: 1 });
          }
        }
      }
    }
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [entityTree, mode]);

  const affectedEntities = useMemo(() => {
    if (!selectedKey) return [];
    const result: string[] = [];
    for (const domain of Object.values(entityTree)) {
      for (const detail of Object.values(domain.entity_details)) {
        const inGroup =
          mode === "area"
            ? detail.area_id === selectedKey
            : detail.labels.some((l) => l.id === selectedKey);
        if (inGroup) result.push(detail.entity_id);
      }
    }
    return result;
  }, [selectedKey, entityTree, mode]);

  function switchMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    setSelectedKey("");
  }

  async function apply() {
    if (!selectedKey || affectedEntities.length === 0) return;
    setApplying(true);
    setError(null);
    let done = 0;
    try {
      for (const entityId of affectedEntities) {
        setProgress(`${done + 1} / ${affectedEntities.length}`);
        await api.patchEntityPermission(tokenId, entityId, { state: selectedState });
        done++;
      }
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to apply permissions.");
    } finally {
      setApplying(false);
      setProgress(null);
    }
  }

  const noun = mode === "area" ? "area" : "label";

  return (
    <Modal titleId="area-picker-title" onClose={applying ? undefined : onClose}>
      <h3 className="modal-title" id="area-picker-title">Select by Area or Label</h3>

      <div className="wizard-tabs" role="tablist" aria-label="Group by">
        {(["area", "label"] as Mode[]).map((m) => (
          <button
            key={m}
            role="tab"
            aria-selected={mode === m}
            className={`wizard-tab${mode === m ? " wizard-tab-active" : ""}`}
            onClick={() => switchMode(m)}
            disabled={applying}
          >
            {m === "area" ? "Area" : "Label"}
          </button>
        ))}
      </div>

      <div className="banner banner-warn">
          This grants access to the entities currently in the selected {noun}. Entities added to this {noun} in the future will not be automatically included. Use a domain-level grant for dynamic coverage.
        </div>

        <div className="field">
          <label>{mode === "area" ? "Area" : "Label"}</label>
          <select
            className="input"
            value={selectedKey}
            onChange={(e) => setSelectedKey(e.target.value)}
          >
            <option value="">-- Select {noun} --</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name} ({g.count} {g.count === 1 ? "entity" : "entities"})
              </option>
            ))}
          </select>
          {groups.length === 0 && (
            <p className="area-picker-summary">No {noun}s are defined on any accessible entity.</p>
          )}
        </div>

        <div className="field">
          <label>Permission to apply</label>
          <select
            className="input"
            value={selectedState}
            onChange={(e) => setSelectedState(e.target.value as NodeState)}
          >
            {STATES.map((s) => (
              <option key={s.state} value={s.state}>{s.label}</option>
            ))}
          </select>
        </div>

        {selectedKey && (
          <p className="area-picker-summary">
            This will set {affectedEntities.length} {affectedEntities.length === 1 ? "entity" : "entities"} to {selectedState}.
          </p>
        )}

        {error && <div className="banner banner-error">{error}</div>}
        {progress && <p className="area-picker-progress">Applying... {progress}</p>}

      <div className="modal-actions">
        <button
          className="btn btn-primary"
          onClick={apply}
          disabled={applying || !selectedKey || affectedEntities.length === 0}
        >
          {applying ? "Applying..." : "Apply"}
        </button>
        <button className="btn btn-text" onClick={onClose} disabled={applying}>Cancel</button>
      </div>
    </Modal>
  );
}
