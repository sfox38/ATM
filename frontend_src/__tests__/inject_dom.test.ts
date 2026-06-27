import { describe, it, expect } from "vitest";
import {
  areaIdFromPath,
  BTN_CLASS,
  deepQueryAll,
  extractEntityId,
  isSelfMutation,
  nameInsertionPoint,
  onEntityPage,
  WIDEN_STYLE_ID,
} from "../inject/dom";

describe("onEntityPage", () => {
  it("matches the config list pages we inject into", () => {
    expect(onEntityPage("/config/entities")).toBe(true);
    expect(onEntityPage("/config/automation/dashboard")).toBe(true);
    expect(onEntityPage("/config/script/edit/123")).toBe(true);
    expect(onEntityPage("/config/scene/dashboard")).toBe(true);
    expect(onEntityPage("/config/helpers")).toBe(true);
    expect(onEntityPage("/config/person")).toBe(true);
  });

  it("ignores pages we do not handle (incl. the non-data-table integration page)", () => {
    expect(onEntityPage("/lovelace/0")).toBe(false);
    expect(onEntityPage("/config/dashboard")).toBe(false);
    expect(onEntityPage("/config/areas/dashboard")).toBe(false);
    expect(onEntityPage("/config/integrations/integration/hue")).toBe(false);
  });
});

describe("areaIdFromPath", () => {
  it("extracts the area_id from an area detail URL", () => {
    expect(areaIdFromPath("/config/areas/area/fitness")).toBe("fitness");
    expect(areaIdFromPath("/config/areas/area/living_room/")).toBe("living_room");
  });

  it("returns null when not on an area detail page", () => {
    expect(areaIdFromPath("/config/areas/dashboard")).toBeNull();
    expect(areaIdFromPath("/config/entities")).toBeNull();
  });
});

describe("nameInsertionPoint", () => {
  function iconCellWith(...extra: Element[]): HTMLElement {
    const c = document.createElement("div");
    c.setAttribute("role", "cell");
    c.className = "mdc-data-table__cell mdc-data-table__cell--icon";
    c.appendChild(document.createElement("ha-state-icon"));
    extra.forEach((e) => c.appendChild(e));
    return c;
  }
  function textCell(text: string): HTMLElement {
    const c = document.createElement("div");
    c.setAttribute("role", "cell");
    c.className = "mdc-data-table__cell";
    c.textContent = text;
    return c;
  }

  it("inserts into the icon's cell, right after the icon", () => {
    const iconC = iconCellWith();
    const device = textCell("Front Door"); // the next cell (Device) must NOT win
    const row = document.createElement("div");
    row.append(iconC, device);
    const point = nameInsertionPoint(row)!;
    expect(point.parent).toBe(iconC);
    const iconEl = iconC.querySelector("ha-state-icon");
    expect(point.before).toBe(iconEl?.nextSibling ?? null);
  });

  it("places after the icon even when the name shares the icon's cell", () => {
    const nameEl = document.createElement("span"); // component-rendered name in the same cell
    const iconC = iconCellWith(nameEl);
    const row = document.createElement("div");
    row.append(iconC, textCell("Outside"));
    const point = nameInsertionPoint(row)!;
    expect(point.parent).toBe(iconC);
    expect(point.before).toBe(nameEl); // icon.nextSibling === the name element
  });

  it("returns null when there is no icon yet (so a later scan retries)", () => {
    const a = textCell("X");
    const b = textCell("Y");
    const row = document.createElement("div");
    row.append(a, b);
    expect(nameInsertionPoint(row)).toBeNull();
  });
});

describe("extractEntityId", () => {
  function row(props: Record<string, unknown>, attrs: Record<string, string> = {}): HTMLElement {
    const el = document.createElement("div");
    Object.assign(el, props);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  it("reads a valid entity_id from the .rowId property", () => {
    expect(extractEntityId(row({ rowId: "automation.morning" }))).toBe("automation.morning");
    expect(extractEntityId(row({ rowId: "input_boolean.guest_mode" }))).toBe("input_boolean.guest_mode");
  });

  it("falls back to data attributes", () => {
    expect(extractEntityId(row({}, { "data-row-id": "script.run" }))).toBe("script.run");
  });

  it("returns null for non-entity ids, so the feature self-disables (no oracle)", () => {
    expect(extractEntityId(row({ rowId: "12345" }))).toBeNull();        // numeric row id
    expect(extractEntityId(row({ rowId: "8d1fconfigentry" }))).toBeNull(); // config-entry id (no dot)
    expect(extractEntityId(row({}))).toBeNull();                         // nothing present
  });
});

describe("isSelfMutation", () => {
  function ourButton(): HTMLElement {
    const b = document.createElement("button");
    b.className = BTN_CLASS;
    return b;
  }
  function widenStyle(): HTMLElement {
    const s = document.createElement("style");
    s.id = WIDEN_STYLE_ID;
    return s;
  }
  function rec(p: { target?: Node; added?: Node[]; removed?: Node[] }): MutationRecord {
    return {
      target: p.target ?? document.createElement("div"),
      addedNodes: (p.added ?? []) as unknown as NodeList,
      removedNodes: (p.removed ?? []) as unknown as NodeList,
    } as MutationRecord;
  }

  it("treats our own glyph swap (textContent on the button) as self", () => {
    const btn = ourButton();
    // applyButtonState swapping +/check: childList churn whose target is our button.
    const r = rec({ target: btn, added: [document.createTextNode("+")], removed: [document.createTextNode("✓")] });
    expect(isSelfMutation([r])).toBe(true);
  });

  it("treats our button being inserted, and the width style appended, as self", () => {
    const cell = document.createElement("div");
    const sr = document.createElement("div");
    expect(
      isSelfMutation([
        rec({ target: cell, added: [ourButton()] }),
        rec({ target: sr, added: [widenStyle()] }),
      ])
    ).toBe(true);
  });

  it("treats HA removing our button as NOT self, so a later scan re-adds it", () => {
    const cell = document.createElement("div");
    expect(isSelfMutation([rec({ target: cell, removed: [ourButton()] })])).toBe(false);
  });

  it("treats a genuine HA row re-render (foreign nodes) as NOT self", () => {
    const tbody = document.createElement("div");
    const foreignRow = document.createElement("div"); // not our button/style
    expect(isSelfMutation([rec({ target: tbody, added: [foreignRow] })])).toBe(false);
  });

  it("is NOT self when a batch mixes our writes with a foreign change", () => {
    const cell = document.createElement("div");
    expect(
      isSelfMutation([
        rec({ target: cell, added: [ourButton()] }), // self
        rec({ target: cell, added: [document.createElement("span")] }), // foreign
      ])
    ).toBe(false);
  });
});

describe("deepQueryAll", () => {
  it("pierces open shadow roots to find the data table", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const sr = host.attachShadow({ mode: "open" });
    const table = document.createElement("ha-data-table");
    sr.appendChild(table);

    expect(deepQueryAll("ha-data-table")).toContain(table);
    host.remove();
  });

  it("returns empty when the anchor is absent (graceful no-op)", () => {
    expect(deepQueryAll("ha-nonexistent-table-xyz")).toEqual([]);
  });
});
