import { useEffect, useMemo, useState } from "react";
import {
  WizardState, MappingColumn, columnProblems, sameTable,
} from "@/lib/types";
import { newTableArtifacts } from "@/lib/newTableArtifacts";
import { submissionProblems, TICKET_RE } from "@/lib/submission";
import { submitTask, submitConfigured, SubmissionReceipt } from "@/lib/submitService";
import {
  compilePreviewConfigured, currentArtifacts, fetchCompilePreview,
  CurrentArtifacts, PreviewResult, PreviewFileKey,
} from "@/lib/compilePreview";
import { diffHunks, diffStats, DiffHunk } from "@/lib/diff";
import { envTemplateSilver, lookupSilverTables } from "@/lib/silverCatalog";
import { isWeekly } from "@/lib/pipelines";

// Step 6 — Save & SUBMIT FOR REVIEW (ADR-0006 D4: this opens a pull request;
// it never deploys). Business users sign off on sentences, engineers verify
// the JSON — so the summary comes first and the intake artifact is
// collapsible. After submitting, "what happens next" is spelled out.
export function ReviewStep({ state, patch, onSubmitted, extraProblems = [] }:
  { state: WizardState; patch?: (p: Partial<WizardState>) => void;
    onSubmitted?: () => void;
    /** Blockers the step cannot see for itself — an earlier step the task
     *  was seeded past and never completed (UIFPE-219). */
    extraProblems?: string[] }) {
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<SubmissionReceipt | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // The intake artifact is EXACTLY the shape gold-attribute-mapping.schema.json
  // validates; task-only metadata (task name, intent, source graph, refresh
  // plan) travels beside it in the envelope, never inside it.
  const intake = useMemo(() => ({
    $schema: "stacks/apicurio/standards/gold-attribute-mapping.schema.json",
    domain: state.domain,
    table: state.destinationTable,
    // new tables pick the driving table QUALIFIED (db.table); the intake
    // convention is the bare Oracle-style name, with the qualified+templated
    // form riding beside it for the scaffolder (UIFPE-260)
    drivingTable: state.isNewTable && state.drivingTable
      ? (state.drivingTable.split(".").pop() ?? "").toUpperCase()
      : state.drivingTable ?? state.sources[0]?.table?.split(".").pop() ?? null,
    ...(state.isNewTable && state.drivingTable
        && envTemplateSilver(state.drivingTable)
      ? { drivingSilverTable: envTemplateSilver(state.drivingTable) } : {}),
    ...(state.isNewTable
        && Object.keys(lookupSilverTables(state.columns, state.drivingTable,
                                          state.domain)).length
      ? { silverTables: lookupSilverTables(state.columns, state.drivingTable,
                                           state.domain) } : {}),
    ...(state.isNewTable && (state.drivingFilter ?? "").trim()
      ? { drivingFilter: state.drivingFilter.trim() } : {}),
    ...(state.entityKey?.length ? { entityKey: state.entityKey } : {}),
    ...(Object.keys(state.rowGroups ?? {}).length ? { rowGroups: state.rowGroups } : {}),
    ...(Object.keys(state.transforms ?? {}).length ? { transforms: state.transforms } : {}),
    batch: { requestedBy: state.requestedBy || "(wizard user)",
             targetDate: state.targetDate ?? null,
             backfillMode: "silent", newChannel: null },
    columns: state.columns,
  }), [state]);

  const envelope = useMemo(() => ({
    task: state.taskName,
    intent: state.intent,
    ...(state.intent === "modify" ? {
      refresh: { historical: state.refreshHistorical, mode: state.refreshMode },
    } : {}),
    // snake_case start_time: compile_intake_delta.py reads BOTH schedule
    // halves from jobProperties — dropping start_time silently discards the
    // run time the user chose at the Schedule step
    jobProperties: {
      frequency: state.frequency, start_time: state.startTime,
      // the weekly rung's day set (UIFPE-532); any other cadence sends none,
      // and the compiler refuses one that arrives beside a non-weekly cadence
      ...(isWeekly(state.frequency) ? { weekly_days: state.weeklyDays } : {}),
    },
    sourceGraph: state.sources,
    intake,
  }), [state, intake]);

  const artifacts = state.isNewTable ? newTableArtifacts(state) : null;

  const problems = [
    ...extraProblems,
    ...(artifacts?.problems ?? []),
    ...state.columns.flatMap((c) =>
      columnProblems(c, { sources: state.sources, drivingTable: state.drivingTable,
                          rowGroups: state.rowGroups, transforms: state.transforms })
        .map((p) => `${c.goldColumnName || "(unnamed)"}: ${p}`)),
  ];

  // Submission needs two things review does not: the ticket and the author's
  // Jira address (the commit is authored by the engineer — the identity rule
  // every PR gate enforces). Checked live, but they only block SUBMIT.
  const author = { name: state.requestedBy || "", email: state.authorEmail || "" };
  const submitProblems = submitConfigured()
    ? submissionProblems(state, state.ticket ?? "", author)
        .map((p) => p.message)
    : [];

  // The unused-source check (UIFPE-219): a declared table no attribute reads
  // is registration for nothing — flagged here, where the whole task is
  // visible, not at the Sources step where columns don't exist yet.
  const readsTable = (c: MappingColumn, t: string) => {
    const tables = c.kind === "scalar" ? [c.source?.table]
      : c.kind === "conditional" ? (c.branches ?? []).map((b) => b.source?.table)
      : (c.fields ?? []).map((f) => f.source?.table);
    return tables.some((x) => x && sameTable(x, t));
  };
  const unusedSources = state.sources
    .filter((s, i) => i > 0 && !state.columns.some((c) => readsTable(c, s.table)))
    .map((s) => s.table.split(".").pop());

  // ── Pre-submit full-query diff (UIFPE-219) ────────────────────────────────
  // The real compiler, run a step early: the envelope above + the entity's
  // CURRENT artifact texts (bundled at build time) go to the preview service,
  // which returns the recompiled texts — the diff is drawn from those, never
  // from a browser rendering. A NEW table sends no artifacts at all: the
  // service scaffolds every file from the intake (UIFPE-260), so the "diff"
  // is the complete generated artifact set.
  const artifactsNow = useMemo(
    () => (state.isNewTable
      ? {} : currentArtifacts(state.domain, state.destinationTable)),
    [state.isNewTable, state.domain, state.destinationTable]);
  // Value-stable key: who requested and by when never change what compiles,
  // so typing a name at this step must not refetch the preview.
  const previewKey = useMemo(() => JSON.stringify({
    ...envelope,
    intake: { ...intake, batch: { ...intake.batch, requestedBy: "(preview)",
                                  targetDate: null } },
  }), [envelope, intake]);
  const blocked = problems.length > 0;
  const [preview, setPreview] =
    useState<PreviewResult | { ok: "loading" } | null>(null);
  useEffect(() => {
    if (!compilePreviewConfigured() || !artifactsNow || blocked) {
      setPreview(null);
      return;
    }
    let stale = false;
    setPreview({ ok: "loading" });
    fetchCompilePreview(JSON.parse(previewKey), artifactsNow)
      .then((r) => { if (!stale) setPreview(r); });
    return () => { stale = true; };
  }, [previewKey, artifactsNow, blocked]);

  const doSubmit = async () => {
    if (!submitConfigured()) {           // service not deployed yet — the
      setSubmitted(true);                // download-and-hand-over flow stands
      onSubmitted?.();
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const r = await submitTask(state, envelope, state.ticket ?? "", author);
      setReceipt(r);
      setSubmitted(true);
      onSubmitted?.();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <h2>Review &amp; submit</h2>
      <p className="sub">
        Check the plain-English summary; the technical artifact underneath is
        exactly what reviewers will approve. Nothing deploys without an
        approved merge.
      </p>

      {problems.length > 0 && (
        <div className="note">Fix before submitting — {problems.join(" · ")}</div>
      )}
      {unusedSources.length > 0 && (
        <div className="note">
          No attribute reads {unusedSources.join(", ")} — remove the table from
          Sources, or add the attribute that needs it.
        </div>
      )}

      <div className="row2">
        <div className="field">
          <label>Requested by</label>
          <input type="text" value={state.requestedBy ?? ""}
            placeholder="your name"
            onChange={(e) => patch?.({ requestedBy: e.target.value })} />
        </div>
        <div className="field">
          <label>Target date (optional)</label>
          <input type="date" value={state.targetDate ?? ""}
            onChange={(e) => patch?.({ targetDate: e.target.value || null })} />
        </div>
      </div>

      {submitConfigured() && (
        <div className="row2">
          <div className="field">
            <label>Ticket</label>
            <input type="text" value={state.ticket ?? ""}
              placeholder="UIFPE-218"
              style={TICKET_RE.test((state.ticket ?? "").trim())
                ? undefined : { borderColor: "#b98" }}
              onChange={(e) => patch?.({ ticket: e.target.value })} />
            <p className="help">The pull request is linked to this ticket —
              it is how anyone searching Jira finds what shipped.</p>
          </div>
          <div className="field">
            <label>Your Pearson email</label>
            <input type="email" value={state.authorEmail ?? ""}
              placeholder="first.last@pearson.com"
              onChange={(e) => patch?.({ authorEmail: e.target.value })} />
            <p className="help">The change is authored by you — reviews,
              Jira updates and the audit trail all point at this address.</p>
          </div>
        </div>
      )}

      <div className="field">
        <label>In plain terms, this change:</label>
        <ul>
          {state.isNewTable && (
            <li>creates the new gold table <strong>{state.destinationTable}</strong> in
              the <strong>{state.domain}</strong> domain, reading from{" "}
              {state.sources.map((s) => s.table.split(".").pop()).join(", ")}</li>
          )}
          {state.columns.map((c) => <li key={c.goldColumnName}>{describe(c, state)}</li>)}
          {state.intent === "modify" && (
            <li>
              {state.refreshHistorical
                ? <>recalculates this attribute for <strong>all existing rows</strong> after
                    deployment{state.refreshMode === "emit"
                      ? ", and notifies every consumer feed whose values changed"
                      : ", quietly (consumer feeds are not notified)"}</>
                : <>applies the new logic to <strong>future changes only</strong> — existing
                    rows keep their current values</>}
            </li>
          )}
        </ul>
      </div>

      {preview && artifactsNow && (
        <PreviewSection preview={preview} artifacts={artifactsNow}
          entity={(state.destinationTable ?? "").split(".").pop() || "entity"} />
      )}

      <details className="field">
        <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
          Technical artifact — the intake batch reviewers approve ({state.columns.length} column{state.columns.length === 1 ? "" : "s"})
        </summary>
        <pre className="code" style={{ marginTop: 10 }}>
          {JSON.stringify(envelope, null, 2)}
        </pre>
      </details>

      {artifacts && (
        // Folded from the retired schema-first setup flow (UIFPE-219): a task
        // that CREATES a table generates its schema here — the registry-first
        // rule means the compiler refuses the envelope until master.json
        // exists, so this is the file that unblocks it.
        <details className="field" open>
          <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
            New gold table — the schema this task registers first
          </summary>
          <p className="help" style={{ marginTop: 8 }}>
            The schema is registered before anything runs: until{" "}
            <code>{artifacts.paths.master}</code> exists, reviewers treat the
            rules and query as a preview. The runtime manifest and query are
            generated from the rules — never authored.
            {submitConfigured() && " Submitting includes this file automatically."}
          </p>
          <pre className="code" style={{ marginTop: 8 }}>{artifacts.masterJson}</pre>
          <div className="actions" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => {
              const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([artifacts.masterJson],
                { type: "application/json" }));
              a.download = "master.json";
              a.click();
              URL.revokeObjectURL(a.href);
            }}>
              Download master.json
            </button>
          </div>
        </details>
      )}

      {submitError && (
        <div className="note">Submission failed — {submitError}</div>
      )}

      <div className="actions">
        <button className="btn" onClick={() => download(state.taskName, envelope)}>
          Download intake JSON
        </button>
        <button className="btn primary"
          disabled={submitted || submitting
            || problems.length > 0 || submitProblems.length > 0}
          title={submitProblems.join(" · ") || undefined}
          onClick={doSubmit}>
          {submitted ? "Submitted (draft cleared)"
            : submitting ? "Opening the pull request…" : "Submit for review"}
        </button>
      </div>
      {!submitted && submitProblems.length > 0 && problems.length === 0 && (
        <p className="help">{submitProblems.join(" · ")}</p>
      )}

      {submitted && (
        <div className="field" style={{ marginTop: 18 }}>
          <label>What happens next</label>
          <table className="props">
            <tbody>
              <tr><td>1 · Review</td>
                  <td>{receipt
                    ? <>Your change is <a href={receipt.url} target="_blank"
                        rel="noreferrer">pull request #{receipt.number}</a> — the
                        data owners review it there, and{" "}
                        <strong>My submissions</strong> (on the Transform home)
                        tracks it without needing GitHub.</>
                    : <>The data owners review your change as a pull request
                        (the PR-creation service is not wired to this
                        environment yet — hand the downloaded intake to the
                        data engineering team; identical content, same
                        review).</>}</td></tr>
              <tr><td>2 · Approve &amp; merge</td>
                  <td>An approver merges it. Nothing changes anywhere until then.</td></tr>
              <tr><td>3 · Auto-deploy</td>
                  <td>The merge deploys itself — no manual steps.</td></tr>
              <tr><td>4 · Your data appears</td>
                  <td>{state.intent === "modify" && state.refreshHistorical
                    ? "The historical refresh runs right after deploy; existing rows update within the hour."
                    : "New columns fill on the next scheduled run (daily, 06:30 UTC) — earlier if the team triggers one."}</td></tr>
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// One human sentence per column — what a sign-off reads.
function describe(c: MappingColumn, state: WizardState): string {
  const verb = state.intent === "modify" ? "changes" : "adds";
  const feeds = (["event", "message", "api"] as const)
    .filter((k) => c.channels[k]).join(", ") || "none (stored only)";
  if (c.kind === "scalar") {
    const j = c.source.join;
    const lookup = j?.on?.length
      ? `, looked up from ${c.source.table.split(".").pop()} (attached to ${
          j.on[0].leftTable.split(".").pop()} on ${
          j.on.map((o) => o.leftColumn).join(" + ")})`
      : "";
    return `${verb} “${c.goldColumnName}” (${c.businessDescription}) — one value per row, from ${c.source.table.split(".").pop()}.${c.source.column}${lookup}` +
      (c.transform ? `, transformed: ${c.transform}` : "") +
      ` · consumer feeds: ${feeds}`;
  }
  if (c.kind === "conditional")
    return `${verb} “${c.goldColumnName}” (${c.businessDescription}) — value picked by ${c.discriminator.column} across ${c.branches.length} branch${c.branches.length === 1 ? "" : "es"} · consumer feeds: ${feeds}`;
  return `${verb} “${c.goldColumnName}” (${c.businessDescription}) — a list with ${c.fields.length} field${c.fields.length === 1 ? "" : "s"} per entry, ordered by ${c.fields.find((f) => f.sortKey)?.name ?? "?"} · consumer feeds: ${feeds}`;
}

function download(taskName: string, envelope: object) {
  const blob = new Blob([JSON.stringify(envelope, null, 2)],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${taskName}.intake.json`;
  a.click();
}

// ── Pre-submit full-query diff (UIFPE-219) ──────────────────────────────────
// Vocabulary rule: "the deployed query", never compiler-speak. The diff is
// the compiler's own before/after; the engineer-facing plan lines ([PLAN] /
// [RUNBOOK]) appear verbatim — they are the exact change, ready to apply.

const FILE_LABEL: Record<PreviewFileKey, string> = {
  sql: "The deployed query",
  mapping: "The mapping (how each attribute is populated)",
  master: "The registered schema",
  manifest: "The runtime manifest",
  pipeline: "The schedule",
  overlay: "The dev enablement (per-environment overlay)",
};

function PreviewSection({ preview, artifacts, entity }:
  { preview: PreviewResult | { ok: "loading" };
    artifacts: Partial<CurrentArtifacts>; entity: string }) {
  const body = (() => {
    if (preview.ok === "loading")
      return <p className="help">Working out the exact change to the
        deployed query…</p>;
    if (!preview.ok)
      return (
        <div className="note">
          {preview.refusal
            ? <>This change cannot be built as declared — {preview.error}</>
            : <>The preview is unavailable right now ({preview.error}) — the
                summary above and the technical artifact below still show the
                full change.</>}
        </div>
      );
    const sql = preview.files.sql;
    const planLines = preview.log.filter(
      (l) => l.startsWith("[PLAN]") || l.startsWith("[RUNBOOK]"));
    const others = (["master", "mapping", "manifest", "pipeline", "overlay"] as const)
      .filter((k) => preview.files[k]?.changed);
    return (
      <>
        {sql?.changed && sql.after != null
          ? <DiffView before={artifacts.sql ?? ""} after={sql.after} />
          : <p className="help">
              {planLines.length
                ? <>The deployed query is not edited automatically for this
                    change — a data engineer applies the change below during
                    review.</>
                : <>The deployed query itself does not change.</>}
            </p>}
        {planLines.length > 0 && (
          <>
            <p className="help" style={{ marginTop: 10 }}>
              For the data engineer — the exact change, ready to apply:
            </p>
            <pre className="code">{planLines.join("\n\n")}</pre>
          </>
        )}
        {others.length > 0 && (
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
              Everything else that changes ({others.map((k) =>
                FILE_LABEL[k].split(" (")[0].toLowerCase().replace(/^the /, ""))
                .join(", ")})
            </summary>
            {others.map((k) => (
              <div key={k} style={{ marginTop: 10 }}>
                <p className="help" style={{ fontWeight: 700 }}>{FILE_LABEL[k]}</p>
                <DiffView
                  before={(artifacts as Partial<Record<PreviewFileKey, string>>)[k] ?? ""}
                  after={preview.files[k]!.after ?? ""} />
              </div>
            ))}
          </details>
        )}
      </>
    );
  })();
  return (
    <div className="field">
      <label>The deployed query — exactly what changes for {entity}</label>
      {body}
    </div>
  );
}

const DIFF_BG: Record<string, string | undefined> = {
  add: "rgba(46, 160, 67, 0.14)",
  del: "rgba(248, 81, 73, 0.14)",
};
const DIFF_MARK: Record<string, string> = { add: "+", del: "−", same: " " };

function DiffView({ before, after }: { before: string; after: string }) {
  const hunks: DiffHunk[] = useMemo(
    () => diffHunks(before, after), [before, after]);
  const stats = useMemo(
    () => diffStats(hunks.flatMap((h) => h.rows)), [hunks]);
  if (!hunks.length) return <p className="help">No changes.</p>;
  const gutter = (n: number | null) => (
    <span style={{ flex: "0 0 38px", textAlign: "right", paddingRight: 6,
                   opacity: 0.45, userSelect: "none",
                   fontVariantNumeric: "tabular-nums" }}>
      {n ?? ""}
    </span>
  );
  return (
    <>
      <p className="help" style={{ margin: "6px 0" }}>
        {stats.added} line{stats.added === 1 ? "" : "s"} added
        {stats.removed > 0 &&
          <> · {stats.removed} line{stats.removed === 1 ? "" : "s"} removed</>}
      </p>
      <div className="code" style={{ padding: 0, overflowX: "auto",
                                     fontSize: 12, lineHeight: 1.5 }}>
        {hunks.map((h, i) => (
          <div key={i}>
            {h.skippedBefore > 0 && (
              <div style={{ opacity: 0.55, padding: "3px 10px",
                            borderTop: i ? "1px solid rgba(128,128,128,.25)" : "none",
                            borderBottom: "1px solid rgba(128,128,128,.25)" }}>
                ⋯ {h.skippedBefore} unchanged line{h.skippedBefore === 1 ? "" : "s"}
              </div>
            )}
            {h.rows.map((r, j) => (
              <div key={j} style={{ display: "flex",
                                    background: DIFF_BG[r.kind] }}>
                {gutter(r.a)}
                {gutter(r.b)}
                <span style={{ flex: "0 0 16px", textAlign: "center",
                               userSelect: "none", opacity: 0.7 }}>
                  {DIFF_MARK[r.kind]}
                </span>
                <span style={{ whiteSpace: "pre", paddingRight: 12 }}>
                  {r.text}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
