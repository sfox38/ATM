import React, { useCallback, useEffect, useRef, useState } from "react";
import type { CreateTokenBody, EntityTree as EntityTreeData, Persona, PermissionTree, TokenRecord } from "../types";
import { api, ApiError } from "../api";
import { PersonaPicker } from "../components/PersonaPicker";
import { EntityTree } from "../components/EntityTree";
import { RawTokenDisplay, CopyButton } from "../components/TokenCreateModal";
import { ConnectInstructions } from "../components/ConnectInstructions";
import { PERSONA_CAP_DEFAULTS } from "../personas";
import { buildTestPrompt, firstGreenTarget } from "../wizard_helpers";
import { ErrorMsg } from "../index";

const NAME_REGEX = /^[A-Za-z0-9_\-]{3,32}$/;
const STEP_LABELS = ["Persona", "Token", "Access", "Copy", "Connect", "Test", "Done"];

type TtlUnit = "minutes" | "hours" | "days" | "weeks" | "none";

function addMinutes(m: number): string {
  return new Date(Date.now() + m * 60000).toISOString();
}

interface Props {
  onCancel: () => void;
  onFinish: (tokenId: string) => void;
}

export function OnboardingWizard({ onCancel, onFinish }: Props) {
  const [step, setStep] = useState(0);
  const [persona, setPersona] = useState<Persona | null>("new_user");
  const [name, setName] = useState("test_token");
  const [ttlUnit, setTtlUnit] = useState<TtlUnit>("none");
  const [ttlValue, setTtlValue] = useState("24");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [rawToken, setRawToken] = useState<string | null>(null);
  const [tokenId, setTokenId] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<PermissionTree>({ domains: {}, devices: {}, entities: {} });

  const [entityTree, setEntityTree] = useState<EntityTreeData | null>(null);
  const [mesaEnforced, setMesaEnforced] = useState(false);
  const [showAllDomains, setShowAllDomains] = useState(false);

  const [connected, setConnected] = useState(false);

  useEffect(() => {
    api.getEntityTree().then(setEntityTree).catch(() => null);
    api.getSettings().then((s) => setMesaEnforced(s.mesa_mode === "enforced")).catch(() => null);
  }, []);

  const grantedEntityId = firstGreenTarget(permissions, entityTree);
  const grantedFriendlyName = (() => {
    if (!grantedEntityId) return "";
    if (!entityTree) return grantedEntityId;
    const domain = grantedEntityId.split(".")[0];
    return entityTree[domain]?.entity_details[grantedEntityId]?.friendly_name ?? grantedEntityId;
  })();

  const hasLights = !!(entityTree && entityTree["light"] && Object.keys(entityTree["light"].entity_details).length > 0);

  async function createAndAdvance() {
    if (!persona) return;
    setSaving(true);
    setError(null);
    try {
      const caps = PERSONA_CAP_DEFAULTS[persona];
      const patchBody: Record<string, unknown> = { persona, ...(caps || {}) };
      if (!tokenId) {
        if (name && !NAME_REGEX.test(name)) {
          setError("Name must be 3-32 characters: letters, digits, _ or -.");
          return;
        }
        let expiresAt: string | undefined;
        if (ttlUnit !== "none") {
          const n = parseInt(ttlValue, 10);
          const minutes = ttlUnit === "minutes" ? n : ttlUnit === "hours" ? n * 60 : ttlUnit === "days" ? n * 1440 : n * 10080;
          expiresAt = addMinutes(minutes);
        }
        const body: CreateTokenBody = { name, expires_at: expiresAt, pass_through: false };
        const resp = await api.createToken(body);
        const { token: raw, ...record } = resp;
        // Stash the irreversible bits BEFORE the persona patch so a patch
        // failure leaves a recoverable (retry-only-the-patch) state.
        setRawToken(raw);
        setTokenId(record.id);
        setPermissions(record.permissions);
        await api.patchToken(record.id, patchBody);
      } else {
        // Token already created (back-navigation): re-apply persona only.
        await api.patchToken(tokenId, patchBody);
      }
      setStep(2);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create the token.");
    } finally {
      setSaving(false);
    }
  }

  // Poll for connection while on the Test step.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const poll = useCallback(async () => {
    if (!tokenId) return;
    try {
      const c = await api.getTokenConnection(tokenId);
      if (c.has_live_session || c.request_count > 0) setConnected(true);
    } catch {
      // transient; keep polling
    }
  }, [tokenId]);

  useEffect(() => {
    if (step !== 5 || connected) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [step, connected, poll]);

  const prompts = buildTestPrompt(grantedFriendlyName || "your device", mesaEnforced);

  function Stepper() {
    return (
      <ol className="wizard-stepper" aria-label="Setup progress">
        {STEP_LABELS.map((label, i) => (
          <li
            key={label}
            className={`wizard-step${i === step ? " wizard-step-active" : ""}${i < step ? " wizard-step-done" : ""}`}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="wizard-step-num">{i + 1}</span>
            <span className="wizard-step-label">{label}</span>
          </li>
        ))}
      </ol>
    );
  }

  function renderBody() {
    switch (step) {
      case 0:
        return (
          <>
            <h3 className="wizard-title">Connect your first AI agent</h3>
            <p className="wizard-sub">We'll create a scoped test token, give it access to one device, and help you connect your agent. A persona sets what the agent is allowed to do. We've selected <strong>New user</strong>, a safe starting point; just press Next, or pick another if you prefer.</p>
            <PersonaPicker selected={persona} onSelect={setPersona} />
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={onCancel}>Cancel</button>
              <button className="btn btn-primary" disabled={!persona} onClick={() => setStep(1)}>Next</button>
            </div>
          </>
        );
      case 1:
        return (
          <>
            <h3 className="wizard-title">Name your test token</h3>
            <p className="wizard-sub">This is a dedicated token for trying things out. The name "test_token" makes its purpose obvious; you can change it. Name and expiry are fixed once the token is created.</p>
            <div className="field">
              <label htmlFor="wiz-name">Name</label>
              <input id="wiz-name" className="input" value={name} disabled={!!tokenId}
                maxLength={32} onChange={(e) => setName(e.target.value)} placeholder="test_token" />
            </div>
            <div className="field">
              <label>Expiry</label>
              <div className="token-create-expiry-row">
                <select className="input input-auto" value={ttlUnit} disabled={!!tokenId}
                  onChange={(e) => setTtlUnit(e.target.value as TtlUnit)}>
                  <option value="none">No expiry</option>
                  <option value="minutes">Minutes</option>
                  <option value="hours">Hours</option>
                  <option value="days">Days</option>
                  <option value="weeks">Weeks</option>
                </select>
                {ttlUnit !== "none" && (
                  <input className="input token-create-expiry-value" type="number" min={1}
                    value={ttlValue} disabled={!!tokenId} onChange={(e) => setTtlValue(e.target.value)} />
                )}
              </div>
            </div>
            {tokenId && <div className="banner banner-info">Token created. Name and expiry are now fixed; cancel and start over to change them.</div>}
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={() => setStep(0)}>Back</button>
              <button className="btn btn-primary" disabled={saving} onClick={createAndAdvance}>
                {saving ? "Working..." : tokenId ? "Next" : "Create token"}
              </button>
            </div>
          </>
        );
      case 2:
        return (
          <>
            <h3 className="wizard-title">Give it access to one light</h3>
            <p className="wizard-sub">
              Below is your <code>light</code> group. Click the triangle on the left to expand it and
              find a light you can physically see. Click the <strong>W</strong> button on that row to
              grant full (read and write) access; its badge changes to "WRITE". You can click W on a
              device to grant everything under it, or expand the device one more level to pick a single
              entity. Grant more later from the token's detail page.
            </p>
            {!hasLights && !showAllDomains && (
              <div className="banner banner-warn">
                No lights found in this Home Assistant.{" "}
                <button className="btn btn-text btn-sm" onClick={() => setShowAllDomains(true)}>Show all devices</button>
              </div>
            )}
            {tokenId && (
              <EntityTree
                tokenId={tokenId}
                permissions={permissions}
                onPermissionsChange={setPermissions}
                domainAllowlist={showAllDomains ? undefined : ["light"]}
              />
            )}
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={() => setStep(1)}>Back</button>
              <button className="btn btn-primary" disabled={!grantedEntityId} onClick={() => setStep(3)}>Next</button>
            </div>
          </>
        );
      case 3:
        return (
          <>
            <h3 className="wizard-title">Copy your token</h3>
            <p className="wizard-sub">This is the secret your agent uses to authenticate. You'll also see it on the next step, pre-filled into the connection command. After you finish setup it cannot be retrieved again, so keep a copy somewhere safe.</p>
            {rawToken && (
              <RawTokenDisplay
                rawToken={rawToken}
                note={<p><strong>Keep this token secret.</strong> It grants the access you just configured. You can also copy it on the next step.</p>}
              />
            )}
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={() => setStep(2)}>Back</button>
              <button className="btn btn-primary" onClick={() => setStep(4)}>Next</button>
            </div>
          </>
        );
      case 4:
        return (
          <>
            <h3 className="wizard-title">Connect ATM to your AI agent</h3>
            {rawToken && <ConnectInstructions token={rawToken} />}
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={() => setStep(3)}>Back</button>
              <button className="btn btn-ghost" onClick={() => setStep(6)}>Not now, I'll connect later</button>
              <button className="btn btn-primary" onClick={() => setStep(5)}>I'm set up, test the connection</button>
            </div>
          </>
        );
      case 5:
        return (
          <>
            <h3 className="wizard-title">Test the connection</h3>
            <p className="wizard-sub">Ask your agent to do this. The moment it talks to ATM, we'll detect it.</p>
            <div className="connect-field">
              <span className="connect-field-label">Try</span>
              <code className="connect-field-value">{prompts.read}</code>
              <CopyButton text={prompts.read} label="Copy" />
            </div>
            {prompts.action && (
              <div className="connect-field">
                <span className="connect-field-label">Then</span>
                <code className="connect-field-value">{prompts.action}</code>
                <CopyButton text={prompts.action} label="Copy" />
              </div>
            )}
            {mesaEnforced && (
              <p className="wizard-hint">Your MESA policy may require admin confirmation for control actions, so the read prompt above is enough to confirm the connection.</p>
            )}
            <div className={`wizard-connect-status${connected ? " wizard-connect-status-ok" : ""}`}>
              {connected ? "Connected! Your agent reached ATM." : "Waiting for your agent to connect..."}
            </div>
            <div className="wizard-actions">
              <button className="btn btn-text" onClick={() => setStep(4)}>Back</button>
              <button className="btn btn-primary" disabled={!connected} onClick={() => setStep(6)}>Next</button>
            </div>
          </>
        );
      default:
        return (
          <>
            <h3 className="wizard-title">You're all set</h3>
            <p className="wizard-sub">
              Your token <code>{name}</code> is ready. It is a normal scoped token: you can adjust its
              capabilities and permissions any time from its detail page. When you no longer need it, open
              the token and choose <strong>Revoke</strong>; revoked tokens can then be permanently deleted
              from the Archived list.
            </p>
            <div className="wizard-actions">
              <button className="btn btn-primary" onClick={() => tokenId && onFinish(tokenId)}>Go to my token</button>
            </div>
          </>
        );
    }
  }

  return (
    <div className="view-root wizard-root">
      <div className="wizard-center">
        <Stepper />
        {error && <ErrorMsg msg={error} />}
        <div className="card wizard-body">{renderBody()}</div>
      </div>
    </div>
  );
}
