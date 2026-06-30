import React, { useState, useEffect, useRef } from "react";
import type { TokenRecord, CreateTokenBody } from "../types";
import { api } from "../api";
import { copyToClipboard } from "../utils";
import { Modal } from "./Modal";
import { ConnectInstructions, CopyCodeBox } from "./ConnectInstructions";

const NAME_REGEX = /^[A-Za-z0-9_\-]{3,32}$/;

interface Props {
  existingNames: string[];
  onCreated: (token: TokenRecord, rawToken: string) => void;
  onClose: () => void;
}

type TtlUnit = "minutes" | "hours" | "days" | "weeks" | "none";

function slugify(name: string) {
  return name.toLowerCase().replace(/-/g, "_");
}

function addMinutes(m: number): string {
  const d = new Date(Date.now() + m * 60000);
  return d.toISOString();
}


export function CopyButton({ text, label = "Copy to clipboard" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await copyToClipboard(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button className="btn btn-primary" onClick={copy}>
      {copied ? "Copied!" : label}
    </button>
  );
}

// The raw-token reveal block (amber warning + monospace token). Shared by the
// post-create modal and the onboarding wizard so both look identical. The
// warning text is overridable because the wizard shows the token again on its
// Connect step, so the default "will not be shown again" copy is inaccurate there.
export function RawTokenDisplay({ rawToken, note }: { rawToken: string; note?: React.ReactNode }) {
  return (
    <>
      <div className="amber-block">
        {note ?? <p><strong>This token will not be shown again.</strong> Copy it now before closing.</p>}
      </div>
      <CopyCodeBox value={rawToken} />
    </>
  );
}

interface TokenDisplayProps {
  rawToken: string;
  tokenName: string;
  onClose: () => void;
}

function TokenDisplayModal({ rawToken, tokenName, onClose }: TokenDisplayProps) {
  const [closeEnabled, setCloseEnabled] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => setCloseEnabled(true), 3000);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  return (
    <Modal titleId="created-token-title" onClose={closeEnabled ? onClose : undefined}>
      <h3 className="modal-title" id="created-token-title">Token Created: {tokenName}</h3>
      <RawTokenDisplay rawToken={rawToken} />
      <div className="banner banner-info">
        To use this token, either replace the token in your agent's existing ATM server config, or add ATM as a new MCP server with this token.
      </div>
      <details className="connect-details">
        <summary>Help me connect this token to an agent</summary>
        <ConnectInstructions token={rawToken} />
      </details>
      <div className="modal-actions">
        <button
          className="btn btn-text"
          onClick={onClose}
          disabled={!closeEnabled}
          title={closeEnabled ? undefined : "Wait 3 seconds before closing"}
        >
          {closeEnabled ? "Close" : "Close (3s)"}
        </button>
      </div>
    </Modal>
  );
}

