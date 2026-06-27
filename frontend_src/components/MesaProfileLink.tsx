// Link from an entity row to its MESA profile editor.
import React from "react";

export function MesaProfileLink({
  entityId, exists, onOpen,
}: { entityId: string; exists: boolean; onOpen: (entityId: string) => void }) {
  const label = exists ? `View MESA profile for ${entityId}` : `Create MESA profile for ${entityId}`;
  return (
    <button
      type="button"
      className={`mesa-link${exists ? " mesa-link-exists" : ""}`}
      title={label}
      aria-label={label}
      onClick={(e) => { e.stopPropagation(); onOpen(entityId); }}
    >
      {exists ? "MESA" : "+"}
    </button>
  );
}
