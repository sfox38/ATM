import { describe, it, expect } from "vitest";
import { diffLines } from "../components/DiffView";

describe("diffLines", () => {
  it("marks no lines changed when identical", () => {
    const { beforeRows, afterRows } = diffLines("a\nb\nc", "a\nb\nc");
    expect(beforeRows.every((r) => !r.changed)).toBe(true);
    expect(afterRows.every((r) => !r.changed)).toBe(true);
  });

  it("marks added lines on the after side only", () => {
    const { beforeRows, afterRows } = diffLines("a\nc", "a\nb\nc");
    expect(beforeRows.find((r) => r.changed)).toBeUndefined();
    expect(afterRows.filter((r) => r.changed).map((r) => r.text)).toEqual(["b"]);
  });

  it("marks removed lines on the before side only", () => {
    const { beforeRows, afterRows } = diffLines("a\nb\nc", "a\nc");
    expect(afterRows.find((r) => r.changed)).toBeUndefined();
    expect(beforeRows.filter((r) => r.changed).map((r) => r.text)).toEqual(["b"]);
  });

  it("handles an empty side", () => {
    const { beforeRows, afterRows } = diffLines("", "a\nb");
    expect(beforeRows).toEqual([]);
    expect(afterRows.every((r) => r.changed)).toBe(true);
  });

  it("skips per-line highlighting past the size guard", () => {
    const big = Array.from({ length: 2000 }, (_, i) => `l${i}`).join("\n");
    const { afterRows } = diffLines(big, big + "\nextra"); // 4001 lines > guard
    expect(afterRows.every((r) => !r.changed)).toBe(true);
  });
});
