// UIFPE-611: when the Identity card shows "Modified by".
import { describe, expect, it } from "vitest";
import { modifiedBy } from "../authorship.js";

describe("modifiedBy", () => {
  it("shows the publisher of an edited export", () => {
    expect(modifiedBy({ version: 2, author: "amy", published_by: "ben" })).toBe("ben");
  });

  it("shows it when the author edited their own export too", () => {
    expect(modifiedBy({ version: 3, author: "amy", published_by: "amy" })).toBe("amy");
  });

  it("stays quiet on a never-edited export published by its author", () => {
    expect(modifiedBy({ version: 1, author: "amy", published_by: "amy" })).toBeNull();
  });

  it("shows a first version someone else published", () => {
    expect(modifiedBy({ version: 1, author: "amy", published_by: "carol" })).toBe("carol");
  });

  it("never guesses for versions from before published_by existed", () => {
    expect(modifiedBy({ version: 4, author: "karthik" })).toBeNull();
    expect(modifiedBy(undefined)).toBeNull();
  });
});
