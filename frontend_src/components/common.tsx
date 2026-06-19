/** Shared presentational helpers used across panel views and the in-context
 * profile modal. Kept out of index.tsx so reusing them (e.g. ProfileEditor in
 * the injected quick-add modal) does not pull the whole panel app into a chunk.
 */
import React from "react";

export function Loading() {
  return (
    <div className="loading-wrap">
      <div className="spinner" />
      <span>Loading...</span>
    </div>
  );
}

export function ErrorMsg({ msg }: { msg: string }) {
  return <div className="banner banner-error">{msg}</div>;
}

export function RefreshIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ display: "block" }}>
      <polyline points="1 4 1 10 7 10" />
      <polyline points="23 20 23 14 17 14" />
      <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15" />
    </svg>
  );
}
