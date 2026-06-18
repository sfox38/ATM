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
