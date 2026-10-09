// tour.test.js (UIFPE-445) — the tour's pure logic: step data integrity,
// the "seen it" memory (including broken storage), and card placement.
import { describe, expect, it } from "vitest";
import {
  TOUR_SEEN_KEY,
  TOUR_STEPS,
  markTourSeen,
  placeCard,
  seenTour,
} from "../tour.js";

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    data,
  };
}

const throwingStorage = {
  getItem() {
    throw new Error("blocked");
  },
  setItem() {
    throw new Error("blocked");
  },
};

describe("TOUR_STEPS", () => {
  it("every step carries id, route, title, and body", () => {
    for (const s of TOUR_STEPS) {
      expect(s.id).toBeTruthy();
      expect(s.route.startsWith("/")).toBe(true);
      expect(s.title).toBeTruthy();
      // one plain sentence minimum — an empty stop teaches nothing
      expect(s.body.length).toBeGreaterThan(30);
    }
  });

  it("step ids are unique", () => {
    const ids = TOUR_STEPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("starts on the Overview and covers every main screen", () => {
    // UIFPE-536: the overview IS the landing screen — Home was merged into it
    expect(TOUR_STEPS[0].route).toBe("/m/beacon");
    const routes = new Set(TOUR_STEPS.map((s) => s.route));
    for (const r of [
      "/m/beacon",
      "/m/beacon/exports",
      "/m/beacon/exports/new",
      "/m/beacon/destinations",
      "/m/beacon/runs",
    ]) {
      expect(routes.has(r)).toBe(true);
    }
  });

  it("ends with a targetless closing card", () => {
    expect(TOUR_STEPS[TOUR_STEPS.length - 1].target).toBeNull();
  });
});

describe("seenTour / markTourSeen", () => {
  it("fresh storage means not seen; marking flips it", () => {
    const s = fakeStorage();
    expect(seenTour(s)).toBe(false);
    markTourSeen(s);
    expect(seenTour(s)).toBe(true);
    expect(s.data[TOUR_SEEN_KEY]).toBe("1");
  });

  it("treats broken storage as seen — never nag without memory", () => {
    expect(seenTour(throwingStorage)).toBe(true);
    expect(() => markTourSeen(throwingStorage)).not.toThrow();
    expect(seenTour(null)).toBe(true);
  });
});

describe("placeCard", () => {
  const viewport = { width: 1200, height: 800 };
  const card = { width: 320, height: 190 };

  it("centers when there is no target", () => {
    const p = placeCard(null, viewport, card);
    expect(p.left).toBeCloseTo((1200 - 320) / 2);
    expect(p.top).toBeCloseTo((800 - 190) / 2);
  });

  it("prefers below the target when it fits", () => {
    const p = placeCard({ top: 100, left: 400, width: 200, height: 50 }, viewport, card);
    expect(p.top).toBe(100 + 50 + 12);
    expect(p.left).toBeCloseTo(400 + 100 - 160);
  });

  it("goes above when below would overflow", () => {
    const p = placeCard({ top: 650, left: 400, width: 200, height: 100 }, viewport, card);
    expect(p.top).toBe(650 - 12 - 190);
  });

  it("goes beside a tall target that fills the column", () => {
    const p = placeCard({ top: 10, left: 100, width: 300, height: 780 }, viewport, card);
    expect(p.left).toBe(100 + 300 + 12);
    expect(p.top).toBeGreaterThanOrEqual(12);
    expect(p.top + card.height).toBeLessThanOrEqual(800 - 12);
  });

  it("never leaves the viewport, even for an edge target", () => {
    const p = placeCard({ top: 0, left: 1150, width: 40, height: 20 }, viewport, card);
    expect(p.left).toBeGreaterThanOrEqual(12);
    expect(p.left + card.width).toBeLessThanOrEqual(1200 - 12);
    expect(p.top).toBeGreaterThanOrEqual(12);
  });

  it("still clamps on a phone-sized viewport", () => {
    const phone = { width: 400, height: 700 };
    const p = placeCard({ top: 300, left: 0, width: 400, height: 100 }, phone, card);
    expect(p.left).toBeGreaterThanOrEqual(12);
    expect(p.left + card.width).toBeLessThanOrEqual(400 - 12 + 320); // clamped to margin when card is wider than space
    expect(p.top).toBeGreaterThanOrEqual(12);
    expect(p.top + card.height).toBeLessThanOrEqual(700 - 12);
  });
});
