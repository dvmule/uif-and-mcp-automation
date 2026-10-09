// The one journey's DECISIONS, separated from its rendering (UIFPE-219):
// where an entry lands, and whether each step is finished. Both were inline
// in Wizard.tsx and both are load-bearing enough to pin directly — the entry
// resolution guards against destroying a user's saved work, and the gates
// are what stop a seeded task being submitted past a step it never walked.
import {
  WizardState, emptyWizard, columnProblems, sourceProblems, taskProblems,
  entityKeyProblems,
} from "./types";
import { REGISTERED } from "./repo";
import {
  pipelineFor, DEFAULT_FREQUENCY, DEFAULT_WEEKLY_DAYS, defaultStartTimeFor,
} from "./pipelines";
import { columnsFor, envTemplateSilver } from "./silverCatalog";
import type { Draft } from "./drafts";

export const STEPS = ["Gold table", "Sources", "Columns", "Schedule",
                      "Review & submit"] as const;
export const COLUMNS_STEP = 2;

export interface Seed { state: WizardState; step: number }
export interface Handoff { seed?: WizardState; step?: number; unsaved?: boolean }

/** An EXPLICIT seed: the home page handed one over (import / AI draft), or
 *  the URL carries an entry parameter. Null when the entry says nothing about
 *  what the task is — which is the signal to restore, not reset. */
export function explicitSeed(params: URLSearchParams,
                             handoff?: Handoff | null): Seed | null {
  // Handed straight over in router state: storage-free, so an import or AI
  // draft still works when localStorage is blocked or full.
  if (handoff?.seed)
    return { state: handoff.seed, step: handoff.step ?? COLUMNS_STEP };

  if (params.get("new"))
    return { step: 0,
             state: { ...emptyWizard(), method: "guided", intent: "add",
                      isNewTable: true } };

  const modify = params.get("modify");
  if (modify) {
    const [table, colName] = modify.split("|");
    const reg = REGISTERED.find((r) => r.table === table);
    const col = reg?.columns.find((c) => c.goldColumnName === colName);
    if (reg && col) {
      // the ENTITY's pipeline (gold_id = table stem), never the domain's —
      // a domain can carry several pipelines with different schedules
      const pipe = pipelineFor(reg.table.split(".").pop() ?? "");
      const freq = pipe?.frequency ?? DEFAULT_FREQUENCY;
      return {
        step: COLUMNS_STEP, // straight to Columns, seeded
        state: {
          ...emptyWizard(),
          method: "guided",
          intent: "modify",
          domain: reg.domain,
          destinationTable: reg.table,
          drivingTable: reg.drivingTable,
          entityKey: reg.entityKey,
          frequency: freq,
          startTime: pipe?.startTime ?? defaultStartTimeFor(freq),
          weeklyDays: pipe?.weeklyDays ?? [...DEFAULT_WEEKLY_DAYS],
          rowGroups: reg.rowGroups,
          transforms: reg.transforms,
          targetColumn: col.goldColumnName,
          columns: [structuredClone(col)],
        },
      };
    }
    // a stale deep-link (renamed or removed attribute): say nothing about the
    // task, so the saved draft survives instead of being reset over
  }
  return null;
}

/** Does the saved draft look like the task this entry would seed? Then the
 *  user did not ask for a new task — they refreshed, or came Forward onto the
 *  entry URL mid-flight — and the draft IS the task.
 *
 *  Identity is the ENTITY and the intent, deliberately NOT which attribute is
 *  being edited: the modify flow lets the user switch attribute inside the
 *  wizard, and comparing the target would call that a different task and
 *  reset them to the URL's original attribute on the next refresh. Every
 *  deliberate start clears the draft first, so a draft that matches this far
 *  is mid-flight work; restoring it and letting the user re-pick beats
 *  discarding what they typed. */
export function draftContinues(draft: WizardState, seed: WizardState): boolean {
  if (draft.isNewTable !== seed.isNewTable) return false;
  // Only a STATED, DIFFERENT intent disqualifies: a draft that has not picked
  // one yet is still this task, and erring toward restoring never loses work.
  if (seed.intent && draft.intent && draft.intent !== seed.intent) return false;
  if (seed.destinationTable)
    return draft.destinationTable === seed.destinationTable;
  // A new-table seed names no table yet: any new-table draft with progress
  // in it is the task the user was building.
  return draft.columns.length > 0 || !!draft.destinationTable;
}

