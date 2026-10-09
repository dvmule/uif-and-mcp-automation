// jobProperties.test.js (UIFPE-702) — the Job properties step's pure rules.
import { describe, expect, it } from "vitest";
import { MANAGED_KEY, deliverToIds, deliverToKeys, deliveredFileName } from "../jobProperties.js";

describe("deliveredFileName — the publisher's rule, said in the wizard", () => {
  it("joins id, name slug, date and batch, with the format's extension", () => {
    expect(deliveredFileName({ jobId: "100021", name: "Amazon US Product Catalog", format: "csv" })).toBe(
      "100021_amazon_us_product_catalog_{yyyyMMdd}_{batch}.csv"
    );
  });
  it("adds .zip when zipped and stands <id> in before publish", () => {
    expect(deliveredFileName({ name: "Price feed", format: "JSON", zip: true })).toBe(
      "<id>_price_feed_{yyyyMMdd}_{batch}.json.zip"
    );
  });
  it("drops the slug when it is only the id", () => {
    expect(deliveredFileName({ jobId: "100021", name: "", format: "xml" })).toBe("100021_{yyyyMMdd}_{batch}.xml");
  });
});

describe("Deliver to — the managed drop stands alone", () => {
  it("shows the managed drop when nothing else is picked", () => {
    expect(deliverToKeys([])).toEqual([MANAGED_KEY]);
    expect(deliverToKeys(["d1"])).toEqual(["d1"]);
  });
  it("picking a destination drops the managed drop", () => {
    expect(deliverToIds([], [MANAGED_KEY, "d1"])).toEqual(["d1"]);
  });
  it("picking the managed drop clears the destinations", () => {
    expect(deliverToIds(["d1", "d2"], ["d1", "d2", MANAGED_KEY])).toEqual([]);
  });
  it("clearing everything falls back to the managed drop", () => {
    expect(deliverToIds(["d1"], [])).toEqual([]);
  });
});
