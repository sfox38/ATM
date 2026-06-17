import React, { useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";

interface Props {
  onWiped: () => void;
  onClose: () => void;
}

export function WipeConfirmModal({ onWiped, onClose }: Props) {
  const [typed, setTyped] = useState("");
  const [wiping, setWiping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doWipe() {
    setWiping(true);
    setError(null);
    try {
      await api.wipe();
      onWiped();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Wipe failed.");
      setWiping(false);
    }
  }

  return (
    <Modal titleId="wipe-title" onClose={wiping ? undefined : onClose}>
      <h3 className="modal-title modal-title-error" id="wipe-title">
        Wipe All ATM Data
      </h3>
      <div className="banner banner-error mb-16">
        This will permanently delete all active tokens, archived records, permission trees, capability flags, global settings, and the in-memory audit log. All running ATM tokens will be immediately invalidated and all clients will lose access. This cannot be undone.
      </div>
      <div className="field">
        <label htmlFor="wipe-confirm-input">Type WIPE to confirm</label>
        <input
          id="wipe-confirm-input"
          className="input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="WIPE"
          autoFocus
        />
      </div>
      {error && <div className="banner banner-error">{error}</div>}
      <div className="modal-actions">
        <button
          className="btn btn-danger"
          onClick={doWipe}
          disabled={typed !== "WIPE" || wiping}
        >
          {wiping ? "Wiping..." : "Wipe All Data"}
        </button>
        <button className="btn btn-text" onClick={onClose} disabled={wiping}>Cancel</button>
      </div>
    </Modal>
  );
}
