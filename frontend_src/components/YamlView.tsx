/** Read-only YAML viewer for configuration snapshots. Prefers HA's native
 * <ha-code-editor> (CodeMirror: monospace, syntax highlighting, scrollbars) and
 * falls back to a styled <pre> when that element is not registered. */
import React, { useEffect, useRef, useState } from "react";
import yaml from "js-yaml";

export function toYaml(value: Record<string, unknown> | null): string {
  if (value == null) return "";
  try {
    return yaml.dump(value, { indent: 2, lineWidth: 100, sortKeys: false, noRefs: true });
  } catch {
    // Fall back to JSON if the structure is somehow not YAML-serialisable.
    return JSON.stringify(value, null, 2);
  }
}

export function YamlView({ value }: { value: string }) {
  const ref = useRef<HTMLElement | null>(null);
  // Decide once per mount: native editor only if HA has registered it.
  const [useEditor] = useState(() => !!customElements.get("ha-code-editor"));

  useEffect(() => {
    if (!useEditor || !ref.current) return;
    const el = ref.current as unknown as Record<string, unknown>;
    el.mode = "yaml";
    el.readOnly = true;
    el.linewrap = true;
    el.value = value;
  }, [useEditor, value]);

  if (!value) return <pre className="yaml-pre yaml-pre-empty">(none)</pre>;

  if (useEditor) {
    return <ha-code-editor ref={ref as React.RefObject<HTMLElement>} className="yaml-editor" />;
  }
  return <pre className="yaml-pre">{value}</pre>;
}
