import React, { useState, useCallback, useEffect } from "react";
import type { EntityTree, DomainTree, PermissionTree, NodeState } from "../types";
import { PermissionSelector } from "./PermissionSelector";
import { MesaProfileLink } from "./MesaProfileLink";
import { api } from "../api";
import { HIGH_RISK_DOMAINS } from "../utils";

const INDIRECT_CONTROL_DOMAINS = new Set([
  "automation", "script", "scene",
]);

interface Props {
  tokenId: string;
  permissions: PermissionTree;
  onPermissionsChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  collapseKey?: number;
  // When set, only these domains render. Used by the onboarding wizard to show
  // a single, less-daunting domain (e.g. ["light"]).
  domainAllowlist?: string[];
  // When set, the tree expands the path to this entity and scrolls it into view
  // (e.g. after selecting it in the Permission Summary card).
  revealEntity?: string;
  // Entities that have a MESA profile, and the handler to open one. When given,
  // each entity row shows a "MESA"/"+" jump to its profile.
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function effectivePermission(
  entityId: string,
  domainKey: string,
  deviceId: string | null,
  permissions: PermissionTree,
): string {
  const eState = permissions.entities[entityId]?.state ?? "GREY";
  const dState = deviceId ? (permissions.devices[deviceId]?.state ?? "GREY") : "GREY";
  const domState = permissions.domains[domainKey]?.state ?? "GREY";

  if (eState === "RED" || dState === "RED" || domState === "RED") return "DENY";
  if (eState === "GREEN") return "WRITE";
  if (eState === "YELLOW") return "READ";
  if (dState === "GREEN") return "WRITE";
  if (dState === "YELLOW") return "READ";
  if (domState === "GREEN") return "WRITE";
  if (domState === "YELLOW") return "READ";
  return "NO_ACCESS";
}

function effectiveForNode(
  nodeType: "domain" | "device",
  nodeId: string,
  domainKey: string,
  permissions: PermissionTree,
): string {
  if (nodeType === "domain") {
    const s = permissions.domains[domainKey]?.state ?? "GREY";
    if (s === "GREEN") return "WRITE";
    if (s === "YELLOW") return "READ";
    if (s === "RED") return "DENY";
    return "NO_ACCESS";
  }
  const dState = permissions.devices[nodeId]?.state ?? "GREY";
  const domState = permissions.domains[domainKey]?.state ?? "GREY";
  if (dState === "RED" || domState === "RED") return "DENY";
  if (dState === "GREEN") return "WRITE";
  if (dState === "YELLOW") return "READ";
  if (domState === "GREEN") return "WRITE";
  if (domState === "YELLOW") return "READ";
  return "NO_ACCESS";
}

interface HintInputProps {
  tokenId: string;
  entityId: string;
  currentHint: string | null;
  currentState: NodeState;
  onSaved: (tree: PermissionTree) => void;
}

function HintInput({ tokenId, entityId, currentHint, currentState, onSaved }: HintInputProps) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(currentHint ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(currentHint ?? "");
  }, [currentHint]);

  async function save() {
    setSaving(true);
    try {
      const tree = await api.patchEntityPermission(tokenId, entityId, {
        state: currentState,
        hint: value.trim() || null,
      });
      onSaved(tree);
      setOpen(false);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <button className="tree-hint-link" onClick={() => setOpen(true)}>
        {currentHint ? "Edit hint" : "Add hint"}
      </button>
    );
  }

  return (
    <span className="hint-input-row">
      <input
        className="tree-hint-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Hint for LLM..."
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setOpen(false); }}
        autoFocus
      />
      <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
        {saving ? "..." : "Save"}
      </button>
      <button className="btn btn-text btn-sm" onClick={() => setOpen(false)}>Cancel</button>
    </span>
  );
}

