import React, { useState } from "react";

// A card whose body collapses behind a clickable header. When collapsed, an
// optional one-line summary is shown inline so the user can tell the current
// state without expanding. Uncontrolled: defaultOpen sets the initial state,
// after which the user's toggles win (it does not re-collapse on re-render).
interface Props {
  title: string;
  summary?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function CollapsibleCard({ title, summary, defaultOpen = false, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card collapsible-card">
      <button
        type="button"
        className="collapsible-header"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`collapsible-chevron${open ? " open" : ""}`} aria-hidden="true" />
        <span className="collapsible-title">{title}</span>
        {!open && summary != null && <span className="collapsible-summary">{summary}</span>}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}