export function TokenCreateModal({ existingNames, onCreated, onClose }: Props) {
  const [name, setName] = useState("");
  const [ttlUnit, setTtlUnit] = useState<TtlUnit>("none");
  const [ttlValue, setTtlValue] = useState("24");
  const [passThrough, setPassThrough] = useState(false);
  const [ptConfirmed, setPtConfirmed] = useState(false);
  const [rateLimitRequests, setRateLimitRequests] = useState("60");
  const [rateLimitBurst, setRateLimitBurst] = useState("10");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<{ record: TokenRecord; raw: string } | null>(null);

  const nameError = (() => {
    if (!name) return null;
    if (!NAME_REGEX.test(name)) return "Name must be 3-32 characters: letters, digits, _ or -.";
    const slug = slugify(name);
    if (existingNames.some((n) => slugify(n) === slug)) return "A token with this name (or equivalent slug) already exists.";
    return null;
  })();

  const reqNum = parseInt(rateLimitRequests, 10);
  const burstDisabled = isNaN(reqNum) || reqNum === 0;

  const canSubmit =
    name.length >= 3 &&
    !nameError &&
    (!passThrough || ptConfirmed) &&
    !saving;

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      let expiresAt: string | undefined;
      if (ttlUnit !== "none") {
        const n = parseInt(ttlValue, 10);
        const minutes =
          ttlUnit === "minutes" ? n :
          ttlUnit === "hours" ? n * 60 :
          ttlUnit === "days" ? n * 60 * 24 :
          n * 60 * 24 * 7;
        expiresAt = addMinutes(minutes);
      }
      const burstNum = burstDisabled ? 0 : parseInt(rateLimitBurst, 10);
      const body: CreateTokenBody = {
        name,
        expires_at: expiresAt,
        pass_through: passThrough,
        confirm_pass_through: passThrough ? true : undefined,
        rate_limit_requests: parseInt(rateLimitRequests, 10) || 0,
        rate_limit_burst: burstNum,
      };
      const resp = await api.createToken(body);
      const { token: rawToken, ...record } = resp;
      setCreatedToken({ record, raw: rawToken });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create token.");
    } finally {
      setSaving(false);
    }
  }

  if (createdToken) {
    return (
      <TokenDisplayModal
        rawToken={createdToken.raw}
        tokenName={createdToken.record.name}
        onClose={() => {
          onCreated(createdToken.record, createdToken.raw);
          onClose();
        }}
      />
    );
  }

  return (
    <Modal titleId="create-token-title" onClose={saving ? undefined : onClose}>
      <h3 className="modal-title" id="create-token-title">Create Token</h3>

      <div className="field">
        <label htmlFor="token-name-input">Name (required)</label>
          <input
            id="token-name-input"
            className={`input${nameError ? " error" : ""}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my_token"
            maxLength={32}
            autoFocus
          />
          {nameError && <span className="field-error">{nameError}</span>}
        </div>

        <div className="field">
          <label htmlFor="token-expiry-unit">Expiry</label>
          <div className="token-create-expiry-row">
            <select
              id="token-expiry-unit"
              className="input input-auto"
              value={ttlUnit}
              onChange={(e) => setTtlUnit(e.target.value as TtlUnit)}
            >
              <option value="none">No expiry</option>
              <option value="minutes">Minutes</option>
              <option value="hours">Hours</option>
              <option value="days">Days</option>
              <option value="weeks">Weeks</option>
            </select>
            {ttlUnit !== "none" && (
              <input
                aria-label="Expiry amount"
                className="input token-create-expiry-value"
                type="number"
                min={1}
                value={ttlValue}
                onChange={(e) => setTtlValue(e.target.value)}
              />
            )}
          </div>
        </div>

        <div className="toggle-row">
          <div className="toggle-label">
            <span>Pass-through mode</span>
            <small>Bypasses the permission tree for broad entity access. Exempt capabilities, MESA, the ATM blocklist, and attribute scrubbing still apply.</small>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              aria-label="Pass-through mode"
              checked={passThrough}
              onChange={(e) => { setPassThrough(e.target.checked); setPtConfirmed(false); }}
            />
            <span className="toggle-switch-track" />
          </label>
        </div>

        {passThrough ? (
          <div className="amber-block">
            <p>
              <strong>This token bypasses the permission tree and gets broad access to your Home Assistant entities and services.</strong> It is not unrestricted: the write, system, and irreversible capabilities (plus log reading) stay enforced exactly as set in Capabilities, the per-entity MESA safety layer still applies, the ATM domain stays blocked, sensitive attributes are still scrubbed, and rate limits, revocation, and expiry still apply. Grant it only to clients you trust. Works only with HTTP-based MCP clients, not stdio-based ones.
            </p>
            <div className="toggle-row mt-10">
              <div className="toggle-label"><span>I understand this token has broad Home Assistant access</span></div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  aria-label="I understand this token has broad Home Assistant access"
                  checked={ptConfirmed}
                  onChange={(e) => setPtConfirmed(e.target.checked)}
                />
                <span className="toggle-switch-track" />
              </label>
            </div>
          </div>
        ) : (
          <div className="token-create-rate-section">
            <div className="token-create-rate-fields">
              <div className="field token-create-rate-field">
                <label htmlFor="create-rate-requests">Requests per minute (0 = disabled)</label>
                <input
                  id="create-rate-requests"
                  className="input"
                  type="number"
                  min={0}
                  value={rateLimitRequests}
                  onChange={(e) => setRateLimitRequests(e.target.value)}
                />
              </div>
              <div className="field token-create-rate-field">
                <label htmlFor="create-rate-burst">Burst per second</label>
                <input
                  id="create-rate-burst"
                  className="input"
                  type="number"
                  min={0}
                  value={burstDisabled ? "0" : rateLimitBurst}
                  disabled={burstDisabled}
                  onChange={(e) => setRateLimitBurst(e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {error && <div className="banner banner-error mt-12">{error}</div>}

      <div className="modal-actions">
        <button className="btn btn-primary" onClick={submit} disabled={!canSubmit}>
          {saving ? "Creating..." : "Create"}
        </button>
        <button className="btn btn-text" onClick={onClose} disabled={saving}>Cancel</button>
      </div>
    </Modal>
  );
}