interface EntityRowProps {
  entityId: string;
  friendlyName: string | null;
  deviceId: string | null;
  domainKey: string;
  permissions: PermissionTree;
  tokenId: string;
  indent: number;
  filterText: string;
  isGhost: boolean;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  revealEntity?: string;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function EntityRow({
  entityId, friendlyName, deviceId, domainKey, permissions,
  tokenId, indent, filterText, isGhost, onPermChange, onEntityClick, revealEntity, mesaProfileEntities, onOpenMesa,
}: EntityRowProps) {
  const entityNode = permissions.entities[entityId];
  const state: NodeState = entityNode?.state ?? "GREY";
  const effective = effectivePermission(entityId, domainKey, deviceId, permissions);
  const rowRef = React.useRef<HTMLDivElement>(null);
  const isRevealed = revealEntity === entityId;

  // When this row becomes the reveal target, scroll it into view and flash it.
  useEffect(() => {
    if (isRevealed && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [isRevealed]);

  if (filterText) {
    const q = filterText.toLowerCase();
    const matches = entityId.toLowerCase().includes(q) || (friendlyName?.toLowerCase().includes(q) ?? false);
    if (!matches) return null;
  }

  async function setEntityState(newState: NodeState) {
    try {
      const tree = await api.patchEntityPermission(tokenId, entityId, {
        state: newState,
        hint: entityNode?.hint ?? null,
      });
      onPermChange(tree);
      onEntityClick?.(entityId, "entity");
    } catch {
      // ignore
    }
  }

  return (
    <div ref={rowRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`} role="treeitem" aria-label={friendlyName ?? entityId} style={{ paddingLeft: `${indent * 20 + 6}px` }}>
      <span className="tree-spacer" />
      {onOpenMesa && !isGhost && (
        <MesaProfileLink entityId={entityId} exists={!!mesaProfileEntities?.has(entityId)} onOpen={onOpenMesa} />
      )}
      <div
        className={`tree-name${onEntityClick ? " tree-cursor-pointer" : ""}`}
        onClick={() => onEntityClick?.(entityId, "entity")}
        title={onEntityClick ? `Simulate permissions for ${entityId}` : undefined}
      >
        <div className="tree-friendly">{friendlyName ?? entityId}</div>
        <div className="tree-entity-id">{entityId}</div>
      </div>
      {isGhost && (
        <span className="tree-badge tree-badge-ghost" title="This entity no longer exists in Home Assistant.">ghost</span>
      )}
      <span className="tree-effective" title={`Effective: ${effective}`}>({effective})</span>
      {state !== "GREY" && (
        <HintInput
          tokenId={tokenId}
          entityId={entityId}
          currentHint={entityNode?.hint ?? null}
          currentState={state}
          onSaved={onPermChange}
        />
      )}
      <PermissionSelector value={state} onChange={setEntityState} />
    </div>
  );
}

interface DeviceGroupProps {
  deviceId: string;
  deviceName: string;
  domainKey: string;
  entityIds: string[];
  domainData: DomainTree;
  permissions: PermissionTree;
  tokenId: string;
  filterText: string;
  allEntityIds: Set<string>;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  collapseKey?: number;
  revealEntity?: string;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function DeviceGroup({
  deviceId, deviceName, domainKey, entityIds, domainData,
  permissions, tokenId, filterText, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, mesaProfileEntities, onOpenMesa,
}: DeviceGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const deviceNode = permissions.devices[deviceId];
  const state: NodeState = deviceNode?.state ?? "GREY";
  const effective = effectiveForNode("device", deviceId, domainKey, permissions);
  const isDynamic = state !== "GREY";

  // Entities sorted by friendly name (falling back to entity id).
  const sortedEntityIds = [...entityIds].sort((a, b) => {
    const an = domainData.entity_details[a]?.friendly_name ?? a;
    const bn = domainData.entity_details[b]?.friendly_name ?? b;
    return an.localeCompare(bn);
  });

  // Expand if filter matches
  useEffect(() => {
    if (filterText) setExpanded(true);
  }, [filterText]);

  // Expand when an entity inside this device is the reveal target.
  useEffect(() => {
    if (revealEntity && entityIds.includes(revealEntity)) setExpanded(true);
  }, [revealEntity, entityIds]);

  // Collapse when collapseKey changes, but NOT on initial mount: this group is
  // lazily mounted when its domain expands (often due to a reveal), and a
  // mount-time collapse would immediately undo the reveal-driven expand above.
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDeviceState(newState: NodeState) {
    try {
      const tree = await api.patchDevicePermission(tokenId, deviceId, { state: newState });
      onPermChange(tree);
      if (entityIds[0]) onEntityClick?.(entityIds[0], "device");
    } catch {
      // ignore
    }
  }

  // Check if any child would be visible under filter
  const hasVisibleChild = filterText
    ? entityIds.some((eid) => {
        const detail = domainData.entity_details[eid];
        const q = filterText.toLowerCase();
        return eid.toLowerCase().includes(q) || (detail?.friendly_name?.toLowerCase().includes(q) ?? false);
      })
    : true;

  if (filterText && !hasVisibleChild && !deviceName.toLowerCase().includes(filterText.toLowerCase())) return null;

  return (
    <div role="treeitem" aria-expanded={expanded} aria-label={deviceName}>
      <div className="tree-node tree-device-indent">
        <button className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-label={expanded ? `Collapse ${deviceName}` : `Expand ${deviceName}`}>
          {expanded ? "v" : ">"}
        </button>
        <div className="tree-name tree-cursor-pointer" onClick={() => setExpanded((x) => !x)}>
          <span className="tree-friendly">{deviceName}</span>
        </div>
        {isDynamic && (
          <span className="tree-badge tree-badge-dynamic" title="New entities added to this device will automatically inherit this permission.">Dynamic</span>
        )}
        <span className="tree-effective" title={`Effective: ${effective}`}>({effective})</span>
        <PermissionSelector value={state} onChange={setDeviceState} />
      </div>
      {expanded && (
        <div className="tree-children" role="group">
          {sortedEntityIds.map((eid) => {
            const detail = domainData.entity_details[eid];
            return (
              <EntityRow
                key={eid}
                entityId={eid}
                friendlyName={detail?.friendly_name ?? null}
                deviceId={deviceId}
                domainKey={domainKey}
                permissions={permissions}
                tokenId={tokenId}
                indent={2}
                filterText={filterText}
                isGhost={!allEntityIds.has(eid)}
                onPermChange={onPermChange}
                onEntityClick={onEntityClick}
                revealEntity={revealEntity}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={onOpenMesa}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

interface DomainGroupProps {
  domainKey: string;
  domainData: DomainTree;
  permissions: PermissionTree;
  tokenId: string;
  filterText: string;
  allEntityIds: Set<string>;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  collapseKey?: number;
  revealEntity?: string;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function DomainGroup({
  domainKey, domainData, permissions, tokenId, filterText, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, mesaProfileEntities, onOpenMesa,
}: DomainGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const domainNode = permissions.domains[domainKey];
  const state: NodeState = domainNode?.state ?? "GREY";
  const effective = effectiveForNode("domain", domainKey, domainKey, permissions);
  const isRisk = HIGH_RISK_DOMAINS.has(domainKey);
  const isIndirect = INDIRECT_CONTROL_DOMAINS.has(domainKey);
  const isDynamic = state !== "GREY";

  useEffect(() => {
    if (filterText) setExpanded(true);
  }, [filterText]);

  // Expand when the reveal target lives in this domain.
  useEffect(() => {
    if (revealEntity && revealEntity.split(".")[0] === domainKey) setExpanded(true);
  }, [revealEntity, domainKey]);

  // Collapse when collapseKey changes, but NOT on initial mount (see DeviceGroup).
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDomainState(newState: NodeState) {
    try {
      const tree = await api.patchDomainPermission(tokenId, domainKey, { state: newState });
      onPermChange(tree);
      const firstEntity = domainData.deviceless_entities[0]
        ?? Object.values(domainData.devices)[0]?.entities[0];
      if (firstEntity) onEntityClick?.(firstEntity, "domain");
    } catch {
      // ignore
    }
  }

  const ghostEntityIds = Object.keys(permissions.entities).filter(
    (eid) => eid.startsWith(`${domainKey}.`) && !allEntityIds.has(eid),
  );

  const hasVisible = filterText
    ? (domainKey.toLowerCase().includes(filterText.toLowerCase()) ||
       Object.values(domainData.entity_details).some((d) => {
         const q = filterText.toLowerCase();
         return d.entity_id.toLowerCase().includes(q) || (d.friendly_name?.toLowerCase().includes(q) ?? false);
       }))
    : true;

  if (filterText && !hasVisible) return null;

  return (
    <div className="tree-domain-group" role="treeitem" aria-expanded={expanded} aria-label={domainKey}>
      <div className="tree-node">
        <button className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-label={expanded ? `Collapse ${domainKey}` : `Expand ${domainKey}`}>
          {expanded ? "v" : ">"}
        </button>
        <div className="tree-name tree-cursor-pointer" onClick={() => setExpanded((x) => !x)}>
          <span className="tree-friendly tree-domain-label">{domainKey}</span>
        </div>
        {isDynamic && (
          <span className="tree-badge tree-badge-dynamic" title="New entities added to this domain will automatically inherit this permission.">Dynamic</span>
        )}
        {isRisk && (
          <span className="tree-badge tree-badge-risk" title="High-risk domain. Granting WRITE here gives access to broad system operations.">!</span>
        )}
        {isIndirect && (
          <span className="tree-badge tree-badge-risk" title="WRITE access here can indirectly control entities outside this token's permission scope. Triggered automations, scripts, and scenes run under Home Assistant's full context.">!</span>
        )}
        <span className="tree-effective" title={`Effective: ${effective}`}>({effective})</span>
        <PermissionSelector value={state} onChange={setDomainState} />
      </div>
      {expanded && (
        <div className="tree-children" role="group">
          {domainData.deviceless_entities.length > 0 && (
            <div>
              {Object.keys(domainData.devices).length > 0 && (
                <div className="tree-node tree-device-indent">
                  <span className="tree-spacer" />
                  <span className="tree-name tree-orphan-label">
                    Deviceless Entities
                  </span>
                </div>
              )}
              {[...domainData.deviceless_entities]
                .sort((a, b) => (domainData.entity_details[a]?.friendly_name ?? a).localeCompare(domainData.entity_details[b]?.friendly_name ?? b))
                .map((eid) => {
                  const detail = domainData.entity_details[eid];
                  return (
                    <EntityRow
                      key={eid}
                      entityId={eid}
                      friendlyName={detail?.friendly_name ?? null}
                      deviceId={null}
                      domainKey={domainKey}
                      permissions={permissions}
                      tokenId={tokenId}
                      indent={1}
                      filterText={filterText}
                      isGhost={!allEntityIds.has(eid)}
                      onPermChange={onPermChange}
                      onEntityClick={onEntityClick}
                      revealEntity={revealEntity}
                      mesaProfileEntities={mesaProfileEntities}
                      onOpenMesa={onOpenMesa}
                    />
                  );
                })}
            </div>
          )}
          {Object.entries(domainData.devices)
            .sort(([, a], [, b]) => a.name.localeCompare(b.name))
            .map(([deviceId, device]) => (
              <DeviceGroup
                key={deviceId}
                deviceId={deviceId}
                deviceName={device.name}
                domainKey={domainKey}
                entityIds={device.entities}
                domainData={domainData}
                permissions={permissions}
                tokenId={tokenId}
                filterText={filterText}
                allEntityIds={allEntityIds}
                onPermChange={onPermChange}
                onEntityClick={onEntityClick}
                collapseKey={collapseKey}
                revealEntity={revealEntity}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={onOpenMesa}
              />
            ))}
          {[...ghostEntityIds].sort().map((eid) => (
            <EntityRow
              key={eid}
              entityId={eid}
              friendlyName={null}
              deviceId={null}
              domainKey={domainKey}
              permissions={permissions}
              tokenId={tokenId}
              indent={1}
              filterText={filterText}
              isGhost={true}
              onPermChange={onPermChange}
              onEntityClick={onEntityClick}
              revealEntity={revealEntity}
              mesaProfileEntities={mesaProfileEntities}
              onOpenMesa={onOpenMesa}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function EntityTree({ tokenId, permissions, onPermissionsChange, onEntityClick, collapseKey, domainAllowlist, revealEntity, mesaProfileEntities, onOpenMesa }: Props) {
  const [tree, setTree] = useState<EntityTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const loadTree = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getEntityTree(force);
      setTree(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load entity tree.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadTree(); }, [loadTree]);

  const allEntityIds = React.useMemo(() => {
    if (!tree) return new Set<string>();
    const ids = new Set<string>();
    for (const domain of Object.values(tree)) {
      for (const eid of Object.keys(domain.entity_details)) ids.add(eid);
    }
    return ids;
  }, [tree]);

  if (loading) return <div className="loading-wrap"><div className="spinner" /></div>;
  if (error) return <div className="banner banner-error">{error}</div>;
  if (!tree) return null;

  const domainKeys = Object.keys(tree)
    .filter((d) => !domainAllowlist || domainAllowlist.includes(d))
    .sort();

  return (
    <div>
      <div className="tree-filter">
        <input
          className="input"
          placeholder="Filter entities..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter entities"
        />
        <button className="reload-btn" onClick={() => loadTree(true)} title="Reload entity tree from HA" aria-label="Reload entity tree">
          Reload
        </button>
      </div>
      <div role="tree" aria-label="Entity permissions">
        {domainKeys.map((domain) => (
          <DomainGroup
            key={domain}
            domainKey={domain}
            domainData={tree[domain]}
            permissions={permissions}
            tokenId={tokenId}
            filterText={filter}
            allEntityIds={allEntityIds}
            onPermChange={onPermissionsChange}
            onEntityClick={onEntityClick}
            collapseKey={collapseKey}
            revealEntity={revealEntity}
            mesaProfileEntities={mesaProfileEntities}
            onOpenMesa={onOpenMesa}
          />
        ))}
      </div>
    </div>
  );
}
