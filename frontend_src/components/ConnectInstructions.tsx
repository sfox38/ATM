// The "connect ATM to your agent" UI: editable URL, token, per-agent tabs with
// prefilled commands/configs. Shared by the onboarding wizard's Connect step and
// the standard token-created modal so both show identical, copy-ready instructions.
import React, { useState } from "react";
import { copyToClipboard } from "../utils";
import { buildAgentTabs, buildMcpUrl } from "../wizard_helpers";

// A full-width value box (read-only or editable) with the Copy button inside it,
// matching the look of the command/JSON blocks.
export function CopyCodeBox(
  { label, value, onChange }: { label?: string; value: string; onChange?: (v: string) => void },
) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await copyToClipboard(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <div className="command-field">
      {label && <div className="command-field-label">{label}</div>}
      <div className="command-block-codewrap">
        <button className="btn btn-primary btn-sm wizard-copy-btn command-copy" onClick={copy}>
          {copied ? "Copied!" : "Copy"}
        </button>
        {onChange
          ? <input className="command-block-input" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} />
          : <pre className="command-block-code">{value}</pre>}
      </div>
    </div>
  );
}

function CommandBlock(
  { title, hint, code, fields }:
  { title?: string; hint?: string; code?: string; fields?: { label: string; value: string }[]; },
) {
  return (
    <div className="command-block">
      {title && <div className="command-block-title">{title}</div>}
      {hint && <small className="wizard-hint">{hint}</small>}
      {fields && fields.map((f) => <CopyCodeBox key={f.label} label={f.label} value={f.value} />)}
      {code && <CopyCodeBox value={code} />}
    </div>
  );
}

export function ConnectInstructions({ token }: { token: string }) {
  const [mcpUrl, setMcpUrl] = useState(buildMcpUrl(window.location.origin));
  const [agentTab, setAgentTab] = useState("claude");
  const tabs = buildAgentTabs(mcpUrl, token);
  const current = tabs.find((t) => t.key === agentTab) ?? tabs[0];
  return (
    <div className="connect-instructions">
      <p className="wizard-sub">
        Add ATM as an MCP server in your agent. ATM authenticates with the header
        {" "}<code>Authorization: Bearer &lt;token&gt;</code>. It does not use API keys or OAuth, so
        don't let your agent try to "log in" to Home Assistant.
      </p>
      <CopyCodeBox label="MCP server URL" value={mcpUrl} onChange={setMcpUrl} />
      <small className="wizard-hint">This is the address you are using right now. If your agent runs on another machine or network, replace it with the URL that machine can reach (for example your Nabu Casa or external HTTPS URL).</small>
      <CopyCodeBox label="Token" value={token} />

      <div className="wizard-tabs-label">Pick your agent</div>
      <div className="wizard-tabs" role="tablist" aria-label="Agent">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={current?.key === t.key}
            className={`wizard-tab${current?.key === t.key ? " wizard-tab-active" : ""}`}
            onClick={() => setAgentTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {current && (
        <div className="wizard-tab-panel">
          {current.intro && <p className="wizard-sub">{current.intro}</p>}
          {current.blocks.map((b, i) => (
            <CommandBlock key={i} title={b.title} hint={b.hint} code={b.code} fields={b.fields} />
          ))}
          <a className="btn btn-text btn-sm" href={current.href} target="_blank" rel="noopener noreferrer">
            Open the {current.label} setup guide
          </a>
        </div>
      )}
    </div>
  );
}
