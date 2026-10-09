// Draft persistence — business users get interrupted. Every wizard change is
// saved locally; the landing page offers to resume. localStorage is the MVP
// store (per-browser); the PR service later gives drafts a shared home.
import type { WizardState } from "./types";
import {
  DEFAULT_WEEKLY_DAYS, normalizeFrequency, normalizeStartTime, parseWeeklyDays,
} from "./pipelines";

const KEY = "transform.draft.v1";

// Bumped when the wizard's STEP NUMBERING changes, so a resumed draft lands
// on the step the user actually stopped at. v2 = UIFPE-219's one-journey
// wizard (the Method step became a home-page entry choice, shifting every
// later index down by one).
const DRAFT_VERSION = 2;

export interface Draft {
  state: WizardState; step: number; savedAt: string; v?: number;
}

/** True when the draft was actually persisted. Storage can be blocked or
 *  full, and a caller that RELIES on the draft (the home page handing a
 *  seeded task to the wizard) needs to know rather than lose the work. */
export function saveDraft(state: WizardState, step: number): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(
      { state, step, savedAt: new Date().toISOString(), v: DRAFT_VERSION }));
    return true;
  } catch { return false; /* drafts are best-effort */ }
}

export function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw) as Draft;
    // Drafts outlive deploys — this one lives in the user's browser, so it can
    // carry any shape a PREVIOUS version of the app wrote. UIFPE-149 renamed
    // the cadences, and a draft saved before it holds `frequency: "gold"`,
    // which is no longer an option in the picker: the <select> would render
    // with nothing selected and the schedule silently became un-chosen.
    // Migrating on read keeps a resumed draft on the cadence the user actually
    // picked. Any future field rename belongs here for the same reason.
    if (draft?.state) {
      draft.state.frequency = normalizeFrequency(draft.state.frequency);
      // startTime did not exist before UIFPE-149, so an older draft has none.
      // Falling back to the cadence's default keeps the resumed schedule
      // identical to what that draft would have produced then.
      draft.state.startTime = normalizeStartTime(draft.state.startTime,
                                                 draft.state.frequency);
      // weeklyDays did not exist before UIFPE-532; the runtime default is
      // Monday, which is also what a resumed weekly draft would have run on.
      draft.state.weeklyDays = parseWeeklyDays(draft.state.weeklyDays)
        ?? [...DEFAULT_WEEKLY_DAYS];
      // drivingFilter did not exist before UIFPE-260; Review calls .trim()
      // on it, so a resumed pre-260 new-table draft must not carry undefined.
      draft.state.drivingFilter = draft.state.drivingFilter ?? "";
    }
    // Pre-UIFPE-219 drafts numbered steps with Method at 0; the one-journey
    // wizard starts at Gold table, so every old index shifts down by one.
    if ((draft.v ?? 1) < 2)
      draft.step = Math.max(0, (draft.step ?? 0) - 1);
    return draft;
  } catch { return null; }
}

export function clearDraft(): void {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}
