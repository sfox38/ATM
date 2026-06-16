import React, { useState } from "react";
import type { TokenRecord, PatchTokenBody } from "../types";
import { api } from "../api";

interface Props {
  token: TokenRecord;
  onUpdate: (updated: TokenRecord) => void;
}

export const PassThroughNotice = React.memo(function PassThroughNotice({ token, onUpdate }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function convertToScoped() {
    setSaving(true);
    setError(null);
    try {
      const body: PatchTokenBody = { pass_through: false };
      const updated = await api.patchToken(token.id, body);
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to convert token.");
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  }

  return (
    <div>
      <div className="pass-through-header-banner">
        <p>
          <strong className="text-warning">Pass Through token.</strong> This token bypasses the permission tree and has unrestricted access to Home Assistant entities and services. Sensitive attributes are still scrubbed, and the exempt capabilities still apply. The ATM domain is always blocked.
        </p>
        <p className="mt-8">
          Pass-through grants reads and everyday actions automatically, but the write, system, and irreversible capabilities (plus log reading) stay enforced exactly as set in the Capabilities section and must be enabled there individually. Capabilities set to Confirm remain gated even under pass-through.
        </p>
        <p className="mt-8">
          Because pass-through exposes every entity, discovery calls ship the whole home into the model, which can significantly increase token usage. A scoped permission tree also keeps context small; pass-through trades that away.
        </p>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {!confirming ? (
        <button
          className="btn btn-outline"
          onClick={() => setConfirming(true)}
        >
          Convert to Scoped
        </button>
      ) : (
        <div className="card pass-through-convert-card">
          <p className="pass-through-convert-body">
            Converting to scoped will immediately apply the stored permission tree. The permission tree will be empty unless grants were previously configured, meaning the token will have no access until you add grants.
          </p>
          <div className="pass-through-actions">
            <button
              className="btn btn-primary"
              onClick={convertToScoped}
              disabled={saving}
            >
              {saving ? "Converting..." : "Confirm Convert"}
            </button>
            <button className="btn btn-text" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
});
