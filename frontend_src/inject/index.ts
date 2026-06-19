/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * In-context MESA profile injector.
 *
 * Loaded on every HA page via the frontend extra-module mechanism, but only when
 * the admin enabled `mesa_inject_enabled` (the Python side registers this module
 * conditionally). It adds a (+) / MESA-pill control to the rows of HA's native
 * config list pages (Automations, Scripts, Helpers, People, and integration
 * detail pages) so an admin can create or edit a MESA entity profile in place.
 *
 * Design rules (see the plan doc):
 *  - Admin only: does nothing unless `hass.user.is_admin`.
 *  - Fully sandboxed: every entry point is wrapped so a thrown error logs and
 *    no-ops; it must never break the HA frontend.
 *  - Single current-HA path with feature-detection: if the data-table DOM does
 *    not match, nothing is injected (the feature self-disables). This is the
 *    intended behaviour on older HA or after a breaking HA frontend release.
 *  - The heavy modal (React + ProfileEditor) is lazy-imported on first click.
 *
 * THE ONE FRAGILE SPOT is `extractEntityId()` in ./dom: it reads each row's id
 * from HA's ha-data-table. If a future HA release changes that, update only that
 * function (and bump MESA_INJECT_MIN_HA in const.py if needed).
 */

import { api, setHass } from "../api";
import { areaIdFromPath, BTN_CLASS, deepQueryAll, extractEntityId, nameInsertionPoint, onEntityPage } from "./dom";

const LOG = "[ATM inject]";
const POLL_MS = 2000;
const DEBOUNCE_MS = 150;

type Scope = "entity" | "area";
let profiledEntities = new Set<string>();
let profiledAreas = new Set<string>();
const observedRoots = new WeakSet<ShadowRoot>();
let debounceTimer: number | undefined;

function log(...args: unknown[]): void {
  // Quiet by default; visible with verbose console logging.
  // eslint-disable-next-line no-console
  console.debug(LOG, ...args);
}

function getHass(): any {
  return (document.querySelector("home-assistant") as any)?.hass ?? null;
}

function buildButton(scope: Scope, key: string): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.className = BTN_CLASS;
  btn.type = "button";
  btn.dataset.atmKey = key; // used to detect a stale button when the area changes
  // Fixed compact size: it lives in the narrow icon cell (often sharing it with
  // the name), so a variable-width label would overlap the name. Margin on both
  // sides keeps it off the icon. The MESA/create meaning is in the tooltip.
  Object.assign(btn.style, {
    cursor: "pointer",
    boxSizing: "border-box",
    width: "26px",
    height: "30px",
    minWidth: "26px",
    flex: "0 0 auto",
    padding: "0",
    border: "1px solid var(--divider-color, #c4c4c4)",
    borderRadius: "6px",
    font: "inherit",
    fontSize: "15px",
    fontWeight: "700",
    lineHeight: "1",
    margin: "0 10px",
    // Sit above HA's icon/name, whose padded hit areas otherwise overlap and
    // "steal" clicks meant for this button.
    position: "relative",
    zIndex: "20",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    verticalAlign: "middle",
    overflow: "hidden",
  } as CSSStyleDeclaration);
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openModal(scope, key).catch((err) => log("modal open failed", err));
  });
  applyButtonState(btn, scope, key);
  return btn;
}

/** Set the +/MESA appearance for the entity's current profiled state. No-op (no
 *  DOM writes) when already in the right state, so our own table observer does
 *  not feed back into an endless rescan loop. */
function applyButtonState(btn: HTMLButtonElement, scope: Scope, key: string): void {
  const has = (scope === "area" ? profiledAreas : profiledEntities).has(key);
  const want = has ? "has" : "new";
  if (btn.dataset.atmState === want) return;
  btn.dataset.atmState = want;
  // Compact, fixed-width glyphs (no "MESA" text, which is too wide for the cell):
  // a check on an accent fill means "profile set, click to edit"; a "+" outline
  // means "create". The MESA wording lives in the tooltip.
  btn.textContent = has ? "✓" : "+";
  btn.title = has
    ? `MESA profile set for ${key} - click to edit`
    : `Create a MESA profile for ${key}`;
  btn.style.background = has ? "var(--primary-color, #03a9f4)" : "var(--secondary-background-color, rgba(127,127,127,0.16))";
  btn.style.color = has ? "var(--text-primary-color, #fff)" : "var(--secondary-text-color, #717171)";
  btn.style.borderColor = has ? "var(--primary-color, #03a9f4)" : "var(--divider-color, #c4c4c4)";
}

function decorateRow(row: HTMLElement): void {
  const entityId = extractEntityId(row);
  if (!entityId) return;
  const existing = row.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`);
  if (existing) {
    applyButtonState(existing, "entity", entityId);
    return;
  }
  const point = nameInsertionPoint(row);
  if (!point) return; // icon not rendered yet; a later scan will retry
  // Mark this cell so only decorated (entity) cells get the wider column, never
  // the full-width group-header rows.
  point.parent.setAttribute("data-atm-widen", "1");
  point.parent.insertBefore(buildButton("entity", entityId), point.before);
}

const WIDEN_STYLE_ID = "atm-mesa-col-widen";

/** Widen the first (icon) column of a data table so our button has room and does
 *  not collide with the name. Injected once per table shadow root. Targets both
 *  the header and body first cells so the columns stay aligned. This is the one
 *  deliberately invasive bit (it overrides HA's column width), so it is kept
 *  narrowly scoped and easy to remove if HA changes the table. */
function ensureColumnWidth(sr: ShadowRoot): void {
  if (sr.querySelector(`#${WIDEN_STYLE_ID}`)) return;
  const style = document.createElement("style");
  style.id = WIDEN_STYLE_ID;
  // Body: only cells we actually decorate (marked with data-atm-widen), so the
  // full-width group-header rows (e.g. "Ungrouped") are left alone. Header: the
  // first column header, to keep the columns aligned.
  style.textContent =
    "[data-atm-widen],.mdc-data-table__header-cell:first-child{" +
    "width:96px !important;min-width:96px !important;max-width:96px !important;flex:0 0 96px !important;}";
  sr.appendChild(style);
}

