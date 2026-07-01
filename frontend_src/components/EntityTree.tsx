import React, { useState, useCallback, useEffect } from "react";
import type { EntityTree, DomainTree, PermissionTree, NodeState } from "../types";
import { PermissionSelector } from "./PermissionSelector";
import { MesaProfileLink } from "./MesaProfileLink";
import { Modal } from "./Modal";
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
  // Which node the reveal targets. For "domain"/"device" the matching group
  // header is flashed and scrolled to (revealEntity is a representative child);
  // for "entity" the entity row itself is the target. Defaults to "entity".
  revealDepth?: "entity" | "device" | "domain";
  // Bumped by the parent on every reveal request so re-selecting the same
  // target still re-runs the expand/scroll effects.
  revealNonce?: number;
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
  globalHint: string | null;
  currentState: NodeState;
  onSaved: (tree: PermissionTree) => void;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function HintInput({ tokenId, entityId, currentHint, globalHint, currentState, onSaved, onGlobalHintsChange }: HintInputProps) {
  const [open, setOpen] = useState(false);
  const [allTokens, setAllTokens] = useState(true);
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function openModal() {
    setAllTokens(true);
    setValue(globalHint ?? "");
    setOpen(true);
  }

  function switchScope(all: boolean) {
    setAllTokens(all);
    // Load the target scope's saved hint if it has one, but never wipe the box:
    // switching scope must not discard text the admin is in the middle of writing.
    const target = all ? globalHint : currentHint;
    if (target) setValue(target);
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      if (allTokens) {
        const r = await api.setEntityHint(entityId, value.trim() || null);
        onGlobalHintsChange(r.entity_hints);
      } else {
        const tree = await api.patchEntityPermission(tokenId, entityId, {
          state: currentState,
          hint: value.trim() || null,
        });
        onSaved(tree);
      }
      setOpen(false);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Failed to save hint.");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    const hasHint = !!currentHint || !!globalHint;
    return (
      <button className="tree-hint-link" onClick={openModal}>
        {hasHint ? "Edit hint" : "Add hint"}
      </button>
    );
  }

  return (
    <Modal titleId="hint-modal-title" onClose={saving ? undefined : () => setOpen(false)}>
      <h3 className="modal-title" id="hint-modal-title">Entity hint</h3>
      <p className="hint-modal-entity">{entityId}</p>
        <input
          className="input"
          aria-label={`Hint for ${entityId}`}
          value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Hint for the AI agent..."
        maxLength={200}
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setOpen(false); }}
        autoFocus
      />
      <div className="hint-scope">
        <span className={allTokens ? "" : "hint-scope-active"}>This token only</span>
        <label className={`toggle-switch${saving ? " disabled" : ""}`}>
          <input
            type="checkbox"
            aria-label="Apply hint to all tokens"
            checked={allTokens}
            disabled={saving}
            onChange={(e) => switchScope(e.target.checked)}
          />
          <span className="toggle-switch-track" />
        </label>
        <span className={allTokens ? "hint-scope-active" : ""}>All tokens</span>
      </div>
      <p className="hint-scope-note">
        {allTokens
          ? "Saved globally: applies to every token that can see this entity."
          : "Saved for this token only. A token-level hint overrides the global one."}
      </p>
      {saveError && <p className="hint-modal-error" role="alert">{saveError}</p>}
      <div className="modal-actions">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button className="btn btn-text" onClick={() => setOpen(false)} disabled={saving}>Cancel</button>
      </div>
    </Modal>
  );
}

interface EntityRowProps {
  entityId: string;
  friendlyName: string | null;
  deviceId: string | null;
  domainKey: string;
  permissions: PermissionTree;
  tokenId: string;
  filterText: string;
  isGhost: boolean;
  onPermChange: (tree: PermissionTree) => void;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  revealEntity?: string;
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function EntityRow({
  entityId, friendlyName, deviceId, domainKey, permissions,
  tokenId, filterText, isGhost, onPermChange, onEntityClick, revealEntity, revealDepth, revealNonce, mesaProfileEntities, onOpenMesa, globalHints, onGlobalHintsChange,
}: EntityRowProps) {
  const entityNode = permissions.entities[entityId];
  const state: NodeState = entityNode?.state ?? "GREY";
  const effective = effectivePermission(entityId, domainKey, deviceId, permissions);
  const rowRef = React.useRef<HTMLDivElement>(null);
  const [permError, setPermError] = useState<string | null>(null);
  const isRevealed = (revealDepth ?? "entity") === "entity" && revealEntity === entityId;

  // When this row becomes the reveal target, scroll it into view and flash it.
  useEffect(() => {
    if (isRevealed && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [isRevealed, revealNonce]);

  if (filterText) {
    const q = filterText.toLowerCase();
    const matches = entityId.toLowerCase().includes(q) || (friendlyName?.toLowerCase().includes(q) ?? false);
    if (!matches) return null;
  }

  async function setEntityState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchEntityPermission(tokenId, entityId, {
        state: newState,
        hint: entityNode?.hint ?? null,
      });
      onPermChange(tree);
      onEntityClick?.(entityId, "entity");
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : "Failed to save permission.");
    }
  }

