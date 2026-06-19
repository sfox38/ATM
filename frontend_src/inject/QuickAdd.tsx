/** In-context MESA profile quick-add modal, injected into HA's native config
 * pages. Defines <atm-mesa-quick-add>, which mounts the panel's ProfileEditor in
 * its own shadow root so an admin can create or edit an entity profile without
 * leaving the page. Lazy-loaded by inject/index.ts on first use; reuses the real
 * editor, validation, and admin API with no duplication.
 *
 * The host element is fixed and full-viewport (the shared .modal-backdrop is
 * position:absolute, so it needs a fixed container to overlay the whole page).
 */
import React, { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { EntityTree } from "../types";
import { api } from "../api";
import { ProfileEditor } from "../views/MesaView";
import { Modal } from "../components/Modal";
import { Loading, ErrorMsg } from "../components/common";
import PANEL_CSS from "../atm-panel.css?inline";

const TAG = "atm-mesa-quick-add";

export type QuickAddScope = "entity" | "area";

export function QuickAddApp({
  scope,
  profileKey,
  isNew,
  onClose,
  onSaved,
}: {
  scope: QuickAddScope;
  profileKey: string;
  isNew: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [entityTree, setEntityTree] = useState<EntityTree | null>(null);
  const [tags, setTags] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ProfileEditor needs the registry (for its key list + friendly names) and the
  // canonical tag vocabulary. Fetch both before mounting it.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getEntityTree(), api.getMesaVocabulary()])
      .then(([tree, vocab]) => {
        if (cancelled) return;
        setEntityTree(tree);
        setTags(vocab.canonical_tags);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load the entity registry.");
      });
    return () => { cancelled = true; };
  }, []);

  if (error) {
    return (
      <Modal titleId="atm-qa-title" onClose={onClose}>
        <h3 className="modal-title" id="atm-qa-title">MESA profile</h3>
        <ErrorMsg msg={error} />
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>Close</button>
        </div>
      </Modal>
    );
  }

  if (!entityTree || tags === null) {
    return (
      <Modal titleId="atm-qa-title" onClose={onClose}>
        <h3 className="modal-title" id="atm-qa-title">MESA profile</h3>
        <Loading />
      </Modal>
    );
  }

  return (
    <ProfileEditor
      scope={scope}
      profileKey={profileKey}
      isNew={isNew}
      entityTree={entityTree}
      canonicalTags={tags}
      onClose={onClose}
      onSaved={onSaved}
      lockedKey
    />
  );
}

class QuickAddElement extends HTMLElement {
  private _root: Root | null = null;

  connectedCallback() {
    const scope: QuickAddScope = this.getAttribute("scope") === "area" ? "area" : "entity";
    const key = this.getAttribute("key") || "";
    // The injector sets has-profile="1" when a stored profile exists, so the
    // editor opens in edit mode (loads + shows Delete) rather than create mode.
    const isNew = this.getAttribute("has-profile") !== "1";
    if (!key) {
      this.remove();
      return;
    }

    // Fixed full-viewport, but transparent: PANEL_CSS paints :host with the panel
    // background, which would otherwise cover the page in solid white. Transparent
    // lets the shared .modal-backdrop (dim + blur) show the HA page behind it.
    this.style.cssText = "position:fixed; inset:0; z-index:2147483600; background:transparent;";
    this._applyTheme();
    const shadow = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = PANEL_CSS;
    shadow.appendChild(style);
    const mount = document.createElement("div");
    mount.style.height = "100%";
    shadow.appendChild(mount);

    this._root = createRoot(mount);
    this._root.render(
      <QuickAddApp
        scope={scope}
        profileKey={key}
        isNew={isNew}
        onClose={() => this._close()}
        onSaved={() =>
          this.dispatchEvent(
            new CustomEvent("atm-mesa-saved", {
              detail: { scope, key },
              bubbles: true,
              composed: true,
            })
          )
        }
      />
    );
  }

  // Match HA's current theme: PANEL_CSS only switches to dark via the
  // .atm-theme-dark class (or an OS prefers-color-scheme match), but HA's dark
  // mode is its own setting, so read it from the page hass like the panel does.
  private _applyTheme() {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const dark = (document.querySelector("home-assistant") as any)?.hass?.themes?.darkMode;
      if (dark === true) this.classList.add("atm-theme-dark");
      else if (dark === false) this.classList.add("atm-theme-light");
    } catch {
      // Leave to prefers-color-scheme.
    }
  }

  private _close() {
    this._root?.unmount();
    this._root = null;
    this.remove();
  }

  disconnectedCallback() {
    this._root?.unmount();
    this._root = null;
  }
}

export function defineQuickAdd() {
  if (!customElements.get(TAG)) customElements.define(TAG, QuickAddElement);
}

defineQuickAdd();
