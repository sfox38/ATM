import React, { useCallback, useEffect, useState } from "react";
import type {
  MesaProfileDetail,
  MesaProfileDocument,
  MesaProfileListItem,
  MesaValidationIssue,
} from "../types";
import { api, ApiError } from "../api";
import { Modal } from "../components/Modal";
import { Loading, ErrorMsg, RefreshIcon } from "../index";

const CONTROL_MODES = ["autonomous", "confirm", "read_only", "prohibited"];
const TRIGGERS = ["unknown", "none", "likely", "deployment_defined"];
const REVERSIBILITY_COSTS = ["", "none", "trivial", "moderate", "high"];
const SCOPES = ["", "entity_only", "device_localized", "room_localized", "zone_wide", "deployment_wide"];
const PRIVACY_LEVELS = ["public", "normal", "sensitive", "restricted"];

type ProfileScope = "entity" | "domain" | "area";

const SCOPE_LABEL: Record<ProfileScope, string> = { entity: "Entity ID", domain: "Domain", area: "Area ID" };
const SCOPE_HINT: Record<ProfileScope, string> = {
  entity: "e.g. light.living_room_ceiling",
  domain: "e.g. light (applies to every entity in the domain)",
  area: "e.g. bedroom (applies to every entity in the area)",
};

function controlModeOf(doc: MesaProfileDocument | null): string {
  const ob = (doc?.semantic_profile?.operational_boundaries ?? {}) as Record<string, unknown>;
  return (ob.control_mode as string) ?? "(inherited)";
}

function tagsOf(doc: MesaProfileDocument | null): string[] {
  const tags = doc?.semantic_profile?.semantic_tags;
  return Array.isArray(tags) ? (tags as string[]) : [];
}

interface EditorState {
  key: string;
  tags: string;
  control_mode: string;
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
    tags: tagsOf(doc).join(", "),
    control_mode: (ob.control_mode as string) ?? "autonomous",
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
  if (s.reversible !== "") ob.reversible = s.reversible === "true";
  if (s.reversibility_cost !== "") ob.reversibility_cost = s.reversibility_cost;
  if (s.side_effect_scope !== "") ob.side_effect_scope = s.side_effect_scope;
  const tags = s.tags.split(",").map((t) => t.trim()).filter(Boolean);
  return {
    semantic_profile: { semantic_tags: tags, operational_boundaries: ob },
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

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="toggle-row">
      <div className="toggle-label">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </div>
      {children}
    </div>
  );
}

