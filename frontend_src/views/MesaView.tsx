import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  EntityTree as EntityTreeData,
  MesaProfileDetail,
  MesaProfileDocument,
  MesaProfileListItem,
  MesaValidationIssue,
  TokenRecord,
} from "../types";
import { api, ApiError } from "../api";
import { Modal } from "../components/Modal";
import { TagInput } from "../components/TagInput";
import { Loading, ErrorMsg, RefreshIcon } from "../index";

// HA domain -> the canonical tag namespace roots that describe an ENTITY of that
// domain, used to surface "Suggested" tags. Intent namespaces (automation, scene)
// describe automations/scenes, not entities, so they're only suggested for those
// domains. Falls back to a general set when unmapped.
const DOMAIN_TAG_ROOTS: Record<string, string[]> = {
  light: ["lighting"],
  switch: ["energy", "resource"],
  climate: ["climate"],
  fan: ["climate"],
  cover: ["security"],
  lock: ["security"],
  alarm_control_panel: ["security"],
  camera: ["security", "diagnostic"],
  binary_sensor: ["presence", "security"],
  sensor: ["energy", "diagnostic", "presence"],
  media_player: ["media", "audio"],
  person: ["person", "presence"],
  device_tracker: ["presence", "person"],
  scene: ["scene"],
  automation: ["automation"],
  script: ["automation"],
  vacuum: ["resource"],
  number: ["helper"],
  select: ["helper"],
  input_boolean: ["helper"],
  input_number: ["helper"],
  input_select: ["helper"],
  input_text: ["helper"],
  input_datetime: ["helper"],
};
const FALLBACK_TAG_ROOTS = ["space", "zone", "diagnostic"];

// Option value plus a human-readable label. The stored value is always the slug
// (what mesa-core expects); the label is only for display.
type Opt = { value: string; label: string };

const CONTROL_MODES: Opt[] = [
  { value: "autonomous", label: "Autonomous" },
  { value: "confirm", label: "Confirm (needs approval)" },
  { value: "read_only", label: "Read-only" },
  { value: "prohibited", label: "Prohibited" },
];
const TRIGGERS: Opt[] = [
  { value: "unknown", label: "Unknown" },
  { value: "none", label: "None" },
  { value: "likely", label: "Likely" },
  { value: "deployment_defined", label: "Deployment-defined" },
];
const REVERSIBILITY_COSTS: Opt[] = [
  { value: "", label: "(unset)" },
  { value: "none", label: "None" },
  { value: "trivial", label: "Trivial" },
  { value: "moderate", label: "Moderate" },
  { value: "high", label: "High" },
];
const SCOPES: Opt[] = [
  { value: "", label: "(unset)" },
  { value: "entity_only", label: "Entity only" },
  { value: "device_localized", label: "Device-localized" },
  { value: "room_localized", label: "Room-localized" },
  { value: "zone_wide", label: "Zone-wide" },
  { value: "deployment_wide", label: "Deployment-wide" },
];
const PRIVACY_LEVELS: Opt[] = [
  { value: "public", label: "Public" },
  { value: "normal", label: "Normal" },
  { value: "sensitive", label: "Sensitive" },
  { value: "restricted", label: "Restricted" },
];
const REVERSIBLE: Opt[] = [
  { value: "", label: "(unset)" },
  { value: "true", label: "Yes" },
  { value: "false", label: "No" },
];
const ENFORCEMENT_MODES: Opt[] = [
  { value: "advisory", label: "Advisory" },
  { value: "enforced", label: "Enforced" },
];

const HELP = {
  entity: "The Home Assistant entity this profile describes. Start typing a name or id to search.",
  domain: "Applies to every entity in this domain unless a more specific (area or entity) profile overrides it.",
  area: "Applies to every entity in this area unless a more specific entity profile overrides it.",
  tags: "Canonical MESA capability tags surfaced to agents. Type to search and pick from the list; each tag is namespaced (e.g. lighting / ambient). Use the suggestions below for this entity's domain.",
  control_mode:
    "How agents may change this entity. Confirm routes writes through admin approval when MESA is enforced; Read-only and Prohibited block writes.",
  enforcement_mode:
    "Advisory: agents are told the control mode but writes still pass. Enforced: ATM actively gates this entity even when the global MESA mode is Advisory. With Confirm control mode, an enforced entity routes writes through admin approval.",
  triggers_automations:
    "Whether changing this entity is likely to fire automations. Drives trigger-validation warnings.",
  reversible: "Whether the effect of a change can be undone.",
  reversibility_cost: "How costly it is to undo a change (time, money, or disruption).",
  side_effect_scope: "How far the physical effect of a change reaches.",
  privacy_level: "Sensitivity of the data this entity exposes to agents.",
};

