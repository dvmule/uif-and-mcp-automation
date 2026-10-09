// Draft persistence lives in the USER'S BROWSER, so it outlives deploys: a
// draft written by an older build can be read by a newer one. UIFPE-149 renamed
// the dispatcher cadences, which made that concrete — a draft saved as
// `frequency: "gold"` resumes into a picker where no option matches, so the
// <select> renders with nothing selected and the schedule is silently lost.
// Found by resuming a real saved draft in the running app, not by review.
import { describe, it, expect, beforeEach } from "vitest";
import { saveDraft, loadDraft, clearDraft } from "../drafts";
import { emptyWizard } from "../types";

// vitest runs in the node environment here (no jsdom configured), so provide
// the minimum localStorage the module needs.
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
};

const KEY = "transform.draft.v1";

describe("draft frequency migration", () => {
  beforeEach(() => store.clear());

  it("rewrites a pre-UIFPE-149 `gold` draft to gold-24h", () => {
    store.set(KEY, JSON.stringify(
      { state: { ...emptyWizard(), frequency: "gold" }, step: 5,
        savedAt: "2026-08-10T15:08:49.000Z" }));
    expect(loadDraft()!.state.frequency).toBe("gold-24h");
  });

  it("rewrites `gold-hourly` to gold-1h", () => {
    store.set(KEY, JSON.stringify(
      { state: { ...emptyWizard(), frequency: "gold-hourly" }, step: 5,
        savedAt: "2026-08-10T15:08:49.000Z" }));
    expect(loadDraft()!.state.frequency).toBe("gold-1h");
  });

  it("leaves a cadence that is still on the ladder alone", () => {
    saveDraft({ ...emptyWizard(), frequency: "gold-15m" }, 5);
    expect(loadDraft()!.state.frequency).toBe("gold-15m");
  });

  it("falls back to the default for a frequency that no longer exists", () => {
    store.set(KEY, JSON.stringify(
      // gold-weekly became a real rung in UIFPE-532; this needs a token that never existed
      { state: { ...emptyWizard(), frequency: "gold-fortnightly" }, step: 5,
        savedAt: "2026-08-10T15:08:49.000Z" }));
    expect(loadDraft()!.state.frequency).toBe("gold-24h");
  });

  it("supplies a startTime for a draft saved before the field existed", () => {
    // Pre-UIFPE-149 drafts have no startTime at all. Falling back to the
    // cadence default reproduces exactly what that draft would have scheduled
    // when it was saved.
    const { startTime: _drop, ...noStart } = emptyWizard();
    store.set(KEY, JSON.stringify(
      { state: { ...noStart, frequency: "gold" }, step: 5,
        savedAt: "2026-08-10T15:08:49.000Z" }));
    const back = loadDraft()!;
    expect(back.state.frequency).toBe("gold-24h");
    expect(back.state.startTime).toBe("06:30");
  });

  it("supplies weeklyDays for a draft saved before the weekly rung existed (UIFPE-532)", () => {
    const { weeklyDays: _drop, ...noDays } = emptyWizard();
    store.set(KEY, JSON.stringify(
      { state: { ...noDays, frequency: "gold-weekly" }, step: 5,
        savedAt: "2026-08-10T15:08:49.000Z" }));
    const back = loadDraft()!;
    expect(back.state.frequency).toBe("gold-weekly");   // a real rung now
    expect(back.state.weeklyDays).toEqual(["MON"]);      // the runtime default
    // a chosen set survives, normalised; junk falls back rather than resuming broken
    saveDraft({ ...emptyWizard(), frequency: "gold-weekly", weeklyDays: ["THU", "TUE"] }, 5);
    expect(loadDraft()!.state.weeklyDays).toEqual(["TUE", "THU"]);
    store.set(KEY, JSON.stringify(
      { state: { ...emptyWizard(), weeklyDays: [] }, step: 5, savedAt: "2026-08-10T15:08:49.000Z" }));
    expect(loadDraft()!.state.weeklyDays).toEqual(["MON"]);
  });

  it("repairs an unparseable startTime rather than resuming into a broken form", () => {
    store.set(KEY, JSON.stringify(
      { state: { ...emptyWizard(), frequency: "gold-4h", startTime: "25:99" },
        step: 5, savedAt: "2026-08-10T15:08:49.000Z" }));
    expect(loadDraft()!.state.startTime).toBe("00:49");
  });

  it("keeps a startTime the user actually chose", () => {
    saveDraft({ ...emptyWizard(), frequency: "gold-4h", startTime: "14:45" }, 5);
    expect(loadDraft()!.state.startTime).toBe("14:45");
  });

  it("round-trips everything else untouched", () => {
    const state = { ...emptyWizard(), taskName: "T1", domain: "product",
                    frequency: "gold-2h" as const };
    saveDraft(state, 3);
    const back = loadDraft()!;
    expect(back.step).toBe(3);
    expect(back.state.domain).toBe("product");
    expect(back.state.taskName).toBe("T1");
  });

  it("returns null when there is no draft, and after clearing", () => {
    expect(loadDraft()).toBeNull();
    saveDraft(emptyWizard(), 1);
    expect(loadDraft()).not.toBeNull();
    clearDraft();
    expect(loadDraft()).toBeNull();
  });
});
