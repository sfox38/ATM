import React, { useState, useMemo } from "react";
import type { PermissionTree, NodeState, EntityTree } from "../types";
import { MesaProfileLink } from "./MesaProfileLink";

interface Props {
  permissions: PermissionTree;
  entityTree?: EntityTree | null;
  onEntityClick?: (entityId: string, depth?: "entity" | "device" | "domain") => void;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

const STATE_LABEL: Record<NodeState, string> = {
  GREY: "INHERIT",
  YELLOW: "READ",
  GREEN: "WRITE",
  RED: "DENY",
};

const STATE_CLASS: Record<NodeState, string> = {
  GREY: "state-GREY",
  YELLOW: "state-YELLOW",
  GREEN: "state-GREEN",
  RED: "state-RED",
};

type NodeType = "domain" | "device" | "entity";
type SortCol = "type" | "friendly" | "id" | "state";
type SortDir = "asc" | "desc";

const TYPE_ORDER: Record<NodeType, number> = { domain: 0, device: 1, entity: 2 };
const STATE_ORDER: Record<NodeState, number> = { GREEN: 0, YELLOW: 1, RED: 2, GREY: 3 };

const TYPE_LABEL: Record<NodeType, string> = {
  domain: "Domain",
  device: "Device",
  entity: "Entity",
};

interface SummaryItem {
  id: string;
  type: NodeType;
  friendlyName: string;
  state: NodeState;
}

function buildLookups(entityTree: EntityTree | null | undefined): {
  entityNames: Map<string, string>;
  deviceNames: Map<string, string>;
  domainFirstEntity: Map<string, string>;
  deviceFirstEntity: Map<string, string>;
  entityToDevice: Map<string, string>;
  deviceDomain: Map<string, string>;
} {
  const entityNames = new Map<string, string>();
  const deviceNames = new Map<string, string>();
  const domainFirstEntity = new Map<string, string>();
  const deviceFirstEntity = new Map<string, string>();
  const entityToDevice = new Map<string, string>();
  const deviceDomain = new Map<string, string>();
  if (!entityTree) return { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain };
  for (const [domainKey, domain] of Object.entries(entityTree)) {
    for (const [eid, info] of Object.entries(domain.entity_details)) {
      if (info.friendly_name) entityNames.set(eid, info.friendly_name);
    }
    const domFirst = domain.deviceless_entities[0]
      ?? Object.values(domain.devices)[0]?.entities[0];
    if (domFirst) domainFirstEntity.set(domainKey, domFirst);
    for (const [did, device] of Object.entries(domain.devices)) {
      deviceNames.set(did, device.name);
      if (device.entities[0]) deviceFirstEntity.set(did, device.entities[0]);
      deviceDomain.set(did, domainKey);
      for (const eid of device.entities) {
        entityToDevice.set(eid, did);
      }
    }
  }
  return { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain };
}

function resolvedState(
  permissions: PermissionTree,
  item: SummaryItem,
  entityToDevice: Map<string, string>,
  deviceDomain: Map<string, string>,
): NodeState {
  // Pass 1: walk ancestor chain for RED
  const toCheck: NodeState[] = [];
  if (item.type === "entity") {
    const deviceId = entityToDevice.get(item.id);
    if (deviceId) toCheck.push(permissions.devices[deviceId]?.state ?? "GREY");
    toCheck.push(permissions.domains[item.id.split(".")[0]]?.state ?? "GREY");
  } else if (item.type === "device") {
    const domain = deviceDomain.get(item.id);
    if (domain) toCheck.push(permissions.domains[domain]?.state ?? "GREY");
  }
  if (toCheck.some((s) => s === "RED")) return "RED";
  // Pass 2: item's own state is the most specific non-GREY grant
  return item.state;
}

function SortHeader({
  label,
  col,
  current,
  dir,
  onSort,
}: {
  label: string;
  col: SortCol;
  current: SortCol;
  dir: SortDir;
  onSort: (col: SortCol) => void;
}) {
  const active = current === col;
  const arrow = active ? (dir === "asc" ? " \u25B2" : " \u25BC") : " \u25B4\u25BE";
  return (
    <th
      onClick={() => onSort(col)}
      className={`perm-summary-th${active ? " active" : ""}`}
    >
      {label}<span className={`perm-summary-arrow${active ? " active" : ""}`}>{arrow}</span>
    </th>
  );
}

function EffectiveCell({ permissions, item, entityToDevice, deviceDomain }: {
  permissions: PermissionTree;
  item: SummaryItem;
  entityToDevice: Map<string, string>;
  deviceDomain: Map<string, string>;
}) {
  const eff = resolvedState(permissions, item, entityToDevice, deviceDomain);
  return <td className="perm-summary-td"><span className={STATE_CLASS[eff]}>{STATE_LABEL[eff]}</span></td>;
}

export function PermissionSummary({ permissions, entityTree, onEntityClick, mesaProfileEntities, onOpenMesa }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>("type");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const { entityNames, deviceNames, domainFirstEntity, deviceFirstEntity, entityToDevice, deviceDomain } = useMemo(
    () => buildLookups(entityTree),
    [entityTree],
  );

  const items = useMemo<SummaryItem[]>(() => {
    const result: SummaryItem[] = [];
    for (const [domain, node] of Object.entries(permissions.domains)) {
      if (node.state !== "GREY") {
        result.push({ id: domain, type: "domain", friendlyName: domain, state: node.state });
      }
    }
    for (const [deviceId, node] of Object.entries(permissions.devices)) {
      if (node.state !== "GREY") {
        result.push({
          id: deviceId,
          type: "device",
          friendlyName: deviceNames.get(deviceId) ?? deviceId,
          state: node.state,
        });
      }
    }
    for (const [entityId, node] of Object.entries(permissions.entities)) {
      if (node.state !== "GREY") {
        result.push({
          id: entityId,
          type: "entity",
          friendlyName: entityNames.get(entityId) ?? entityId,
          state: node.state,
        });
      }
    }
    return result;
  }, [permissions, entityNames, deviceNames]);

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      let cmp = 0;
      if (sortCol === "type") cmp = TYPE_ORDER[a.type] - TYPE_ORDER[b.type];
      else if (sortCol === "friendly") cmp = a.friendlyName.localeCompare(b.friendlyName);
      else if (sortCol === "id") cmp = a.id.localeCompare(b.id);
      else if (sortCol === "state") cmp = STATE_ORDER[a.state] - STATE_ORDER[b.state];
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [items, sortCol, sortDir]);