type ProfileScope = "entity" | "domain" | "area";

const SCOPE_LABEL: Record<ProfileScope, string> = { entity: "Entity", domain: "Domain", area: "Area" };
const SCOPE_PLACEHOLDER: Record<ProfileScope, string> = {
  entity: "Search by name or entity id...",
  domain: "Search domains...",
  area: "Search areas...",
};

function tagsOf(doc: MesaProfileDocument | null): string[] {
  const tags = doc?.semantic_profile?.semantic_tags;
  return Array.isArray(tags) ? (tags as string[]) : [];
}

interface EditorState {
  key: string;
  tags: string[];
  control_mode: string;
  enforcement_mode: string;
  triggers_automations: string;
  reversible: string; // "", "true", "false"
  reversibility_cost: string;
  side_effect_scope: string;
  privacy_level: string;
}

function docToEditor(key: string, doc: MesaProfileDocument | null): EditorState {
  const sp = (doc?.semantic_profile ?? {}) as Record<string, unknown>;
  const ob = (sp.operational_boundaries ?? {}) as Record<string, unknown>;
  const pc = (doc?.privacy_classification ?? {}) as Record<string, unknown>;
  const rev = ob.reversible;
  return {
    key,
    tags: tagsOf(doc),
    control_mode: (ob.control_mode as string) ?? "autonomous",
    enforcement_mode: (ob.enforcement_mode as string) ?? "advisory",
    triggers_automations: (ob.triggers_automations as string) ?? "unknown",
    reversible: rev === true ? "true" : rev === false ? "false" : "",
    reversibility_cost: (ob.reversibility_cost as string) ?? "",
    side_effect_scope: (ob.side_effect_scope as string) ?? "",
    privacy_level: (pc.level as string) ?? "normal",
  };
}

function editorToDoc(s: EditorState): MesaProfileDocument {
  const ob: Record<string, unknown> = {
    control_mode: s.control_mode,
    triggers_automations: s.triggers_automations,
  };
  // Omit when advisory (the default) to keep stored docs clean, matching how
  // mesa-core serialises the field.
  if (s.enforcement_mode === "enforced") ob.enforcement_mode = "enforced";
  if (s.reversible !== "") ob.reversible = s.reversible === "true";
  if (s.reversibility_cost !== "") ob.reversibility_cost = s.reversibility_cost;
  if (s.side_effect_scope !== "") ob.side_effect_scope = s.side_effect_scope;
  return {
    semantic_profile: { semantic_tags: s.tags, operational_boundaries: ob },
    privacy_classification: { level: s.privacy_level },
  };
}

async function loadProfile(scope: ProfileScope, key: string): Promise<MesaProfileDocument | null> {
  if (scope === "entity") return (await api.getMesaProfile(key)).stored;
  if (scope === "domain") return (await api.getMesaDomain(key)).stored;
  return (await api.getMesaArea(key)).stored;
}

async function saveProfile(scope: ProfileScope, key: string, doc: MesaProfileDocument): Promise<MesaValidationIssue[]> {
  if (scope === "entity") return (await api.putMesaProfile(key, doc)).warnings;
  if (scope === "domain") { await api.putMesaDomain(key, doc); return []; }
  await api.putMesaArea(key, doc);
  return [];
}

async function deleteProfile(scope: ProfileScope, key: string): Promise<void> {
  if (scope === "entity") { await api.deleteMesaProfile(key); return; }
  if (scope === "domain") { await api.deleteMesaDomain(key); return; }
  await api.deleteMesaArea(key);
}

// A small "?" badge that reveals brief help on hover/focus. Uses the native
// title attribute so the tooltip is never clipped by the scrolling modal body.
function HelpTip({ text }: { text: string }) {
  return (
    <span className="help-tip" title={text} role="img" aria-label={`Help: ${text}`} tabIndex={0}>?</span>
  );
}

function FieldLabel({ id, text, help }: { id?: string; text: string; help: string }) {
  return (
    <label htmlFor={id} className="mesa-field-label">
      {text}
      <HelpTip text={help} />
    </label>
  );
}