function ProfileEditor({
  scope,
  profileKey,
  isNew,
  onClose,
  onSaved,
}: {
  scope: ProfileScope;
  profileKey: string | null;
  isNew: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<MesaProfileDetail | null>(null);
  const [state, setState] = useState<EditorState>(docToEditor(profileKey ?? "", null));
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<MesaValidationIssue[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (isNew || !profileKey) return;
    setLoading(true);
    Promise.all([
      loadProfile(scope, profileKey),
      // Effective resolution only makes sense for entities.
      scope === "entity" ? api.getMesaProfile(profileKey) : Promise.resolve(null),
    ])
      .then(([stored, d]) => { setDetail(d); setState(docToEditor(profileKey, stored)); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load profile."))
      .finally(() => setLoading(false));
  }, [scope, profileKey, isNew]);

  function set<K extends keyof EditorState>(key: K, value: EditorState[K]) {
    setState((s) => ({ ...s, [key]: value }));
  }

  async function save() {
    if (!state.key.trim()) { setError(`${SCOPE_LABEL[scope]} is required.`); return; }
    setSaving(true);
    setError(null);
    try {
      const w = await saveProfile(scope, state.key.trim(), editorToDoc(state));
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

  const titleVerb = isNew ? `Add ${scope} profile` : `Edit ${scope}: ${profileKey}`;

  return (
    <Modal titleId="mesa-editor-title" onClose={onClose}>
      <h3 className="modal-title" id="mesa-editor-title">{titleVerb}</h3>
      <div className="mesa-editor-body">
        {error && <ErrorMsg msg={error} />}
        {loading ? <Loading /> : (
          <>
            {isNew && (
              <Field label={SCOPE_LABEL[scope]} hint={SCOPE_HINT[scope]}>
                <input className="input" value={state.key}
                  onChange={(e) => set("key", e.target.value)} />
              </Field>
            )}
            <Field label="Semantic tags" hint="Comma-separated canonical tags, e.g. lighting.ambient">
              <input className="input" value={state.tags}
                onChange={(e) => set("tags", e.target.value)} />
            </Field>
            <Field label="Control mode" hint="How agents may write: autonomous, confirm, read_only, prohibited">
              <select className="input input-auto" value={state.control_mode}
                onChange={(e) => set("control_mode", e.target.value)}>
                {CONTROL_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
            <Field label="Triggers automations" hint="Whether changing this is likely to fire automations">
              <select className="input input-auto" value={state.triggers_automations}
                onChange={(e) => set("triggers_automations", e.target.value)}>
                {TRIGGERS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
            <Field label="Reversible">
              <select className="input input-auto" value={state.reversible}
                onChange={(e) => set("reversible", e.target.value)}>
                <option value="">(unset)</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </Field>
            <Field label="Reversibility cost">
              <select className="input input-auto" value={state.reversibility_cost}
                onChange={(e) => set("reversibility_cost", e.target.value)}>
                {REVERSIBILITY_COSTS.map((m) => <option key={m} value={m}>{m || "(unset)"}</option>)}
              </select>
            </Field>
            <Field label="Side-effect scope">
              <select className="input input-auto" value={state.side_effect_scope}
                onChange={(e) => set("side_effect_scope", e.target.value)}>
                {SCOPES.map((m) => <option key={m} value={m}>{m || "(unset)"}</option>)}
              </select>
            </Field>
            <Field label="Privacy level">
              <select className="input input-auto" value={state.privacy_level}
                onChange={(e) => set("privacy_level", e.target.value)}>
                {PRIVACY_LEVELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>

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
                <summary>Effective resolution</summary>
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
        {!isNew && !confirmDelete && (
          <button
            className="btn btn-danger"
            onClick={() => (scope === "entity" ? remove() : setConfirmDelete(true))}
            disabled={saving}
          >
            Delete
          </button>
        )}
        <button className="btn btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
        <button className="btn btn-primary" onClick={save} disabled={saving || loading}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </Modal>
  );
}

type Editing = { scope: ProfileScope; key: string | null; isNew: boolean };

export function MesaView() {
  const [profiles, setProfiles] = useState<MesaProfileListItem[]>([]);
  const [issues, setIssues] = useState<{ issues: MesaValidationIssue[]; orphans: string[] }>({ issues: [], orphans: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, iss] = await Promise.all([api.listMesaProfiles({ limit: 200 }), api.getMesaIssues()]);
      setProfiles(list.profiles);
      setIssues(iss);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load MESA profiles.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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
            Add profile
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
        Domain and area profiles are edited by key (use the buttons above). The table lists entity-level profiles.
      </p>

      {loading ? <Loading /> : (
        <div className="card">
          {profiles.length === 0 ? (
            <p className="token-table-empty">No entity MESA profiles yet. Add a profile to describe an entity's control mode, automation impact, and privacy to agents.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr><th>Entity</th><th>Control mode</th><th>Tags</th></tr>
              </thead>
              <tbody>
                {profiles.map((p) => (
                  <tr key={p.entity_id} className="clickable"
                    onClick={() => setEditing({ scope: "entity", key: p.entity_id, isNew: false })}>
                    <td><code>{p.entity_id}</code></td>
                    <td>{controlModeOf(p.document)}</td>
                    <td>{tagsOf(p.document).join(", ") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {editing && (
        <ProfileEditor
          scope={editing.scope}
          profileKey={editing.key}
          isNew={editing.isNew}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
