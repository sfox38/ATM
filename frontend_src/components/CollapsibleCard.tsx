import React, { useState } from "react";

// A card whose body collapses behind a clickable header. When collapsed, an
// optional one-line summary is shown inline so the user can tell the current
// state without expanding. If persistKey is given, the open/closed state is
// remembered in localStorage across reloads; otherwise defaultOpen sets the
// initial state and the user's toggles win for the session.
interface Props {
  title: string;
  summary?: React.ReactNode;
  defaultOpen?: boolean;
  persistKey?: string;
  children: React.ReactNode;
}

function readInitial(persistKey: string | undefined, defaultOpen: boolean): boolean {
  if (!persistKey) return defaultOpen;
  try {
    const v = localStorage.getItem(persistKey);
    return v === null ? defaultOpen : v === "1";
  } catch {
    return defaultOpen;
  }
}

export function CollapsibleCard({ title, summary, defaultOpen = false, persistKey, children }: Props) {
  const [open, setOpen] = useState(() => readInitial(persistKey, defaultOpen));

  function toggle() {
    setOpen((o) => {
      const next = !o;
      if (persistKey) {
        try { localStorage.setItem(persistKey, next ? "1" : "0"); } catch { /* ignore */ }
      }
      return next;
    });
  }

  return (
    <div className="card collapsible-card">
      <button
        type="button"
        className="collapsible-header"
        aria-expanded={open}
        onClick={toggle}
      >
        <span className={`collapsible-chevron${open ? " open" : ""}`} aria-hidden="true" />
        <span className="collapsible-title">{title}</span>
        {!open && summary != null && <span className="collapsible-summary">{summary}</span>}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}
