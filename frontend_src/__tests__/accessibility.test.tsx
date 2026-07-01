/** Focused accessibility regressions for custom ATM panel controls. */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { PermissionSelector } from "../components/PermissionSelector";
import { TagInput } from "../components/TagInput";
import { AuditTable } from "../components/AuditTable";
import type { AuditEntry } from "../types";

describe("accessibility regressions", () => {
  it("names permission selectors and each permission button", () => {
    render(
      <PermissionSelector
        value="GREEN"
        onChange={() => undefined}
        label="Permission for light.kitchen"
      />,
    );

    expect(screen.getByRole("group", { name: "Permission for light.kitchen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Read and write, selected/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("connects the tag combobox to its active suggestion", () => {
    render(
      <TagInput
        value={[]}
        onChange={() => undefined}
        canonicalTags={["lighting.ambient", "lighting.task"]}
      />,
    );

    const combo = screen.getByRole("combobox", { name: "Semantic tags" });
    fireEvent.change(combo, { target: { value: "light" } });
    fireEvent.keyDown(combo, { key: "ArrowDown" });

    expect(combo).toHaveAttribute("aria-controls");
    expect(combo).toHaveAttribute("aria-activedescendant");
    expect(screen.getByRole("listbox")).toHaveAttribute("id", combo.getAttribute("aria-controls"));
  });

  it("opens audit row details from a named button", () => {
    const entry: AuditEntry = {
      request_id: "req-1",
      timestamp: "2026-06-30T00:00:00Z",
      token_id: "tok-1",
      token_name: "demo",
      method: "GET",
      resource: "/api/atm/states",
      outcome: "allowed",
      client_ip: "127.0.0.1",
      pass_through: false,
      payload: null,
    };

    render(
      <AuditTable
        entries={[entry]}
        page={0}
        pageSize={10}
        onPageChange={vi.fn()}
        tokenNames={{ "tok-1": "demo" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Open audit entry Allowed for demo/ }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Audit Entry")).toBeInTheDocument();
  });
});
