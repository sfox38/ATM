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

const STATES: { state: NodeState; label: string }[] = [
  { state: "YELLOW", label: "Read" },
  { state: "GREEN", label: "Write" },
  { state: "RED", label: "Deny" },
  { state: "GREY", label: "Remove grant" },
];

export function AreaPicker({ tokenId, entityTree, onDone, onClose }: Props) {
  const [selectedArea, setSelectedArea] = useState<string>("");
  const [selectedState, setSelectedState] = useState<NodeState>("GREEN");
  const [applying, setApplying] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const areas = useMemo(() => {
    const map = new Map<string, { id: string; name: string; count: number }>();
    for (const domain of Object.values(entityTree)) {
      for (const detail of Object.values(domain.entity_details)) {
        if (detail.area_id && detail.area_name) {
          const existing = map.get(detail.area_id);
          if (existing) {
            existing.count++;
          } else {
            map.set(detail.area_id, { id: detail.area_id, name: detail.area_name, count: 1 });
          }
        }
      }
    }
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [entityTree]);

  const affectedEntities = useMemo(() => {
    if (!selectedArea) return [];
    const result: string[] = [];
    for (const domain of Object.values(entityTree)) {
      for (const detail of Object.values(domain.entity_details)) {
        if (detail.area_id === selectedArea) result.push(detail.entity_id);
      }
    }
    return result;
  }, [selectedArea, entityTree]);

  async function apply() {
    if (!selectedArea || affectedEntities.length === 0) return;
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
      setError(e instanceof Error ? e.message : "Failed to apply area permissions.");
    } finally {
      setApplying(false);
      setProgress(null);
    }
  }

  return (
    <Modal titleId="area-picker-title" onClose={applying ? undefined : onClose}>
      <h3 className="modal-title" id="area-picker-title">Select by Area</h3>

      <div className="banner banner-warn">
          This grants access to the entities currently in the selected area. Entities added to this area in the future will not be automatically included. Use a domain-level grant for dynamic coverage.
        </div>

        <div className="field">
          <label>Area</label>
          <select
            className="input"
            value={selectedArea}
            onChange={(e) => setSelectedArea(e.target.value)}
          >
            <option value="">-- Select area --</option>
            {areas.map((area) => (
              <option key={area.id} value={area.id}>
                {area.name} ({area.count} {area.count === 1 ? "entity" : "entities"})
              </option>
            ))}
          </select>
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

        {selectedArea && (
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
          disabled={applying || !selectedArea || affectedEntities.length === 0}
        >
          {applying ? "Applying..." : "Apply"}
        </button>
        <button className="btn btn-text" onClick={onClose} disabled={applying}>Cancel</button>
      </div>
    </Modal>
  );
}