// A select rendered with friendly labels but storing slug values, full width so
// every control lines up on the grid.
function SelectField({
  id, label, help, value, options, onChange,
}: { id: string; label: string; help: string; value: string; options: Opt[]; onChange: (v: string) => void }) {
  return (
    <div className="field">
      <FieldLabel id={id} text={label} help={help} />
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

// Fuzzy combobox over a fixed option set. Selecting an option sets `value` to
// the option's slug; free typing is allowed but the parent validates exactness.
function Combo({
  id, value, options, placeholder, invalid, onChange,
}: {
  id: string;
  value: string;
  options: Opt[];
  placeholder?: string;
  invalid?: boolean;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  useEffect(() => { setQuery(value); }, [value]);

  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const pool = terms.length === 0
      ? options
      : options.filter((o) => {
          const hay = `${o.value} ${o.label}`.toLowerCase();
          return terms.every((t) => hay.includes(t));
        });
    return pool.slice(0, 10);
  }, [query, options]);

  function pick(v: string) {
    onChange(v);
    setQuery(v);
    setOpen(false);
  }

  return (
    <div className="combo">
      <input
        id={id}
        className={`input${invalid ? " error" : ""}`}
        value={query}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        onChange={(e) => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
      />
      {open && matches.length > 0 && (
        <ul className="combo-list" role="listbox">
          {matches.map((o) => (
            <li
              key={o.value}
              className="combo-option"
              role="option"
              aria-selected={o.value === value}
              onMouseDown={(e) => { e.preventDefault(); pick(o.value); }}
            >
              <span className="combo-option-label">{o.label}</span>
              {o.label !== o.value && <code className="combo-option-sub">{o.value}</code>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Shows an entity's EFFECTIVE resolved control_mode/enforcement and which layer
// provides each, so an admin sees when a broader domain/area profile overrides
// the entity-level setting (the resolver is most-restrictive-wins).
function MesaEffectivePanel({ detail }: { detail: MesaProfileDetail }) {
  const exps = detail.explanation?.explanation ?? [];
  const find = (suffix: string) => exps.find((e) => e.field_path.endsWith(suffix));
  const ob = (detail.effective?.semantic_profile as { operational_boundaries?: Record<string, unknown> } | undefined)?.operational_boundaries ?? {};
  const cm = find("control_mode");
  const en = find("enforcement_mode");
  const cmVal = String(cm?.effective_value ?? ob.control_mode ?? "autonomous");
  const enVal = String(en?.effective_value ?? ob.enforcement_mode ?? "advisory");
  const cmLevel = cm?.provided_by_level;
  const enLevel = en?.provided_by_level;
  const overridden = (cmLevel && cmLevel !== "entity") || (enLevel && enLevel !== "entity");
  return (
    <div className="mesa-effective">
      <span className="mesa-effective-title">Effective</span>
      <span>
        control mode <code>{cmVal}</code>{cmLevel && <em> (from {cmLevel})</em>}, enforcement <code>{enVal}</code>{enLevel && <em> (from {enLevel})</em>}
      </span>
      {overridden && (
        <div className="mesa-effective-note">
          A broader profile overrides this entity's setting (most-restrictive layer wins). The effective mode above is what actually applies.
        </div>
      )}
    </div>
  );
}

function ProfileEditor({
  scope,
  profileKey,
  isNew,
  entityTree,
  canonicalTags,
  onClose,
  onSaved,
  tokens,
  returnTokenId,
  onRevealInToken,
}: {
  scope: ProfileScope;
  profileKey: string | null;
  isNew: boolean;
  entityTree: EntityTreeData | null;
  canonicalTags: string[];
  onClose: () => void;
  onSaved: () => void;
  tokens?: TokenRecord[];
  returnTokenId?: string | null;
  onRevealInToken?: (entityId: string, tokenId: string | null) => void;
}) {
  const [detail, setDetail] = useState<MesaProfileDetail | null>(null);
  const [state, setState] = useState<EditorState>(docToEditor(profileKey ?? "", null));
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<MesaValidationIssue[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showReco, setShowReco] = useState(false);
  // Snapshot of the last persisted (or freshly initialised) state, for the
  // unsaved-changes guard.
  const cleanSnapshot = useRef<string>(JSON.stringify(docToEditor(profileKey ?? "", null)));

  useEffect(() => {
    if (isNew || !profileKey) return;
    setLoading(true);
    Promise.all([
      loadProfile(scope, profileKey),
      // Effective resolution only makes sense for entities.
      scope === "entity" ? api.getMesaProfile(profileKey) : Promise.resolve(null),
    ])
      .then(([stored, d]) => {
        setDetail(d);
        const next = docToEditor(profileKey, stored);
        setState(next);
        cleanSnapshot.current = JSON.stringify(next);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load profile."))
      .finally(() => setLoading(false));
  }, [scope, profileKey, isNew]);

  function set<K extends keyof EditorState>(key: K, value: EditorState[K]) {
    setState((s) => ({ ...s, [key]: value }));
  }

  // Valid keys for the current scope, derived from the live registry.
  const keyOptions = useMemo<Opt[]>(() => {
    if (!entityTree) return [];
    if (scope === "domain") {
      return Object.keys(entityTree).sort().map((d) => ({ value: d, label: d }));
    }
    if (scope === "area") {
      const seen = new Map<string, string>();
      for (const dt of Object.values(entityTree)) {
        for (const info of Object.values(dt.entity_details)) {
          if (info.area_id && !seen.has(info.area_id)) seen.set(info.area_id, info.area_name || info.area_id);
        }
      }
      return [...seen.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
    }
    const out: Opt[] = [];
    for (const dt of Object.values(entityTree)) {
      for (const [eid, info] of Object.entries(dt.entity_details)) {
        out.push({ value: eid, label: info.friendly_name || eid });
      }
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [entityTree, scope]);

  const keyValid = !isNew || keyOptions.some((o) => o.value === state.key.trim());
  const keyInvalidShown = isNew && state.key.trim() !== "" && !keyValid;
  const dirty = JSON.stringify(state) !== cleanSnapshot.current;
  const canSave = !saving && !loading && keyValid;

  // Suggested tags for this scope's domain, ordered by root priority and
  // interleaved so the most relevant namespace leads (not alphabetical, which
  // would let an early root like "automation" crowd out "lighting").
  const recommendedTags = useMemo(() => {
    const domain = scope === "entity" ? state.key.split(".")[0] : scope === "domain" ? state.key.trim() : "";
    let roots = DOMAIN_TAG_ROOTS[domain] ?? FALLBACK_TAG_ROOTS;
    let byRoot = roots.map((r) => canonicalTags.filter((t) => t.split(".")[0] === r));
    if (byRoot.every((l) => l.length === 0)) {
      roots = FALLBACK_TAG_ROOTS;
      byRoot = roots.map((r) => canonicalTags.filter((t) => t.split(".")[0] === r));
    }
    const out: string[] = [];
    for (let col = 0; out.length < 8; col++) {
      let advanced = false;
      for (const list of byRoot) {
        if (list[col]) { out.push(list[col]); advanced = true; if (out.length >= 8) break; }
      }
      if (!advanced) break;
    }
    return out;
  }, [scope, state.key, canonicalTags]);

  function attemptClose() {
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    onClose();
  }

  async function save() {
    if (!keyValid) { setError(`Choose a valid ${SCOPE_LABEL[scope].toLowerCase()}.`); return; }
    setSaving(true);
    setError(null);
    try {
      const w = await saveProfile(scope, state.key.trim(), editorToDoc(state));
      cleanSnapshot.current = JSON.stringify(state);
      setWarnings(w);
      if (w.length === 0) { onSaved(); onClose(); }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to save profile.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!profileKey) return;
    setSaving(true);
    try {
      await deleteProfile(scope, profileKey);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete profile.");
      setSaving(false);
    }
  }

  const titleVerb = isNew ? `Add ${SCOPE_LABEL[scope].toLowerCase()} profile` : `Edit ${scope}: ${profileKey}`;

  // The origin token to jump back to, only when it is scoped (pass-through tokens
  // have no permissions tree). Profiles opened directly from the MESA list have
  // no origin, so the footer link is simply absent.
  const originToken = returnTokenId ? (tokens ?? []).find((t) => t.id === returnTokenId) ?? null : null;
  const roundTripToken = originToken && !originToken.pass_through ? originToken : null;

  return (
    <Modal titleId="mesa-editor-title" onClose={attemptClose}>
      <h3 className="modal-title" id="mesa-editor-title">{titleVerb}</h3>
      <div className="mesa-editor-body">
        {error && <ErrorMsg msg={error} />}
        {loading ? <Loading /> : (
          <>
            {scope === "entity" && !isNew && detail && <MesaEffectivePanel detail={detail} />}
            {isNew && (
              <div className="field">
                <FieldLabel id="mesa-key" text={SCOPE_LABEL[scope]} help={HELP[scope]} />
                <Combo
                  id="mesa-key"
                  value={state.key}
                  options={keyOptions}
                  placeholder={SCOPE_PLACEHOLDER[scope]}
                  invalid={keyInvalidShown}
                  onChange={(v) => set("key", v)}
                />
                {keyInvalidShown && (
                  <span className="field-error">No matching {SCOPE_LABEL[scope].toLowerCase()}. Pick one from the list.</span>
                )}
              </div>
            )}

            <div className="field">
              <div className="mesa-taglabel-row">
                <FieldLabel id="mesa-tags" text="Semantic tags" help={HELP.tags} />
                {recommendedTags.length > 0 && (
                  <button type="button" className="link-btn" onClick={() => setShowReco((s) => !s)}>
                    {showReco ? "Hide suggestions" : "Show suggestions"}
                  </button>
                )}
              </div>
              <TagInput
                value={state.tags}
                onChange={(t) => set("tags", t)}
                canonicalTags={canonicalTags}
                recommended={recommendedTags}
                showRecommended={showReco}
              />
            </div>

            <div className="mesa-grid">
              <SelectField id="mesa-cm" label="Control mode" help={HELP.control_mode}
                value={state.control_mode} options={CONTROL_MODES} onChange={(v) => set("control_mode", v)} />
              <SelectField id="mesa-em" label="Enforcement" help={HELP.enforcement_mode}
                value={state.enforcement_mode} options={ENFORCEMENT_MODES} onChange={(v) => set("enforcement_mode", v)} />
              <SelectField id="mesa-ta" label="Triggers automations" help={HELP.triggers_automations}
                value={state.triggers_automations} options={TRIGGERS} onChange={(v) => set("triggers_automations", v)} />
              <SelectField id="mesa-rev" label="Reversible" help={HELP.reversible}
                value={state.reversible} options={REVERSIBLE} onChange={(v) => set("reversible", v)} />
              <SelectField id="mesa-rc" label="Reversibility cost" help={HELP.reversibility_cost}
                value={state.reversibility_cost} options={REVERSIBILITY_COSTS} onChange={(v) => set("reversibility_cost", v)} />
              <SelectField id="mesa-ses" label="Side-effect scope" help={HELP.side_effect_scope}
                value={state.side_effect_scope} options={SCOPES} onChange={(v) => set("side_effect_scope", v)} />
              <SelectField id="mesa-pl" label="Privacy level" help={HELP.privacy_level}
                value={state.privacy_level} options={PRIVACY_LEVELS} onChange={(v) => set("privacy_level", v)} />
            </div>

            {warnings.length > 0 && (
              <div className="banner banner-warn">
                <strong>Saved with trigger-validation warnings:</strong>
                <ul>
                  {warnings.map((w, i) => (
                    <li key={i}>{w.recommendation} (automation {w.automation_id}, {w.role})</li>
                  ))}
                </ul>
                <button className="btn btn-sm" onClick={() => { onSaved(); onClose(); }}>Dismiss</button>
              </div>
            )}

            {confirmDelete && (
              <div className="banner banner-warn">
                <strong>Delete this {scope} profile?</strong>
                <p>
                  {scope === "domain"
                    ? `Every entity in the "${profileKey}" domain that inherits from this profile will fall back to the next level (area, deployment defaults, then the built-in safety baseline). This can change the effective control mode for many entities at once.`
                    : `Every entity in the "${profileKey}" area that inherits from this profile will fall back to deployment defaults or the built-in baseline.`}
                </p>
                <div className="modal-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)} disabled={saving}>Cancel</button>
                  <button className="btn btn-danger btn-sm" onClick={remove} disabled={saving}>
                    {saving ? "Deleting..." : `Delete ${scope} profile`}
                  </button>
                </div>
              </div>
            )}

            {!isNew && scope === "entity" && detail && (
              <details className="mesa-explain">
                <summary>Effective resolution <HelpTip text="The value that actually applies for each policy field after MESA resolves inheritance (entity, then area, then domain, then deployment defaults, then the built-in baseline). 'From' shows which level supplied the value, 'Origin' who authored it." /></summary>
                <table className="data-table">
                  <thead><tr><th>Field</th><th>Effective</th><th>From</th><th>Origin</th></tr></thead>
                  <tbody>
                    {detail.explanation.explanation.map((row) => (
                      <tr key={row.field_path}>
                        <td><code>{row.field_path}</code></td>
                        <td>{String(row.effective_value)}</td>
                        <td>{row.provided_by_level}</td>
                        <td>{row.provided_by_origin}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </>
        )}
      </div>
      <div className="modal-actions">
        {scope === "entity" && !isNew && profileKey && onRevealInToken && roundTripToken && (
          <button
            type="button"
            className="link-btn mesa-tree-link"
            onClick={() => onRevealInToken(profileKey, roundTripToken.id)}
          >
            View in permissions tree...
          </button>
        )}
        {!isNew && !confirmDelete && (
          <button
            className="btn btn-danger"
            onClick={() => (scope === "entity" ? remove() : setConfirmDelete(true))}
            disabled={saving}
          >
            Delete
          </button>
        )}
        <button className="btn btn-ghost" onClick={attemptClose} disabled={saving}>Cancel</button>
        <button className="btn btn-primary" onClick={save} disabled={!canSave}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </Modal>
  );
}

// Control-mode display metadata, reusing the shared badge palette.
const CONTROL_MODE_META: Record<string, { label: string; cls: string }> = {
  autonomous: { label: "Autonomous", cls: "badge-green" },
  confirm: { label: "Confirm", cls: "badge-amber" },
  read_only: { label: "Read-only", cls: "badge-grey" },
  prohibited: { label: "Prohibited", cls: "badge-red" },
};

function rawControlMode(doc: MesaProfileDocument | null): string {
  const ob = (doc?.semantic_profile?.operational_boundaries ?? {}) as Record<string, unknown>;
  return (ob.control_mode as string) ?? "inherited";
}

function isEnforced(doc: MesaProfileDocument | null): boolean {
  const ob = (doc?.semantic_profile?.operational_boundaries ?? {}) as Record<string, unknown>;
  return ob.enforcement_mode === "enforced";
}

function domainOf(entityId: string): string {
  return entityId.split(".")[0] || "other";
}

function ControlBadge({ mode }: { mode: string }) {
  const meta = CONTROL_MODE_META[mode] ?? { label: "Inherited", cls: "badge-grey" };
  return <span className={`badge ${meta.cls}`}>{meta.label}</span>;
}

type Editing = { scope: ProfileScope; key: string | null; isNew: boolean };

export function MesaView({ openProfileEntityId, onProfileOpened, tokens, returnTokenId, onRevealInToken }: {
  openProfileEntityId?: string | null;
  onProfileOpened?: () => void;
  tokens?: TokenRecord[];
  returnTokenId?: string | null;
  onRevealInToken?: (entityId: string, tokenId: string | null) => void;
} = {}) {
  const [profiles, setProfiles] = useState<MesaProfileListItem[]>([]);
  const [issues, setIssues] = useState<{ issues: MesaValidationIssue[]; orphans: string[] }>({ issues: [], orphans: [] });
  const [entityTree, setEntityTree] = useState<EntityTreeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [domains, setDomains] = useState<{ domain: string; document: MesaProfileDocument }[]>([]);
  const [areas, setAreas] = useState<{ area_id: string; document: MesaProfileDocument }[]>([]);
  const [canonicalTags, setCanonicalTags] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");  // "" = all; a control_mode value; or "enforced"
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, iss, doms, ars] = await Promise.all([
        api.listMesaProfiles({ limit: 200 }),
        api.getMesaIssues(),
        api.listMesaDomains().catch(() => ({ domains: [] })),
        api.listMesaAreas().catch(() => ({ areas: [] })),
      ]);
      setProfiles(list.profiles);
      setIssues(iss);
      setDomains(doms.domains);
      setAreas(ars.areas);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load MESA profiles.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  // Load the registry once for the editor's fuzzy key search + validation, and
  // so the list can search/show friendly names.
  useEffect(() => { api.getEntityTree().then(setEntityTree).catch(() => null); }, []);
  // The canonical MESA tag vocabulary powers the tag-input autocomplete.
  useEffect(() => { api.getMesaVocabulary().then((v) => setCanonicalTags(v.canonical_tags)).catch(() => null); }, []);

  // Deep-link from a token card: open the entity editor once profiles have
  // loaded (so we know whether to open it as edit or create).
  const [pendingOpen, setPendingOpen] = useState<string | null>(openProfileEntityId ?? null);
  useEffect(() => { if (openProfileEntityId) setPendingOpen(openProfileEntityId); }, [openProfileEntityId]);
  useEffect(() => {
    if (!pendingOpen || loading) return;
    const exists = profiles.some((p) => p.entity_id === pendingOpen);
    setEditing({ scope: "entity", key: pendingOpen, isNew: !exists });
    setPendingOpen(null);
    onProfileOpened?.();
  }, [pendingOpen, loading, profiles, onProfileOpened]);

  const friendly = useCallback((eid: string): string => {
    return entityTree?.[domainOf(eid)]?.entity_details[eid]?.friendly_name ?? "";
  }, [entityTree]);

  // Manage-by-exception summary: tally control modes (+ enforced) across all profiles.
  const counts = useMemo(() => {
    const c: Record<string, number> = { autonomous: 0, confirm: 0, read_only: 0, prohibited: 0, inherited: 0, enforced: 0 };
    for (const p of profiles) {
      const m = rawControlMode(p.document);
      c[m] = (c[m] ?? 0) + 1;
      if (isEnforced(p.document)) c.enforced += 1;
    }
    return c;
  }, [profiles]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return profiles.filter((p) => {
      if (filter === "enforced") { if (!isEnforced(p.document)) return false; }
      else if (filter && rawControlMode(p.document) !== filter) return false;
      if (!q) return true;
      const hay = `${p.entity_id} ${friendly(p.entity_id)} ${tagsOf(p.document).join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [profiles, search, filter, friendly]);

  // Group filtered profiles by domain; gated entities float to the top of each group.
  const groups = useMemo(() => {
    const m = new Map<string, MesaProfileListItem[]>();
    for (const p of filtered) {
      const d = domainOf(p.entity_id);
      const arr = m.get(d);
      if (arr) arr.push(p); else m.set(d, [p]);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) => {
        const aAuto = rawControlMode(a.document) === "autonomous" ? 1 : 0;
        const bAuto = rawControlMode(b.document) === "autonomous" ? 1 : 0;
        return aAuto - bAuto || a.entity_id.localeCompare(b.entity_id);
      });
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  function toggleGroup(d: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(d)) next.delete(d); else next.add(d);
      return next;
    });
  }

  const chips = [
    { key: "confirm", label: "Confirm", n: counts.confirm },
    { key: "prohibited", label: "Prohibited", n: counts.prohibited },
    { key: "read_only", label: "Read-only", n: counts.read_only },
    { key: "enforced", label: "Enforced", n: counts.enforced },
    { key: "inherited", label: "Inherited", n: counts.inherited },
    { key: "autonomous", label: "Autonomous", n: counts.autonomous },
  ].filter((c) => c.n > 0);

  return (
    <div className="view-root">
      <div className="filter-row">
        <div className="filter-row-right">
          <button className="btn btn-ghost btn-sm btn-icon" onClick={refresh} aria-label="Refresh"><RefreshIcon /></button>
          <button className="btn btn-ghost btn-sm" onClick={() => setEditing({ scope: "area", key: null, isNew: true })}>
            Add area profile
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setEditing({ scope: "domain", key: null, isNew: true })}>
            Add domain profile
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setEditing({ scope: "entity", key: null, isNew: true })}>
            Add entity profile
          </button>
        </div>
      </div>

      {error && <ErrorMsg msg={error} />}

      {(issues.issues.length > 0 || issues.orphans.length > 0) && (
        <div className="banner banner-warn">
          {issues.issues.length > 0 && (
            <div>
              <strong>{issues.issues.length} trigger-validation issue(s):</strong>
              <ul>
                {issues.issues.map((i, idx) => (
                  <li key={idx}><code>{i.entity_id}</code> declared <code>{i.declared_value}</code> but appears in automation {i.automation_id} ({i.role})</li>
                ))}
              </ul>
            </div>
          )}
          {issues.orphans.length > 0 && (
            <div>
              <strong>{issues.orphans.length} orphaned profile(s)</strong> (entity no longer exists): {issues.orphans.join(", ")}
            </div>
          )}
        </div>
      )}

      <p className="mesa-scope-note">
        The list shows entity-level profiles, grouped by domain. Domain and area profiles cascade to many entities and are listed separately below.
      </p>

      {(domains.length > 0 || areas.length > 0) && (
        <div className="card mesa-scope-card">
          <div className="mesa-scope-card-title">Domain &amp; area profiles</div>
          <p className="mesa-scope-card-sub">
            These cascade to every entity in the domain or area unless a more specific profile overrides them. Click to edit.
          </p>
          <div className="mesa-scope-list">
            {domains.map((d) => (
              <button key={`dom:${d.domain}`} className="mesa-scope-pill"
                onClick={() => setEditing({ scope: "domain", key: d.domain, isNew: false })}>
                <span className="mesa-scope-kind">domain</span>
                <code>{d.domain}</code>
                <ControlBadge mode={rawControlMode(d.document)} />
                {isEnforced(d.document) && <span className="badge badge-blue">Enforced</span>}
              </button>
            ))}
            {areas.map((a) => (
              <button key={`area:${a.area_id}`} className="mesa-scope-pill"
                onClick={() => setEditing({ scope: "area", key: a.area_id, isNew: false })}>
                <span className="mesa-scope-kind">area</span>
                <code>{a.area_id}</code>
                <ControlBadge mode={rawControlMode(a.document)} />
                {isEnforced(a.document) && <span className="badge badge-blue">Enforced</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {profiles.length > 0 && (
        <div className="mesa-controls">
          <div className="mesa-summary" role="group" aria-label="Filter by control mode">
            <button className={`mesa-chip${filter === "" ? " mesa-chip-active" : ""}`} onClick={() => setFilter("")}>
              All <span className="mesa-chip-count">{profiles.length}</span>
            </button>
            {chips.map((c) => (
              <button key={c.key}
                className={`mesa-chip${filter === c.key ? " mesa-chip-active" : ""}`}
                onClick={() => setFilter(filter === c.key ? "" : c.key)}>
                {c.label} <span className="mesa-chip-count">{c.n}</span>
              </button>
            ))}
          </div>
          <input className="input mesa-search" placeholder="Search entity, name, or tag..."
            value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search profiles" />
        </div>
      )}

      {loading ? <Loading /> : profiles.length === 0 ? (
        <div className="card">
          <p className="token-table-empty">No entity MESA profiles yet. Add a profile to describe an entity's control mode, automation impact, and privacy to agents.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card"><p className="token-table-empty">No profiles match your filter.</p></div>
      ) : (
        <div className="mesa-groups">
          {groups.map(([domain, items]) => {
            const isCollapsed = collapsed.has(domain);
            return (
              <div key={domain} className="card mesa-group">
                <button className="mesa-group-header" onClick={() => toggleGroup(domain)} aria-expanded={!isCollapsed}>
                  <span className={`mesa-group-caret${isCollapsed ? " collapsed" : ""}`} aria-hidden="true">&#9662;</span>
                  <code>{domain}</code>
                  <span className="mesa-group-count">{items.length}</span>
                </button>
                {!isCollapsed && (
                  <table className="data-table">
                    <tbody>
                      {items.map((p) => (
                        <tr key={p.entity_id} className="clickable"
                          onClick={() => setEditing({ scope: "entity", key: p.entity_id, isNew: false })}>
                          <td>
                            <div className="mesa-row-name">{friendly(p.entity_id) || p.entity_id}</div>
                            {friendly(p.entity_id) && <code className="mesa-row-id">{p.entity_id}</code>}
                          </td>
                          <td className="mesa-row-modes">
                            <ControlBadge mode={rawControlMode(p.document)} />
                            {isEnforced(p.document) && <span className="badge badge-blue">Enforced</span>}
                          </td>
                          <td className="mesa-row-tags">{tagsOf(p.document).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
        </div>
      )}

      {editing && (
        <ProfileEditor
          scope={editing.scope}
          profileKey={editing.key}
          isNew={editing.isNew}
          entityTree={entityTree}
          canonicalTags={canonicalTags}
          onClose={() => setEditing(null)}
          onSaved={refresh}
          tokens={tokens}
          returnTokenId={returnTokenId}
          onRevealInToken={onRevealInToken}
        />
      )}
    </div>
  );
}
