import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import type { WizardState } from "@/lib/types";
import {
  COLUMNS_STEP, STEPS, resolveEntry, stepGates, unfinishedBefore,
} from "@/lib/wizardFlow";
import type { Handoff } from "@/lib/wizardFlow";
import { saveDraft, loadDraft, clearDraft } from "@/lib/drafts";
import { DestinationStep } from "./DestinationStep";
import { SourceStep } from "./SourceStep";
import { RulesStep } from "./RulesStep";
import { JobPropertiesStep } from "./JobPropertiesStep";
import { ReviewStep } from "./ReviewStep";

// THE one journey (UIFPE-219): every entry point — guided, import, AI draft,
// attribute deep-link, new gold table, resume — lands in these same steps.
// How a task STARTS is a home-page decision (lib/entryMethods.ts seeds the
// state), never a wizard step. Where an entry LANDS and what each step
// requires live in lib/wizardFlow.ts, so both are unit-pinned; this file
// renders them. Review & submit present as one step until the submission
// service (UIFPE-218) splits them.

// Keyed by the query string: navigating between entry points of the SAME
// route (?new=1 -> ?modify=...) must start the seeded task, not keep
// rendering the previous one — without the key React re-renders the mounted
// instance and the one-time seed never re-runs.
export function Wizard() {
  const loc = useLocation();
  return <WizardInner key={loc.search} handoff={loc.state as Handoff | null} />;
}

function WizardInner({ handoff }: { handoff: Handoff | null }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const init = useMemo(() => resolveEntry(params, handoff, loadDraft()), []);
  const [step, setStep] = useState(init.step);
  const [state, setState] = useState<WizardState>(init.state);
  const patch = (p: Partial<WizardState>) => setState((s) => ({ ...s, ...p }));

  // Business users get interrupted — persist every change locally so the
  // landing page can offer to resume. Nothing is written until the task
  // actually CHANGES: on a restoring mount a mount-save would rewrite what
  // it just read, and on a seeding mount it would overwrite a draft the user
  // has not chosen to abandon (the data-loss defect the verification round
  // found). Identity against the mounted values is the test — `patch` always
  // makes a new object — and unlike a first-run flag it survives
  // StrictMode's deliberate double-invocation of effects.
  useEffect(() => {
    if (state === init.state && step === init.step) return;
    saveDraft(state, step);
  }, [state, step]);
  // NOTHING else writes on mount. A handed-over seed is already persisted by
  // the home page before it navigates, and react-router keeps location.state
  // in history — so a reload or Back/Forward re-delivers that seed forever.
  // An effect that saved it here would write the stale seed over the user's
  // edits on every such re-entry: the same data-loss defect, reintroduced.

  const gates = stepGates(state);
  const behind = unfinishedBefore(gates, step);
  const canNext = gates[step] && behind < 0 && step < STEPS.length - 1;
  const earlierProblems = behind < 0 ? []
    : [`${STEPS[behind]} is not finished — go back and complete it`];

  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const back = () => step === 0 ? nav("/transform")
    : setStep((s) => Math.max(s - 1, 0));

  // The modify flow walks Sources too: the registered lineage lives on the
  // column, but re-sourcing an attribute needs the same catalog browser the
  // add flow gets. The step auto-seeds the entity's primary table and is a
  // pass-through when nothing changes.
  const bodies = [
    <DestinationStep state={state} patch={patch} />,
    <SourceStep state={state} patch={patch} />,
    <RulesStep state={state} patch={patch} />,
    <JobPropertiesStep state={state} patch={patch} />,
    <ReviewStep state={state} patch={patch} onSubmitted={clearDraft}
      extraProblems={earlierProblems} />,
  ];

  // Any earlier step is clickable — including one a seed skipped past, so
  // "go back and complete it" is actionable.
  const canJump = (i: number) => i < step;

  return (
    <>
      <div className="topbar">
        <h1 style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <span>&#8646;</span>
          <input className="taskname" value={state.taskName} title="Task name — click to rename"
            onChange={(e) => patch({ taskName: e.target.value })} />
          {state.destinationTable && (
            <span className="pill">&#8594; {state.destinationTable}</span>
          )}
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {behind >= 0 && (
            <span className="kind" style={{ fontSize: 12.5 }}>
              Finish {STEPS[behind]} first
            </span>
          )}
          <button className="btn" onClick={back}>&#8249; Back</button>
          {step < STEPS.length - 1 && (
            <button className="btn primary" disabled={!canNext} onClick={next}>
              Next &#8250;
            </button>
          )}
        </div>
      </div>

      {handoff?.unsaved && (
        <div className="note" style={{ marginBottom: 12 }}>
          This browser is not saving drafts (storage is blocked or full), so
          leaving this page loses the task. Finish it in one go, or download
          the intake from Review before you go.
        </div>
      )}

      <div className="stepper">
        {STEPS.map((label, i) => (
          <span key={label} style={{ display: "contents" }}>
            <span className={"step" + (i === step ? " active"
              : i < step ? (gates[i] ? " done" : " active") : "")}
              role={canJump(i) ? "button" : undefined}
              title={canJump(i)
                ? (gates[i] ? `Back to ${label}` : `${label} is not finished`)
                : undefined}
              onClick={() => canJump(i) && setStep(i)}>
              <span className="dot">{i < step && gates[i] ? "✓" : i + 1}</span>
              <span className="lbl">{label}</span>
            </span>
            {i < STEPS.length - 1 && <span className="step-rule" />}
          </span>
        ))}
      </div>

      <div className="card">{bodies[step]}</div>
    </>
  );
}

export { COLUMNS_STEP };