/**
 * Where this entry lands. RESTORING is the default, because the alternative
 * loses work: the wizard saves the state it mounts with, so seeding a fresh
 * task on a refresh, a browser Forward, or a dead bookmark would overwrite
 * the draft before the user could ever reach the Resume card.
 */
export function resolveEntry(params: URLSearchParams,
                             handoff?: Handoff | null,
                             draft?: Draft | null): Seed {
  // A handed-over seed (import / AI) is persisted by the home page BEFORE it
  // navigates, so any draft present is that seed or the user's later edits of
  // it — both beat the copy history keeps re-delivering in location.state on
  // every reload. With no draft, storage was blocked and the handoff is all
  // there is.
  if (handoff?.seed) {
    if (draft) return { state: draft.state, step: draft.step };
    return { state: handoff.seed, step: handoff.step ?? COLUMNS_STEP };
  }
  const seed = explicitSeed(params, handoff);
  if (seed) {
    // Entry URLs get re-entered by F5 and browser Forward. The home page
    // clears the draft before every deliberate start, so a draft that still
    // matches this entry is mid-flight work, not leftovers.
    if (draft && draftContinues(draft.state, seed.state))
      return { state: draft.state, step: draft.step };
    return seed;
  }
  if (draft) return { state: draft.state, step: draft.step };
  return { state: { ...emptyWizard(), method: "guided" }, step: 0 };
}

/**
 * Per-step completeness. A SEEDED entry lands deep in the flow (Columns), so
 * the earlier steps were never walked — without checking all of them an
 * import with no key declared could be carried to Review and submitted.
 */
export function stepGates(state: WizardState): boolean[] {
  // destinationTable must be truthy — a fresh domain starts it as "" while
  // the user types the new table's name. A new table's name must be db.table
  // shaped; add-flow column names must not collide in-task or with
  // registered ones.
  const newTableNameOk = !state.isNewTable
    || /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(state.destinationTable ?? "");
  const registeredNames = state.intent === "add" && !state.isNewTable
    ? (REGISTERED.find((r) => r.table === state.destinationTable)?.columns
        .map((c) => c.goldColumnName) ?? [])
    : [];
  return [
    state.domain !== null && !!state.destinationTable && newTableNameOk &&
      (state.isNewTable
        // UIFPE-107: a new table declares its identity before anything can
        // attach to it — primary silver table + a complete key whose columns
        // all belong to that table.
        ? (!!state.drivingTable && !state.drivingTable.endsWith(".")
           // the scaffolder templates the environment out of the database
           // name; a catalog without an env marker cannot be scaffolded
           // per-env (UIFPE-260) — NewTableIdentity explains this inline
           && envTemplateSilver(state.drivingTable) !== null
           && entityKeyProblems(state.entityKey,
                columnsFor(state.drivingTable)).length === 0)
        : (state.intent !== null &&
           (state.intent === "add" || state.targetColumn !== null))),
    // Sources: tables only (UIFPE-219) — attachments are column-level and
    // validated in the Columns step.
    state.intent === "modify"
      ? sourceProblems(state.sources, state.drivingTable).length === 0
      : (state.sources.length > 0 &&
         sourceProblems(state.sources, state.drivingTable).length === 0),
    state.columns.length > 0 &&
      taskProblems(state.columns, registeredNames).length === 0 &&
      state.columns.every((c) => columnProblems(c,
        { sources: state.sources, drivingTable: state.drivingTable,
          rowGroups: state.rowGroups,
          transforms: state.transforms }).length === 0),
    true,   // Schedule always holds a valid value (defaults from the pipeline)
    true,   // Review's own action is Submit, not Next
  ];
}

/** Index of the first unfinished step BEFORE this one, or -1. */
export function unfinishedBefore(gates: boolean[], step: number): number {
  return gates.slice(0, step).findIndex((g) => !g);
}
