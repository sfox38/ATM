// Small inline affordance that jumps to an entity's MESA profile. Shows "MESA"
// when a profile exists (opens it), or "+" when none does (opens a prefilled
// create form). Placed to the left of an entity name in the token cards.
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
