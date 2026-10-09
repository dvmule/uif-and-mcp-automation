import { WizardState } from "@/lib/types";
import {
  pipelineFor, FREQUENCIES, cadenceFor, runTimesPreview, parseTimeOfDay,
  defaultStartTimeFor, DEFAULT_COOLDOWN_MINUTES, WEEKDAYS, isWeekly,
  formatWeekdays, type Weekday,
} from "@/lib/pipelines";

// Product's measured incremental sits around 25-40 min, so anything under an
// hour is very likely shorter than the job itself. That is SAFE — the
// dispatcher's in-flight guard skips a tick whose previous run is still going —
// but it buys nothing except log noise, so the picker says so rather than
// letting someone discover it from CloudWatch. Not hard-blocked: the control
// plane does not know any job's runtime.
const BUSY_CADENCE_MINUTES = 60;

// The two halves of the schedule are one decision, so they get one width. Set on
// the WRAPPERS rather than on the controls: both controls are width:100% from
// index.css, so sizing the wrappers keeps them identical no matter how long the
// longest cadence label gets.
const CONTROL_WIDTH = 260;

// Step 5 — the pipeline properties this change rides on. The SCHEDULE is a
// live decision (UIFPE-107): which dispatcher cadence this table's pipeline
// subscribes to is config-as-code (pipelines/base/<domain>.yaml
// schedule.frequency) and the delta compiler writes it. Worker profiles stay
// read-only here; the modify flow adds the historical-repair mode.
export function JobPropertiesStep({ state, patch }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  const modifying = state.intent === "modify";
  const weekly = isWeekly(state.frequency);
  // The last remaining day cannot be unticked: an empty set is an entity that
  // never runs, and gold_config.validate refuses it - so the picker never
  // offers that state rather than explaining it afterwards.
  const toggleDay = (d: Weekday) => {
    const on = state.weeklyDays.includes(d);
    if (on && state.weeklyDays.length === 1) return;
    patch({ weeklyDays: WEEKDAYS.filter((w) => (w === d ? !on : state.weeklyDays.includes(w))) });
  };
  // the ENTITY's registered pipeline (gold_id = table stem) — a domain can
  // carry several pipelines on different clocks (UIFPE-252)
  const pipeline = pipelineFor(
    state.destinationTable?.split(".").pop() ?? null);
  return (
    <>
      <h2>Schedule</h2>
      <p className="sub">
        How the pipeline runs this change.{" "}
        {pipeline
          ? <>Values from the registered <code>pipelines/base/{state.domain}.yaml</code>.</>
          : <>No pipeline registered for this domain yet — a new table gets
              one as part of this submission's review, carrying the schedule
              chosen below.</>}
      </p>

      <div className="field">
        <label>Schedule</label>
        <div className="checks" style={{ alignItems: "flex-end" }}>
          <span style={{ width: CONTROL_WIDTH }}>
            <span className="help" style={{ display: "block" }}>How often</span>
            <select value={state.frequency}
              onChange={(e) => {
                // Changing the cadence re-anchors the start time to that
                // cadence's default, UNLESS the user has already chosen one.
                // Otherwise picking "every 4 hours" would silently keep 06:30
                // from the daily default and phase the run times to 02:30.
                const frequency = e.target.value as WizardState["frequency"];
                const touched = state.startTime
                  !== defaultStartTimeFor(state.frequency);
                patch({ frequency,
                        startTime: touched ? state.startTime
                          : defaultStartTimeFor(frequency) });
              }}>
              {FREQUENCIES.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
          </span>
          <span style={{ width: CONTROL_WIDTH }}>
            <span className="help" style={{ display: "block" }}>
              Starting at (UTC)
            </span>
            <input type="time" value={state.startTime}
              onChange={(e) => patch({ startTime: e.target.value })} />
          </span>
        </div>

        {weekly && (
          <div className="checks" style={{ marginTop: 8 }} role="group" aria-label="Run days (UTC)">
            <span className="help" style={{ display: "block", width: "100%" }}>Run days (UTC)</span>
            {WEEKDAYS.map((d) => (
              <label key={d}>
                <input type="checkbox" checked={state.weeklyDays.includes(d)}
                  onChange={() => toggleDay(d)}
                  aria-label={d.charAt(0) + d.slice(1).toLowerCase()} />
                {d.charAt(0) + d.slice(1).toLowerCase()}
              </label>
            ))}
          </div>
        )}

        {parseTimeOfDay(state.startTime) === null ? (
          <p className="help err">
            Start time must be a time of day — the schedule cannot be compiled
            without one.
          </p>
        ) : weekly ? (
          <p className="help">
            Runs weekly on <strong>{formatWeekdays(state.weeklyDays)}</strong> at{" "}
            <strong>{runTimesPreview(state.startTime, state.frequency).times[0]}</strong>
            {" "}UTC. Days and time are UTC here; the Syncs page shows them in
            your own timezone.
          </p>
        ) : (
          <p className="help">
            Runs at{" "}
            <strong>
              {runTimesPreview(state.startTime, state.frequency).times
                .join(" · ")}
            </strong>
            {runTimesPreview(state.startTime, state.frequency).elided > 0
              && <> … and {runTimesPreview(state.startTime, state.frequency)
                .elided} more each day</>}
            {" "}UTC. This table keeps its own clock — other tables on the same
            cadence are unaffected.
          </p>
        )}

        <p className="help">
          {pipeline && (pipeline.frequency !== state.frequency
                        || pipeline.startTime !== state.startTime
                        || (weekly && pipeline.weeklyDays.join() !== state.weeklyDays.join()))
            ? <>Changes the registered schedule ({pipeline.frequency}
                {isWeekly(pipeline.frequency) && <> on {formatWeekdays(pipeline.weeklyDays)}</>} at{" "}
                {pipeline.startTime} → {state.frequency}
                {weekly && <> on {formatWeekdays(state.weeklyDays)}</>} at {state.startTime})
                — compiled into the pipeline YAML and shipped on the next config
                sync after merge.</>
            : <>Both halves are config-as-code in{" "}
                <code>pipelines/base/{state.domain}.yaml</code>; no
                infrastructure change is needed to move a table's schedule.</>}
        </p>
        {(cadenceFor(state.frequency)?.minutes ?? 0) < BUSY_CADENCE_MINUTES && (
          <p className="help">
            Runs closer together than this table's own runtime are skipped, not
            stacked — the dispatcher refuses to start a run while the previous
            one is still going. A cadence shorter than the job simply means
            most ticks do nothing. After a failed run it waits{" "}
            {DEFAULT_COOLDOWN_MINUTES} minutes before trying again.
          </p>
        )}
      </div>

      <table className="props">
        <tbody>
          <tr><td>CDC profile</td><td>G.1X × 8 (8 DPU) · timeout 180 min</td></tr>
          <tr><td>Full-load profile</td><td>G.2X × 8 (16 DPU) · timeout 180 min</td></tr>
          <tr><td>Snapshot gate</td><td><span className="pill ok">enabled</span> — skips the run when Silver hasn't committed</td></tr>
          <tr><td>New-column backfill</td><td>silent (values appear without notifying channels)</td></tr>
        </tbody>
      </table>

      {modifying && (
        <>
          <div className="field" style={{ marginTop: 18 }}>
            <label>Historical repair (F3 --refresh_columns)</label>
            <div className="checks">
              <label>
                <input type="checkbox" checked={state.refreshHistorical}
                  onChange={(e) => patch({ refreshHistorical: e.target.checked })} />
                refresh existing rows after deploy
              </label>
              <select style={{ width: 180 }} value={state.refreshMode}
                disabled={!state.refreshHistorical}
                onChange={(e) => patch({ refreshMode: e.target.value as any })}>
                <option value="emit">emit — notify channels</option>
                <option value="silent">silent — repair quietly</option>
              </select>
            </div>
            <p className="help">
              A logic change only affects FUTURE rows until refreshed. The
              refresh recomputes just this column plus its hashes for all
              historical rows (op=REFRESH in the change log), and refuses to
              run while Silver deltas are pending — sequence is enforced, not
              hoped for.
            </p>
          </div>
        </>
      )}
    </>
  );
}
