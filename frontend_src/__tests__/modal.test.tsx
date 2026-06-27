import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import React from "react";
import { Modal } from "../components/Modal";

describe("Modal Escape handling", () => {
  it("closes on Escape even when focus is on document.body", () => {
    const onClose = vi.fn();
    render(
      <Modal titleId="t" onClose={onClose}>
        <p>body content with no focusable element</p>
      </Modal>
    );
    // Simulate focus having fallen outside the dialog (e.g. clicking a label).
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape closes only the topmost modal when dialogs are nested", () => {
    const onCloseOuter = vi.fn();
    const onCloseInner = vi.fn();
    const { rerender } = render(
      <>
        <Modal titleId="outer" onClose={onCloseOuter}><p>outer</p></Modal>
        <Modal titleId="inner" onClose={onCloseInner}><p>inner</p></Modal>
      </>
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCloseInner).toHaveBeenCalledTimes(1);
    expect(onCloseOuter).not.toHaveBeenCalled();

    // The inner dialog closes (unmounts); Escape now reaches the outer one.
    rerender(
      <>
        <Modal titleId="outer" onClose={onCloseOuter}><p>outer</p></Modal>
      </>
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCloseOuter).toHaveBeenCalledTimes(1);
  });
});
