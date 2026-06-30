import { describe, it, expect, vi, beforeEach } from "vitest";

// The shell (the <atm-panel> custom element) is what HA actually instantiates;
// existing tests render isolated child views instead. Here we mount the real
// custom element and assert its lifecycle/theme/hass-setter behavior, while
// stubbing the React root so we exercise the SHELL, not the full ATMApp tree.

vi.mock("../atm-panel.css?inline", () => ({ default: "" }));

const renderSpy = vi.fn();
const unmountSpy = vi.fn();
vi.mock("react-dom/client", () => ({
  createRoot: () => ({ render: renderSpy, unmount: unmountSpy }),
}));

const setHassSpy = vi.fn();
vi.mock("../api", () => ({
  api: {},
  setHass: setHassSpy,
  ApiError: class extends Error {},
}));

// Importing the module registers the custom element.
await import("../index");

type PanelEl = HTMLElement & { hass: unknown; narrow: boolean };

describe("atm-panel custom element shell", () => {
  beforeEach(() => {
    renderSpy.mockClear();
    unmountSpy.mockClear();
    setHassSpy.mockClear();
  });

  it("registers the atm-panel custom element", () => {
    expect(customElements.get("atm-panel")).toBeTruthy();
  });

  it("attaches a shadow root on connect and unmounts the root on disconnect", () => {
    const el = document.createElement("atm-panel");
    document.body.appendChild(el);
    expect(el.shadowRoot).toBeTruthy();
    el.remove();
    expect(unmountSpy).toHaveBeenCalled();
  });

  it("renders the app once hass is provided", () => {
    const el = document.createElement("atm-panel") as PanelEl;
    document.body.appendChild(el);
    // _render is a no-op until hass is set, so nothing renders on bare connect.
    expect(renderSpy).not.toHaveBeenCalled();
    el.hass = { user: { id: "u1" } };
    expect(renderSpy).toHaveBeenCalled();
    el.remove();
  });

  it("forwards hass to setHass and follows hass darkMode in auto theme", () => {
    const el = document.createElement("atm-panel") as PanelEl;
    document.body.appendChild(el);

    el.hass = { themes: { darkMode: true } };
    expect(setHassSpy).toHaveBeenCalled();
    expect(el.classList.contains("atm-theme-dark")).toBe(true);

    el.hass = { themes: { darkMode: false } };
    expect(el.classList.contains("atm-theme-light")).toBe(true);

    el.remove();
  });

  it("re-renders when narrow changes", () => {
    const el = document.createElement("atm-panel") as PanelEl;
    document.body.appendChild(el);
    el.hass = { user: { id: "u1" } };  // render is a no-op without hass
    renderSpy.mockClear();
    el.narrow = true;
    expect(renderSpy).toHaveBeenCalled();
    el.remove();
  });

  it("re-renders when the hass user id changes (deep-link / auth context)", () => {
    const el = document.createElement("atm-panel") as PanelEl;
    document.body.appendChild(el);
    renderSpy.mockClear();
    el.hass = { user: { id: "u1" } };
    expect(renderSpy).toHaveBeenCalledTimes(1);
    // Same user id: no extra render.
    el.hass = { user: { id: "u1" } };
    expect(renderSpy).toHaveBeenCalledTimes(1);
    // New user id: re-render.
    el.hass = { user: { id: "u2" } };
    expect(renderSpy).toHaveBeenCalledTimes(2);
    el.remove();
  });
});
