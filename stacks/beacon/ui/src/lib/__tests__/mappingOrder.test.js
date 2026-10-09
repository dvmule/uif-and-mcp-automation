import { describe, expect, it } from "vitest";
import { dropIndex, moveRow, reorderNeedsConfirm } from "../mappingOrder.js";

const L = ["a", "b", "c", "d"];

describe("dropIndex", () => {
  it("drops before the row on its upper half and after it on its lower half", () => {
    expect(dropIndex(2, 104, 100, 20)).toBe(2);
    expect(dropIndex(2, 116, 100, 20)).toBe(3);
  });
});

describe("moveRow", () => {
  it("moves a row down: the insertion point counts the row still in place", () => {
    expect(moveRow(L, 0, 3)).toEqual(["b", "c", "a", "d"]);
    expect(moveRow(L, 0, 4)).toEqual(["b", "c", "d", "a"]);
  });

  it("moves a row up", () => {
    expect(moveRow(L, 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(moveRow(L, 2, 1)).toEqual(["a", "c", "b", "d"]);
  });

  it("returns the same array when the drop would not change the order", () => {
    expect(moveRow(L, 1, 1)).toBe(L);
    expect(moveRow(L, 1, 2)).toBe(L);
  });

  it("ignores impossible moves and never mutates the input", () => {
    expect(moveRow(L, -1, 0)).toBe(L);
    expect(moveRow(L, 4, 0)).toBe(L);
    expect(moveRow(L, 0, 9)).toBe(L);
    expect(moveRow(L, null, 0)).toBe(L);
    expect(moveRow(undefined, 0, 1)).toBeUndefined();
    moveRow(L, 0, 3);
    expect(L).toEqual(["a", "b", "c", "d"]);
  });
});

describe("reorderNeedsConfirm", () => {
  it("asks only on a published export, and only once per editing session", () => {
    expect(reorderNeedsConfirm({ editing: false, asked: false })).toBe(false);
    expect(reorderNeedsConfirm({ editing: true, asked: false })).toBe(true);
    expect(reorderNeedsConfirm({ editing: true, asked: true })).toBe(false);
  });
});
