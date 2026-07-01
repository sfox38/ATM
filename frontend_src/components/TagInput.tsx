// GitHub-topics-style input for MESA semantic tags. Each chip is one atomic
// canonical tag (namespace.qualifier) displayed dot-free. Entry is canonical-only:
// only tags present in the registry can be committed.
import React, { useId, useMemo, useRef, useState } from "react";

const MAX_DEFAULT = 6;

function splitTag(tag: string): { ns: string; qual: string } {
  const i = tag.indexOf(".");
  return i < 0 ? { ns: "", qual: tag } : { ns: tag.slice(0, i), qual: tag.slice(i + 1) };
}

interface Props {
  value: string[];
  onChange: (tags: string[]) => void;
  canonicalTags: string[];
  recommended?: string[];
  showRecommended?: boolean;
  max?: number;
}

export function TagInput({
  value, onChange, canonicalTags, recommended = [], showRecommended = false, max = MAX_DEFAULT,
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();

  const atMax = value.length >= max;

  // Match on the START of any word (namespace or qualifier, split on . and _),
  // and only after at least one character is typed.
  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/[\s._]+/).filter(Boolean);
    if (terms.length === 0) return [];
    const selected = new Set(value);
    return canonicalTags
      .filter((t) => !selected.has(t))
      .filter((t) => {
        const words = t.toLowerCase().split(/[._]/).filter(Boolean);
        return terms.every((term) => words.some((w) => w.startsWith(term)));
      })
      .slice(0, 10);
  }, [query, canonicalTags, value]);

  function add(tag: string) {
    if (!canonicalTags.includes(tag) || value.includes(tag) || value.length >= max) return;
    onChange([...value, tag]);
    setQuery("");
    setActive(0);
  }

  function removeAt(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }

  function commit() {
    if (matches.length > 0) { add(matches[Math.min(active, matches.length - 1)]); return; }
    const exact = query.trim().toLowerCase();
    if (canonicalTags.includes(exact)) add(exact);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, matches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" || e.key === "Tab" || e.key === ",") {
      if (query.trim() || matches.length) { e.preventDefault(); commit(); }
    }
    else if (e.key === "Escape") { setOpen(false); setQuery(""); }
    else if (e.key === "Backspace" && query === "" && value.length > 0) { removeAt(value.length - 1); }
  }

  const recoToShow = recommended.filter((t) => !value.includes(t) && canonicalTags.includes(t)).slice(0, 8);

  return (
    <div className="tag-input-wrap">
      {showRecommended && recoToShow.length > 0 && (
        <div className="tag-reco">
          {recoToShow.map((t) => {
            const { ns, qual } = splitTag(t);
            return (
              <button type="button" key={t} className="tag-reco-chip" onClick={() => add(t)} title={t}>
                {ns && <span className="tag-chip-ns">{ns}</span>}
                <span className="tag-chip-qual">{qual}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="tag-input" onClick={() => inputRef.current?.focus()}>
        {value.map((t, i) => {
          const { ns, qual } = splitTag(t);
          return (
            <span key={t} className="tag-chip" title={t}>
              {ns && <span className="tag-chip-ns">{ns}</span>}
              <span className="tag-chip-qual">{qual}</span>
              <button type="button" className="tag-chip-x" aria-label={`Remove ${t}`}
                onClick={(e) => { e.stopPropagation(); removeAt(i); }}>&times;</button>
            </span>
          );
        })}
        {!atMax && (
          <input
            ref={inputRef}
            className="tag-input-field"
            value={query}
            placeholder={value.length === 0 ? "Type to search tags..." : ""}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0); }}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 120)}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded={open && matches.length > 0}
            aria-autocomplete="list"
            aria-label="Semantic tags"
            aria-controls={matches.length > 0 ? listboxId : undefined}
            aria-activedescendant={open && matches.length > 0 ? `${listboxId}-option-${Math.min(active, matches.length - 1)}` : undefined}
          />
        )}

        {open && matches.length > 0 && (
          <ul className="tag-suggest" role="listbox" id={listboxId}>
            {matches.map((t, i) => {
              const { ns, qual } = splitTag(t);
              return (
                <li key={t} id={`${listboxId}-option-${i}`} role="option" aria-selected={i === active}
                  className={`tag-suggest-item${i === active ? " active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); add(t); }}
                  onMouseEnter={() => setActive(i)}>
                  <span className="tag-suggest-ns">{ns}</span>
                  <span className="tag-suggest-qual">{qual}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {atMax && <div className="tag-input-note">Maximum {max} tags.</div>}
    </div>
  );
}
