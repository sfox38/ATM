import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// The injected modal mounts the panel's CSS as an inline string; stub it so the
// test does not depend on Vite's ?inline CSS handling.
vi.mock("../atm-panel.css?inline", () => ({ default: "" }));

const { getEntityTree, getMesaVocabulary, getMesaProfile, putMesaProfile } = vi.hoisted(() => ({
  getEntityTree: vi.fn(),
  getMesaVocabulary: vi.fn(),
  getMesaProfile: vi.fn(),
  putMesaProfile: vi.fn(),
}));

vi.mock("../api", () => {
  class ApiError extends Error {
    status: number;
    code: string;
    constructor(s: number, c: string, m: string) {
      super(m);
      this.status = s;
      this.code = c;
    }
  }
  return {
    api: { getEntityTree, getMesaVocabulary, getMesaProfile, putMesaProfile },
    setHass: vi.fn(),
    ApiError,
  };
});

import { QuickAddApp } from "../inject/QuickAdd";

const TREE = {
  automation: {
    devices: {},
    deviceless_entities: ["automation.morning"],
    entity_details: {
      "automation.morning": {
        entity_id: "automation.morning",
        friendly_name: "Morning",
        device_id: null,
        area_id: null,
        area_name: null,
        labels: [],
      },
    },
  },
};

const DETAIL = {
  entity_id: "automation.morning",
  stored: null,
  effective: {},
  explanation: { entity_id: "automation.morning", explanation: [], conflicts_detected: false, warnings: [] },
};

describe("QuickAddApp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getEntityTree.mockResolvedValue(TREE);
    getMesaVocabulary.mockResolvedValue({ canonical_tags: ["lighting.ambient"], canonical_roots: ["lighting"] });
    getMesaProfile.mockResolvedValue(DETAIL);
  });

  it("loads the registry then mounts the editor for the target entity", async () => {
    render(<QuickAddApp scope="entity" profileKey="automation.morning" isNew onClose={() => {}} onSaved={() => {}} />);
    expect(await screen.findByText(/Add entity profile/i)).toBeTruthy();
    expect(getEntityTree).toHaveBeenCalled();
    expect(getMesaVocabulary).toHaveBeenCalled();
  });

  it("shows an error with a Close action when the registry fails", async () => {
    getEntityTree.mockRejectedValueOnce(new Error("boom"));
    const onClose = vi.fn();
    render(<QuickAddApp scope="entity" profileKey="automation.morning" isNew onClose={onClose} onSaved={() => {}} />);
    expect(await screen.findByText(/Close/i)).toBeTruthy();
  });
});