  return (
    <div ref={rowRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
      <span className="tree-spacer" />
      {onOpenMesa && !isGhost && (
        <MesaProfileLink entityId={entityId} exists={!!mesaProfileEntities?.has(entityId)} onOpen={onOpenMesa} />
      )}
      {onEntityClick ? (
        <button
          type="button"
          className="tree-name tree-cursor-pointer"
          onClick={() => onEntityClick(entityId, "entity")}
          title={`Simulate permissions for ${entityId}`}
        >
          <span className="tree-friendly">{friendlyName ?? entityId}</span>
          <span className="tree-entity-id">{entityId}</span>
        </button>
      ) : (
        <div className="tree-name">
          <div className="tree-friendly">{friendlyName ?? entityId}</div>
          <div className="tree-entity-id">{entityId}</div>
        </div>
      )}
      {isGhost && (
        <span className="tree-badge tree-badge-ghost" title="This entity no longer exists in Home Assistant.">ghost</span>
      )}
      <span className="tree-effective" title={`Effective: ${effective}`}>({effective})</span>
      {state !== "GREY" && (
        <HintInput
          tokenId={tokenId}
          entityId={entityId}
          currentHint={entityNode?.hint ?? null}
          globalHint={globalHints[entityId] ?? null}
          currentState={state}
          onSaved={onPermChange}
          onGlobalHintsChange={onGlobalHintsChange}
        />
      )}
      <PermissionSelector value={state} onChange={setEntityState} label={`Permission for ${friendlyName ?? entityId}`} />
      {permError && <span className="tree-perm-error" role="alert" title={permError}>Save failed</span>}
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
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function DeviceGroup({
  deviceId, deviceName, domainKey, entityIds, domainData,
  permissions, tokenId, filterText, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, revealDepth, revealNonce, mesaProfileEntities, onOpenMesa, globalHints, onGlobalHintsChange,
}: DeviceGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const [permError, setPermError] = useState<string | null>(null);
  const deviceNode = permissions.devices[deviceId];
  const state: NodeState = deviceNode?.state ?? "GREY";
  const effective = effectiveForNode("device", deviceId, domainKey, permissions);
  const isDynamic = state !== "GREY";
  const headerRef = React.useRef<HTMLDivElement>(null);
  const isRevealed = revealDepth === "device" && !!revealEntity && entityIds.includes(revealEntity);

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

  // Expand when an entity inside this device is the reveal target. Skip for
  // domain-depth reveals: those target the domain header, not every device.
  useEffect(() => {
    if (revealDepth !== "domain" && revealEntity && entityIds.includes(revealEntity)) setExpanded(true);
  }, [revealDepth, revealEntity, entityIds, revealNonce]);

  // When this device is the reveal target, scroll its header into view and flash it.
  useEffect(() => {
    if (isRevealed && headerRef.current) {
      headerRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [isRevealed, revealNonce]);

  // Collapse when collapseKey changes, but NOT on initial mount: this group is
  // lazily mounted when its domain expands (often due to a reveal), and a
  // mount-time collapse would immediately undo the reveal-driven expand above.
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDeviceState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchDevicePermission(tokenId, deviceId, { state: newState });
      onPermChange(tree);
      if (entityIds[0]) onEntityClick?.(entityIds[0], "device");
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : "Failed to save permission.");
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
    <div>
      <div ref={headerRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
        <button type="button" className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-label={expanded ? `Collapse ${deviceName}` : `Expand ${deviceName}`}>
          <span className={`collapsible-chevron${expanded ? " open" : ""}`} aria-hidden="true" />
        </button>
        <button type="button" className="tree-name tree-cursor-pointer" onClick={() => setExpanded((x) => !x)}>
          <span className="tree-friendly">{deviceName}</span>
        </button>
        {isDynamic && (
          <span className="tree-badge tree-badge-dynamic" title="New entities added to this device will automatically inherit this permission.">Dynamic</span>
        )}
        <span className="tree-effective" title={`Effective: ${effective}`}>({effective})</span>
        <PermissionSelector value={state} onChange={setDeviceState} label={`Permission for device ${deviceName}`} />
        {permError && <span className="tree-perm-error" role="alert" title={permError}>Save failed</span>}
      </div>
      {expanded && (
        <div className="tree-children-flat">
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
                filterText={filterText}
                isGhost={!allEntityIds.has(eid)}
                onPermChange={onPermChange}
                onEntityClick={onEntityClick}
                revealEntity={revealEntity}
                revealDepth={revealDepth}
                revealNonce={revealNonce}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={onOpenMesa}
                globalHints={globalHints}
                onGlobalHintsChange={onGlobalHintsChange}
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
  revealDepth?: "entity" | "device" | "domain";
  revealNonce?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
  globalHints: Record<string, string>;
  onGlobalHintsChange: (hints: Record<string, string>) => void;
}

function DomainGroup({
  domainKey, domainData, permissions, tokenId, filterText, allEntityIds, onPermChange, onEntityClick, collapseKey, revealEntity, revealDepth, revealNonce, mesaProfileEntities, onOpenMesa, globalHints, onGlobalHintsChange,
}: DomainGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const [permError, setPermError] = useState<string | null>(null);
  const domainNode = permissions.domains[domainKey];
  const state: NodeState = domainNode?.state ?? "GREY";
  const effective = effectiveForNode("domain", domainKey, domainKey, permissions);
  const isRisk = HIGH_RISK_DOMAINS.has(domainKey);
  const isIndirect = INDIRECT_CONTROL_DOMAINS.has(domainKey);
  const isDynamic = state !== "GREY";
  const headerRef = React.useRef<HTMLDivElement>(null);
  const isRevealed = revealDepth === "domain" && !!revealEntity && revealEntity.split(".")[0] === domainKey;

  useEffect(() => {
    if (filterText) setExpanded(true);
  }, [filterText]);

  // Expand when the reveal target lives in this domain.
  useEffect(() => {
    if (revealEntity && revealEntity.split(".")[0] === domainKey) setExpanded(true);
  }, [revealEntity, domainKey, revealNonce]);

  // When this domain is the reveal target, scroll its header into view and flash it.
  useEffect(() => {
    if (isRevealed && headerRef.current) {
      headerRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [isRevealed, revealNonce]);

  // Collapse when collapseKey changes, but NOT on initial mount (see DeviceGroup).
  const skipFirstCollapse = React.useRef(true);
  useEffect(() => {
    if (skipFirstCollapse.current) { skipFirstCollapse.current = false; return; }
    setExpanded(false);
  }, [collapseKey]);

  async function setDomainState(newState: NodeState) {
    setPermError(null);
    try {
      const tree = await api.patchDomainPermission(tokenId, domainKey, { state: newState });
      onPermChange(tree);
      const firstEntity = domainData.deviceless_entities[0]
        ?? Object.values(domainData.devices)[0]?.entities[0];
      if (firstEntity) onEntityClick?.(firstEntity, "domain");
    } catch (e: unknown) {
      setPermError(e instanceof Error ? e.message : "Failed to save permission.");
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
    <div className="tree-domain-group">
      <div ref={headerRef} className={`tree-node${isRevealed ? " tree-node-revealed" : ""}`}>
        <button type="button" className="tree-expand" onClick={() => setExpanded((x) => !x)} aria-label={expanded ? `Collapse ${domainKey}` : `Expand ${domainKey}`}>
          <span className={`collapsible-chevron${expanded ? " open" : ""}`} aria-hidden="true" />
        </button>
        <button type="button" className="tree-name tree-cursor-pointer" onClick={() => setExpanded((x) => !x)}>
          <span className="tree-friendly tree-domain-label">{domainKey}</span>
        </button>
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
        <PermissionSelector value={state} onChange={setDomainState} label={`Permission for domain ${domainKey}`} />
        {permError && <span className="tree-perm-error" role="alert" title={permError}>Save failed</span>}
      </div>
      {expanded && (
        <div className="tree-children">
          {domainData.deviceless_entities.length > 0 && (
            <div>
              {Object.keys(domainData.devices).length > 0 && (
                <div className="tree-node">
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
                      filterText={filterText}
                      isGhost={!allEntityIds.has(eid)}
                      onPermChange={onPermChange}
                      onEntityClick={onEntityClick}
                      revealEntity={revealEntity}
                      revealDepth={revealDepth}
                      revealNonce={revealNonce}
                      mesaProfileEntities={mesaProfileEntities}
                      onOpenMesa={onOpenMesa}
                      globalHints={globalHints}
                      onGlobalHintsChange={onGlobalHintsChange}
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
                revealDepth={revealDepth}
                revealNonce={revealNonce}
                mesaProfileEntities={mesaProfileEntities}
                onOpenMesa={onOpenMesa}
                globalHints={globalHints}
                onGlobalHintsChange={onGlobalHintsChange}
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
              filterText={filterText}
              isGhost={true}
              onPermChange={onPermChange}
              onEntityClick={onEntityClick}
              revealEntity={revealEntity}
              revealDepth={revealDepth}
              revealNonce={revealNonce}
              mesaProfileEntities={mesaProfileEntities}
              onOpenMesa={onOpenMesa}
              globalHints={globalHints}
              onGlobalHintsChange={onGlobalHintsChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function EntityTree({ tokenId, permissions, onPermissionsChange, onEntityClick, collapseKey, domainAllowlist, revealEntity, revealDepth, revealNonce, mesaProfileEntities, onOpenMesa }: Props) {
  const [tree, setTree] = useState<EntityTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [globalHints, setGlobalHints] = useState<Record<string, string>>({});

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
  useEffect(() => { api.getEntityHints().then((r) => setGlobalHints(r.entity_hints)).catch(() => undefined); }, []);

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
      <div aria-label="Entity permissions">
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
            revealDepth={revealDepth}
            revealNonce={revealNonce}
            mesaProfileEntities={mesaProfileEntities}
            onOpenMesa={onOpenMesa}
            globalHints={globalHints}
            onGlobalHintsChange={setGlobalHints}
          />
        ))}
      </div>
    </div>
  );
}
