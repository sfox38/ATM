import React from "react";
import type { NodeState } from "../types";

interface Props {
  value: NodeState;
  onChange: (state: NodeState) => void;
  disabled?: boolean;
  label?: string;
}

const BUTTONS: { state: NodeState; label: string; title: string }[] = [
  { state: "GREY", label: "N", title: "No explicit grant (inherits from parent)" },
  { state: "YELLOW", label: "R", title: "Read-only" },
  { state: "GREEN", label: "W", title: "Read and write" },
  { state: "RED", label: "D", title: "Deny (overrides all children)" },
];

let _dragState: NodeState | null = null;

if (typeof document !== "undefined") {
  document.addEventListener("pointerup", () => { _dragState = null; });
  document.addEventListener("pointercancel", () => { _dragState = null; });
}

export const PermissionSelector = React.memo(function PermissionSelector({ value, onChange, disabled, label = "Permission" }: Props) {
  return (
    <div
      className="perm-selector"
      role="group"
      aria-label={label}
      style={{ touchAction: "none" }}
      onPointerEnter={() => {
        if (_dragState !== null && !disabled) onChange(_dragState);
      }}
    >
      {BUTTONS.map(({ state, label, title }) => (
        <button
          key={state}
          title={title}
          className={`perm-btn${value === state ? ` active-${state}` : ""}`}
          onPointerDown={(e) => {
            if (disabled) return;
            e.preventDefault();
            e.currentTarget.releasePointerCapture(e.pointerId);
            const newState: NodeState = state === value ? "GREY" : state;
            _dragState = newState;
            onChange(newState);
          }}
          disabled={disabled}
          aria-pressed={value === state}
          aria-label={`${title}${value === state ? ", selected" : ""}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
});
