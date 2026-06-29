/** Shared before/after pane, used by approval review and configuration version history. */
import React from "react";

export function BeforeAfter({ before, after }: { before: string | null; after: string | null }) {
  return (
    <div className="approval-diff-cols">
      <div className="approval-diff-col">
        <div className="approval-diff-label">Before</div>
        <pre className="approval-pre">{before ?? "(none)"}</pre>
      </div>
      <div className="approval-diff-col">
        <div className="approval-diff-label">After</div>
        <pre className="approval-pre">{after ?? "(none)"}</pre>
      </div>
    </div>
  );
}

export interface DiffLine {
  text: string;
  changed: boolean;
}

// Above this combined line count we skip the O(m*n) LCS and render both sides
// plainly (no per-line highlight), so a large raw file cannot stall the panel.
const MAX_DIFF_LINES = 3000;

/** Line-level diff of two text blocks via LCS, returning each side's lines with a
 * `changed` flag (a removed line on the before side, an added line on the after
 * side). Used for raw file / configuration.yaml version snapshots. */
export function diffLines(before: string, after: string): { beforeRows: DiffLine[]; afterRows: DiffLine[] } {
  const a = before === "" ? [] : before.split("\n");
  const b = after === "" ? [] : after.split("\n");
  if (a.length + b.length > MAX_DIFF_LINES) {
    return {
      beforeRows: a.map((text) => ({ text, changed: false })),
      afterRows: b.map((text) => ({ text, changed: false })),
    };
  }
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const beforeRows: DiffLine[] = [];
  const afterRows: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      beforeRows.push({ text: a[i], changed: false });
      afterRows.push({ text: b[j], changed: false });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      beforeRows.push({ text: a[i], changed: true });
      i++;
    } else {
      afterRows.push({ text: b[j], changed: true });
      j++;
    }
  }
  while (i < m) beforeRows.push({ text: a[i++], changed: true });
  while (j < n) afterRows.push({ text: b[j++], changed: true });
  return { beforeRows, afterRows };
}

/** Renders one side of a raw-text line diff; changed lines are tinted by tone. */
export function RawDiffPane({ rows, tone }: { rows: DiffLine[]; tone: "remove" | "add" }) {
  if (rows.length === 0) return <pre className="yaml-pre yaml-pre-empty">(empty)</pre>;
  return (
    <pre className="yaml-pre raw-diff">
      {rows.map((r, idx) => (
        <div key={idx} className={r.changed ? `diff-line diff-${tone}` : "diff-line"}>
          {r.text === "" ? " " : r.text}
        </div>
      ))}
    </pre>
  );
}