function scan(): void {
  if (onEntityPage()) scanDataTables();
  else scanAreaPage();
}

function scanDataTables(): void {
  for (const table of deepQueryAll("ha-data-table")) {
    const sr = (table as HTMLElement).shadowRoot;
    if (!sr) continue;
    observeRoot(sr);
    ensureColumnWidth(sr);
    const rows = sr.querySelectorAll<HTMLElement>('[role="row"]');
    for (const row of Array.from(rows)) {
      if (row.classList.contains("mdc-data-table__header-row")) continue;
      try {
        decorateRow(row);
      } catch (e) {
        log("row decorate failed", e);
      }
    }
  }
}

/** Area detail page: append the control to the subpage header's title, after the
 *  area name. The area_id comes from the URL, so there is no DOM id to extract. */
function scanAreaPage(): void {
  const areaId = areaIdFromPath();
  if (!areaId) return;
  for (const sp of deepQueryAll("hass-subpage")) {
    const sr = (sp as HTMLElement).shadowRoot;
    const title = sr?.querySelector<HTMLElement>(".main-title");
    if (!sr || !title) continue;
    const existing = title.querySelector<HTMLButtonElement>(`.${BTN_CLASS}`);
    if (existing && existing.dataset.atmKey === areaId) {
      applyButtonState(existing, "area", areaId);
    } else {
      if (existing) existing.remove(); // stale button from a previously viewed area
      observeRoot(sr);
      title.appendChild(buildButton("area", areaId));
    }
    return; // only the active subpage
  }
}

function debouncedScan(): void {
  window.clearTimeout(debounceTimer);
  debounceTimer = window.setTimeout(() => safe(scan), DEBOUNCE_MS);
}

/** Observe a table's shadow root so virtualized row changes repaint promptly. */
function observeRoot(sr: ShadowRoot): void {
  if (observedRoots.has(sr)) return;
  observedRoots.add(sr);
  try {
    new MutationObserver(() => debouncedScan()).observe(sr, {
      childList: true,
      subtree: true,
    });
  } catch (e) {
    log("observe failed", e);
  }
}

async function refreshProfiled(): Promise<void> {
  try {
    const set = new Set<string>();
    let cursor: string | undefined;
    for (let i = 0; i < 20; i++) {
      const resp = await api.listMesaProfiles({ limit: 200, cursor });
      for (const p of resp.profiles) set.add(p.entity_id);
      if (!resp.has_more || !resp.next_cursor) break;
      cursor = resp.next_cursor;
    }
    profiledEntities = set;
  } catch (e) {
    log("profile list refresh failed", e);
  }
}

async function refreshProfiledAreas(): Promise<void> {
  try {
    const resp = await api.listMesaAreas();
    profiledAreas = new Set(resp.areas.map((a) => a.area_id));
  } catch (e) {
    log("area profile list refresh failed", e);
  }
}

async function openModal(scope: Scope, key: string): Promise<void> {
  await import("./QuickAdd"); // defines <atm-mesa-quick-add>
  const el = document.createElement("atm-mesa-quick-add");
  el.setAttribute("scope", scope);
  el.setAttribute("key", key);
  const profiled = scope === "area" ? profiledAreas : profiledEntities;
  if (profiled.has(key)) el.setAttribute("has-profile", "1");
  el.addEventListener("atm-mesa-saved", () => {
    (scope === "area" ? refreshProfiledAreas() : refreshProfiled()).then(() => safe(scan));
  });
  document.body.appendChild(el);
}

function safe(fn: () => void): void {
  try {
    fn();
  } catch (e) {
    log("error", e);
  }
}

function installListeners(): void {
  for (const ev of ["location-changed", "popstate", "hashchange"]) {
    window.addEventListener(ev, () => debouncedScan());
  }
  // Coarse observer for navigation/panel swaps (shadow-internal changes are
  // covered by the per-table observers and the poll).
  try {
    new MutationObserver(() => debouncedScan()).observe(document.body, {
      childList: true,
      subtree: true,
    });
  } catch (e) {
    log("body observe failed", e);
  }
  window.setInterval(() => safe(scan), POLL_MS);
}

let startAttempts = 0;
function start(): void {
  const hass = getHass();
  if (!hass) {
    // <home-assistant> not ready yet; retry briefly, then give up.
    if (startAttempts++ < 40) window.setTimeout(() => safe(start), 250);
    return;
  }
  if (!hass.user?.is_admin) return; // non-admins get nothing
  setHass(hass);
  Promise.all([refreshProfiled(), refreshProfiledAreas()]).then(() => safe(scan));
  installListeners();
  log("active");
}

safe(start);
