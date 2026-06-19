/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Pure DOM helpers for the injector, separated so they can be unit-tested without
 * the entry module's load-time side effects.
 *
 * THE ONE FRAGILE, HA-VERSION-SPECIFIC FUNCTION is `extractEntityId`. If a future
 * HA release changes how ha-data-table exposes a row's id, update only this file
 * (and bump MESA_INJECT_MIN_HA in const.py if appropriate).
 */

export const ENTITY_ID_RE = /^[a-z_]+\.[a-z0-9_]+$/;
export const BTN_CLASS = "atm-mesa-inject-btn";

// URL prefixes whose data-table rows are entity rows we can profile. The
// /config/entities list covers every entity (including person.*), so it is the
// broadest surface; the per-domain pages are kept for in-context convenience.
export const ENTITY_PAGE_PREFIXES = [
  "/config/entities",
  "/config/automation",
  "/config/script",
  "/config/scene",
  "/config/helpers",
  "/config/person",
];

export function onEntityPage(path: string = window.location.pathname): boolean {
  return ENTITY_PAGE_PREFIXES.some((p) => path.startsWith(p));
}

// Area detail page: /config/areas/area/<area_id>. The area_id is in the URL, so
// no DOM extraction is needed for it.
const AREA_DETAIL_RE = /^\/config\/areas\/area\/([^/]+)/;

/** The area_id from an area detail page URL, or null when not on one. */
export function areaIdFromPath(path: string = window.location.pathname): string | null {
  const m = path.match(AREA_DETAIL_RE);
  return m ? decodeURIComponent(m[1]) : null;
}

/**
 * Where to insert the control: in the same cell as the entity icon
 * (ha-state-icon), right after it. The icon is the one anchor that is reliably in
 * the leftmost entity/name column on every HA config table, so co-locating with
 * it keeps the control in the right column regardless of how the name renders.
 *
 * Returns null when there is no icon yet. HA renders row icons asynchronously
 * (virtualized rows), so injecting before the icon exists would drop the control
 * at the cell's start and the icon would then render after it (the cause of the
 * left/right "swap"). Refusing here makes a later scan retry once the icon is in.
 */
export function nameInsertionPoint(row: HTMLElement): { parent: HTMLElement; before: Node | null } | null {
  const icon = row.querySelector<HTMLElement>("ha-state-icon, ha-icon");
  const iconCell = icon ? icon.closest<HTMLElement>('[role="cell"]') : null;
  if (icon && iconCell) {
    return { parent: iconCell, before: icon.nextSibling };
  }
  return null;
}

/** Collect all elements matching `selector`, piercing open shadow roots. */
export function deepQueryAll(selector: string, root: Document | ShadowRoot | Element = document): Element[] {
  const out: Element[] = [];
  const stack: (Document | ShadowRoot | Element)[] = [root];
  while (stack.length) {
    const node = stack.pop()!;
    let descendants: Element[] = [];
    try {
      descendants = Array.from((node as Element).querySelectorAll("*"));
    } catch {
      continue;
    }
    for (const el of descendants) {
      if (el.matches?.(selector)) out.push(el);
      const sr = (el as HTMLElement).shadowRoot;
      if (sr) stack.push(sr);
    }
  }
  return out;
}

/**
 * Read an entity_id for one data-table row.
 *
 * HA's ha-data-table stores each row's configured id as a `.rowId` property on
 * the `role="row"` element (the same property its own row-click handler reads).
 * On the entity picker pages that id is the entity_id. We validate the shape, so a
 * non-entity table (numeric id, config-entry id, etc.) yields null and is skipped.
 * Returns null when no plausible entity_id is found, which makes the whole feature
 * self-disable on a DOM it does not recognise.
 */
export function extractEntityId(row: HTMLElement): string | null {
  const prop = (row as any).rowId;
  if (typeof prop === "string" && ENTITY_ID_RE.test(prop)) return prop;
  for (const attr of ["data-row-id", "data-id", "data-entity-id"]) {
    const v = row.getAttribute(attr);
    if (v && ENTITY_ID_RE.test(v)) return v;
  }
  return null;
}