  function handleSort(col: SortCol) {
    if (col === sortCol) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  }

  if (items.length === 0) {
    return (
      <p className="perm-summary-empty">
        No explicit permissions configured. All access denied by default.
      </p>
    );
  }

  return (
    <table className="perm-summary-table">
      <thead>
        <tr>
          <SortHeader label="Type" col="type" current={sortCol} dir={sortDir} onSort={handleSort} />
          <SortHeader label="Name" col="friendly" current={sortCol} dir={sortDir} onSort={handleSort} />
          <SortHeader label="ID" col="id" current={sortCol} dir={sortDir} onSort={handleSort} />
          <SortHeader label="Effective" col="state" current={sortCol} dir={sortDir} onSort={handleSort} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => {
          let targetEntity: string | undefined;
          let depth: "entity" | "device" | "domain" = "entity";
          if (item.type === "entity") { targetEntity = item.id; depth = "entity"; }
          else if (item.type === "domain") { targetEntity = domainFirstEntity.get(item.id); depth = "domain"; }
          else if (item.type === "device") { targetEntity = deviceFirstEntity.get(item.id); depth = "device"; }
          const isClickable = !!onEntityClick && !!targetEntity;
          const handleClick = isClickable ? () => onEntityClick!(targetEntity!, depth) : undefined;
          const title = isClickable ? `Simulate ${item.type} permissions for ${item.id}` : undefined;
          return (
            <tr key={`${item.type}:${item.id}`} className="perm-summary-tr">
              <td className="perm-summary-td">
                <span className={`perm-type-badge perm-type-${item.type}`}>
                  {TYPE_LABEL[item.type]}
                </span>
              </td>
              <td className="perm-summary-td-name">
                {item.type === "entity" && onOpenMesa && (
                  <MesaProfileLink
                    entityId={item.id}
                    exists={!!mesaProfileEntities?.has(item.id)}
                    onOpen={onOpenMesa}
                  />
                )}
                <span
                  className={`perm-summary-name-text${isClickable ? " clickable" : ""}`}
                  onClick={handleClick}
                  title={title}
                >
                  {item.friendlyName !== item.id ? item.friendlyName : <span className="state-GREY">-</span>}
                </span>
              </td>
              <td
                className={`perm-summary-td-id${isClickable ? " clickable" : ""}`}
                onClick={handleClick}
                title={title}
              >
                {item.id}
              </td>
              <EffectiveCell permissions={permissions} item={item} entityToDevice={entityToDevice} deviceDomain={deviceDomain} />
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
