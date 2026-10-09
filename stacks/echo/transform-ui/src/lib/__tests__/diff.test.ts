// Line diff (UIFPE-219) — display-only, but the Review step's trust anchor
// renders through it, so the line accounting is pinned: a diff that drops or
// duplicates a line would misrepresent the compiler's own answer.
import { describe, expect, it } from "vitest";
import { diffHunks, diffLines, diffStats } from "../diff";

const A = ["one", "two", "three", "four", "five"].join("\n");

describe("diffLines", () => {
  it("identical texts are all-same with aligned numbering", () => {
    const rows = diffLines(A, A);
    expect(rows.every((r) => r.kind === "same")).toBe(true);
    expect(rows.map((r) => r.a)).toEqual([1, 2, 3, 4, 5]);
    expect(rows.map((r) => r.b)).toEqual([1, 2, 3, 4, 5]);
  });

  it("a pure insertion adds without deleting, numbering both sides", () => {
    const b = ["one", "two", "NEW", "three", "four", "five"].join("\n");
    const rows = diffLines(A, b);
    expect(diffStats(rows)).toEqual({ added: 1, removed: 0 });
    const add = rows.find((r) => r.kind === "add")!;
    expect(add.text).toBe("NEW");
    expect(add.a).toBeNull();
    expect(add.b).toBe(3);
    // trailing common lines keep BOTH counters correct after the shift
    const last = rows.at(-1)!;
    expect(last).toMatchObject({ kind: "same", a: 5, b: 6 });
  });

  it("a replacement is a del/add pair, not a mangled same", () => {
    const b = A.replace("three", "tres");
    const rows = diffLines(A, b);
    expect(diffStats(rows)).toEqual({ added: 1, removed: 1 });
    expect(rows.find((r) => r.kind === "del")!.text).toBe("three");
    expect(rows.find((r) => r.kind === "add")!.text).toBe("tres");
  });

  it("every before-line and after-line appears exactly once", () => {
    const b = ["zero", "two", "three", "3.5", "five", "six"].join("\n");
    const rows = diffLines(A, b);
    expect(rows.filter((r) => r.kind !== "add").map((r) => r.text).join("\n"))
      .toBe(A);
    expect(rows.filter((r) => r.kind !== "del").map((r) => r.text).join("\n"))
      .toBe(b);
  });
});

describe("diffHunks", () => {
  const long = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join("\n");

  it("identical texts yield no hunks", () => {
    expect(diffHunks(A, A)).toEqual([]);
  });

  it("one mid-file change becomes one context-framed hunk", () => {
    const b = long.replace("line 20", "line twenty");
    const hunks = diffHunks(long, b, 3);
    expect(hunks).toHaveLength(1);
    expect(hunks[0].skippedBefore).toBe(16);           // lines 1..16 elided
    expect(hunks[0].rows[0]).toMatchObject({ kind: "same", a: 17 });
    expect(hunks[0].rows.filter((r) => r.kind !== "same")).toHaveLength(2);
  });

  it("distant changes stay separate hunks; touching contexts merge", () => {
    const far = long.replace("line 5", "L5").replace("line 35", "L35");
    expect(diffHunks(long, far, 3)).toHaveLength(2);
    const near = long.replace("line 5", "L5").replace("line 9", "L9");
    expect(diffHunks(long, near, 3)).toHaveLength(1);
  });

  it("survives the very large fallback without losing lines", () => {
    const big = Array.from({ length: 2500 }, (_, i) => `a${i}`).join("\n");
    const bigB = Array.from({ length: 2500 }, (_, i) => `b${i}`).join("\n");
    const rows = diffLines(big, bigB);      // 2500×2500 > cap → replace-all
    expect(diffStats(rows)).toEqual({ added: 2500, removed: 2500 });
  });
});
