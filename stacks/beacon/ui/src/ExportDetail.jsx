// Export detail — the export's setup page (UIFPE-696): the Review & publish
// cards (Output, Source, Fields, Rules, Delivery & schedule) under a header
// with the export's actions, then its runs. The run-history chart and the
// versions list sit below for now; UIFPE-698 moves them to the export's
// insight page. Every action and API call is unchanged.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Archive, ArrowLeft, BarChart3, CalendarX, Download, Loader2, Mail, Pencil, Play, RefreshCw, Search, Send, Server, Square, Table2, X } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "./api.js";
import { describeSchedule, nextRun, pauseState, todayInZone } from "./schedule.js";
import {
  Button,
  DASH,
  EmptyState,
  InfoTip,
  Note,
  Pill,
  SkeletonRows,
  StatusBadge,
  Table,
  Td,
  deliveredFiles,
  fmtAbsolute,
  fmtDuration,
  fmtNum,
  fmtRelative,
  inputClass,
  CountUp,
  SlidingPill,
  useSlidingPill,
} from "./ui.jsx";
import { dayBuckets, fillDays, health, isInFlight, runDurationMs, runTargets, scoredRuns, successRate } from "./lib/runHealth.js";
import { deliveredFileName } from "./lib/jobProperties.js";
import { recipeFields, recipeFilters, recipeSources, recipeSpreads, searchFields } from "./lib/exportRecipe.js";
import { versionDiff } from "./lib/versionDiff.js";
import { isVerified, statusText } from "./lib/destinationPicker.js";
import {
  RUN_FETCH_LIMIT,
  SELECTION_PAGE,
  finishDrag,
  historyCapped,
  inSelection,
  runsInSelection,
  selectionLabel,
  selectionSummary,
} from "./lib/runSelection.js";
import { useConfirm } from "./ConfirmDialog.jsx";
import { modifiedBy } from "./lib/authorship.js";

const RANGES = [
  ["24h", 1],
  ["7d", 7],
  ["30d", 30],
  ["90d", 90],
];

const STATUS_FILL = {
  SUCCEEDED: "var(--color-turq)",
  FAILED: "var(--color-fuchsia-brand)",
  WARNING: "var(--color-pearson-amber)",
};

// UIFPE-579: the health pill, the success rate and the chart's buckets are
// pure rules in lib/runHealth.js (vitest) — they all turn on the difference
// between a run that FINISHED and one still executing, which UIFPE-561 made
// load-bearing when it started merging in-flight runs into the list.

// UIFPE-583: ONE mounted screen per export. React Router renders the same
// component when only `:jobId` changes — it re-renders rather than remounts —
// so everything this screen holds in state used to follow the operator to the
// next export. Most visibly the in-flight run being polled (UIFPE-197):
// opening export B while A's batch ran showed A's batch as B's, and left B's
// Run-now button locked as "Running…". Keying the panel on the id remounts it
// with fresh state, which also covers any state added here later — the reason
// this bug happened is that `live` was added without anyone thinking about the
// switch, and enumerating setters would leave the next addition to repeat it.
export default function ExportDetail() {
  const { jobId } = useParams();
  return <ExportDetailPanel key={jobId} jobId={jobId} view="setup" />;
}

// UIFPE-698: the export's insight page — the same panel, its insight view
export function ExportInsight() {
  const { jobId } = useParams();
  return <ExportDetailPanel key={`insight-${jobId}`} jobId={jobId} view="insight" />;
}

const INS_PAGE = 10;

function ExportDetailPanel({ jobId, view = "setup" }) {
  // UIFPE-698: the insight page's open tab and runs page
  const [insTab, setInsTab] = useState("details");
  const [insPage, setInsPage] = useState(0);
  const [versions, setVersions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [range, setRange] = useState(30);
  const [files, setFiles] = useState(null);
  const [filesBusy, setFilesBusy] = useState(null); // batch whose links are being fetched
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);
  // UIFPE-197: the in-flight run being polled — {runId, batchId, version, startedAt}
  const [live, setLive] = useState(null);
  const [now, setNow] = useState(Date.now());
  // UIFPE-207: the schedule record, null when the export has none
  const [schedule, setSchedule] = useState(null);
  const [schedBusy, setSchedBusy] = useState(false);
  // UIFPE-231: the pause date-window picker
  const [pausePicker, setPausePicker] = useState(false);
  const [pauseFrom, setPauseFrom] = useState("");
  const [pauseUntil, setPauseUntil] = useState("");
  // UIFPE-168: extraction mode (versioned) + stored watermark (runtime state)
  const [extraction, setExtraction] = useState(null);
  // UIFPE-220: gold drift — null while checking; failures stay null (the
  // banner is a warning surface, never a reason the page breaks)
  const [drift, setDrift] = useState(null);
  // UIFPE-696: the destinations list, only to name this export's delivery
  // targets and say whether each is verified — [] until it answers
  const [destinations, setDestinations] = useState([]);
  const [destLoaded, setDestLoaded] = useState(false);
  // UIFPE-640: the version whose popup is open, or null
  const [versionOpen, setVersionOpen] = useState(null);
  // UIFPE-604: days picked on the Run history chart ({from, to} or null),
  // the press-drag in progress, and how many filtered rows are showing
  const [picked, setPicked] = useState(null);
  const [drag, setDrag] = useState(null);
  const [pickedShown, setPickedShown] = useState(SELECTION_PAGE);
  // UIFPE-602: in-app confirmation for the destructive actions below
  const [confirm, confirmDialog] = useConfirm();
  // UIFPE-631 (owner-approved): whether the FIRST answer for versions and runs
  // is still outstanding. Additive and presentation-only: `versions` and `runs`
  // keep starting as [] for every rule that reads them; this only lets the page
  // show a placeholder instead of claiming "No runs yet" / "Nothing published"
  // before the data has arrived. It flips once and stays flipped, so Refresh
  // never shows the placeholder again.
  // UIFPE-632: the schedule too — until it arrives, "Manual only" would be a guess
  const [pending, setPending] = useState({ versions: true, runs: true, schedule: true });

  const load = () => {
    api
      .catalogVersions(jobId)
      .then(setVersions)
      .catch(() => setVersions([]))
      .finally(() => setPending((p) => (p.versions ? { ...p, versions: false } : p)));
    api
      .runHistory(jobId, RUN_FETCH_LIMIT) // UIFPE-604: the default 50 left the chart short
      .then((r) => {
        const rows = r.runs || [];
        setRuns(rows);
        // UIFPE-583: re-attach to a batch of THIS export that is already in
        // flight — after a reload, or after coming back from another export.
        // Without this the row would be visible (UIFPE-561) but the banner,
        // its elapsed clock and its Stop button would not, and Stop lives
        // nowhere else. Needs the execution ARN, which the merged in-flight
        // row carries; a row without one is left as a row.
        setLive((current) => {
          if (current) return current; // a run started on this page wins
          const running = rows.find((x) => x.status === "running" && x.run_id);
          if (!running) return null;
          return {
            runId: running.run_id,
            batchId: running.batch_id,
            version: running.version || null,
            startedAt: Date.parse(running.started_at || running.recorded_at) || Date.now(),
          };
        });
      })
      .catch(() => setRuns([]))
      .finally(() => setPending((p) => (p.runs ? { ...p, runs: false } : p)));
    api
      .getSchedule(jobId)
      .then(setSchedule)
      .catch(() => setSchedule(null))
      .finally(() => setPending((p) => (p.schedule ? { ...p, schedule: false } : p)));
    api.extraction(jobId).then(setExtraction).catch(() => setExtraction(null));
    api.jobDrift(jobId).then(setDrift).catch(() => setDrift(null));
  };
  useEffect(load, [jobId]);
  useEffect(() => {
    api.destinations().then((d) => setDestinations(d || [])).catch(() => setDestinations([])).finally(() => setDestLoaded(true));
  }, []);

  // UIFPE-231: pausing takes a date window — the schedule stays ENABLED in
  // AWS, the starter skips fires inside the window, and resume is simply the
  // window passing (nothing to fail). PUT is a full replace, so the body
  // built here is the whole truth: including the dates pauses, omitting them
  // resumes. "Pause indefinitely" keeps the old enabled=false behavior.
  const putScheduleBody = async (extra) => {
    setSchedBusy(true);
    try {
      setSchedule(await api.putSchedule(jobId, {
        expression: schedule.expression,
        timezone: schedule.timezone,
        enabled: true,
        ...extra,
      }));
      setPausePicker(false);
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setSchedBusy(false);
    }
  };

  const confirmPause = () => {
    if (!pauseUntil) {
      setMsg({ tone: "fail", text: "Pick the date the schedule should resume after." });
      return;
    }
    putScheduleBody({ paused_from: pauseFrom || undefined, paused_until: pauseUntil });
  };

  const removeSchedule = async () => {
    const ok = await confirm({
      // UIFPE-716: the prototype's wording, naming the export
      title: `Remove the schedule for ${latest?.name ?? jobId}?`,
      body: "The export stays published; it runs only when someone presses Run now.",
      points: ["No new version is published.", "Add a schedule again any time via Edit → Job properties."],
      action: "Remove schedule",
    });
    if (!ok) return;
    setSchedBusy(true);
    try {
      await api.deleteSchedule(jobId);
      setSchedule(null);
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setSchedBusy(false);
    }
  };

  // UIFPE-168: reset = the next run re-baselines with a full extract — the
  // operator affordance for a Gold rebuild or a partner resync
  const resetWatermark = async () => {
    const ok = await confirm({
      title: "Reset the watermark?",
      body: "The next run sends the FULL dataset again, as a new starting point.",
      points: ["Runs after that go back to sending only what changed."],
      action: "Reset watermark",
    });
    if (!ok) return;
    setBusy("reset");
    setMsg(null);
    try {
      await api.resetWatermark(jobId);
      setMsg({ tone: "ok", text: "Watermark reset — the next run re-baselines with a full extract." });
      load();
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setBusy(null);
    }
  };

  const latest = versions[versions.length - 1];
  // UIFPE-446: soft delete — a retired export keeps history but can't run,
  // edit, or schedule until un-retired
  const retired = !!latest?.retired;
  const setRetired = async (value) => {
    if (
      value &&
      !(await confirm({
        // UIFPE-716: the prototype's wording
        title: `Retire ${latest?.name ?? jobId}?`,
        body: "It leaves the catalog and the Exports list. Its history and delivered files stay.",
        points: [
          "Reversible any time — Show retired on the Exports list, then Restore.",
          "Nothing runs while it is retired.",
        ],
        action: "Retire export",
      }))
    )
      return;
    setBusy("retire");
    setMsg(null);
    try {
      await (value ? api.retireExport(jobId) : api.unretireExport(jobId));
      setMsg({
        tone: "ok",
        text: value
          ? "Export retired — it is hidden from the panel. Find it again via Exports → Show retired."
          : "Export restored — it is back in the catalog.",
      });
      load();
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setBusy(null);
    }
  };
  const state = health(runs);
  const rate = successRate(runs);
  const scoredCount = scoredRuns(runs).length;

  // UIFPE-721: every day of the range, empty ones included
  const chart = useMemo(() => fillDays(dayBuckets(runs, range), range), [runs, range]);
  const chartRuns = chart.reduce((n, b) => n + b.SUCCEEDED + b.FAILED, 0);

  // UIFPE-604: click a bar for one day, press-drag-release across bars for a
  // range (the CloudWatch gesture). Recharts reports the category under the
  // pointer as activeLabel — the bucket's "MM-DD" — anywhere in the plot area
  // (activeTooltipIndex as the fallback). The drag lives in a ref as well as
  // state: Recharts can hand the mouse-up to a handler from an earlier render,
  // which would still see the drag as not started.
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const dragRef = useRef(null);
  const dateAt = (state) => {
    const buckets = chartRef.current;
    const byLabel = buckets.find((b) => b.day === state?.activeLabel);
    const byIndex = buckets[Number(state?.activeTooltipIndex)];
    return (byLabel || byIndex)?.date ?? null;
  };
  const moveDrag = (next) => {
    dragRef.current = next;
    setDrag(next);
  };
  const startDrag = (state) => {
    const d = dateAt(state);
    if (d) moveDrag({ start: d, end: d });
  };
  const extendDrag = (state) => {
    const current = dragRef.current;
    const d = current && dateAt(state);
    if (d && d !== current.end) moveDrag({ ...current, end: d });
  };
  const endDrag = () => {
    const current = dragRef.current;
    if (!current) return;
    setPicked((sel) => finishDrag(sel, current.start, current.end));
    // UIFPE-698: a pick on the chart opens the insight page's Runs tab at page one
    if (view === "insight") {
      setInsTab("runs");
      setInsPage(0);
    }
    setPickedShown(SELECTION_PAGE);
    moveDrag(null);
  };
  const clearPicked = () => {
    setPicked(null);
    setPickedShown(SELECTION_PAGE);
  };
  // what the chart shades: the drag while it happens, else the selection
  const shaded = drag ? finishDrag(null, drag.start, drag.end) : picked;
  const pickedRuns = runsInSelection(runs, picked);
  const pickedSummary = selectionSummary(pickedRuns);
  // UIFPE-696: the latest five, or the days picked on the chart
  const tableRuns = picked ? pickedRuns.slice(0, pickedShown) : runs.slice(0, 5);
  // the live row belongs to today — hide it while other days are picked
  const showLiveRow = live && (!picked || inSelection(new Date().toISOString().slice(0, 10), picked));

  const runNow = async () => {
    setBusy("run");
    setMsg(null);
    try {
      const result = await api.startRun(jobId);
      setLive({
        runId: result.run_id,
        batchId: result.batch_id,
        version: result.version,
        startedAt: Date.now(),
      });
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setBusy(null);
    }
  };

  // UIFPE-197: while a run is in flight, poll the live Step Functions status —
  // the runs table only gets its row from the record_run Lambda at the END of
  // the pipeline, so this is the only in-progress signal available.
  useEffect(() => {
    if (!live) return undefined;
    let cancelled = false;
    const poll = async () => {
      let status;
      try {
        status = await api.runStatus(live.runId);
      } catch {
        return; // transient poll failure — keep polling
      }
      if (cancelled || !status.state || status.state === "RUNNING") return;
      setLive(null);
      if (status.state === "SUCCEEDED") {
        setMsg({ tone: "ok", text: `Batch ${live.batchId} (v${live.version}) succeeded — files are ready below.` });
      } else if (status.state === "ABORTED") {
        // UIFPE-203: an operator stop, not a failure
        setMsg({ tone: "warn", text: `Batch ${live.batchId} stopped — nothing was delivered.` });
      } else {
        setMsg({
          tone: "fail",
          text: `Batch ${live.batchId} ${status.state.toLowerCase()}${status.error ? ` — ${status.error}` : ""}`,
        });
      }
      load();
    };
    poll();
    const pollTimer = setInterval(poll, 5000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      cancelled = true;
      clearInterval(pollTimer);
      clearInterval(tick);
    };
  }, [live?.runId]);

  // UIFPE-203: aborts the execution AND its Spark task; the poll above sees
  // ABORTED within seconds and settles the banner + history
  const stopRun = async () => {
    const { runId, batchId } = live; // the run the operator clicked Stop on
    const ok = await confirm({
      title: `Stop batch ${batchId}?`,
      body: "The run is cancelled now. Nothing from this batch is delivered.",
      points: ["You can start a fresh run any time with Run now."],
      action: "Stop run",
    });
    if (!ok) return;
    setBusy("stop");
    try {
      await api.stopRun(runId);
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setBusy(null);
    }
  };

  // UIFPE-442: the links expand right under the clicked row (a second click
  // collapses them) — never in a block after the table the user has to find
  const download = async (batchId) => {
    if (files?.batch_id === batchId) {
      setFiles(null);
      return;
    }
    setMsg(null);
    setFilesBusy(batchId);
    try {
      setFiles(await api.batchFiles(jobId, batchId));
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setFilesBusy(null);
    }
  };

  const primaryLabel = state.state === "failing" ? "Retry" : "Run now";
  const PrimaryIcon = state.state === "failing" ? RefreshCw : Play;

  // Row counts live in the PySpark task's report, which the state machine
  // currently discards (stepfunctions.tf: ResultPath = null on the Fargate
  // step) — so only show those columns for runs that actually carry them,
  // rather than two columns of em-dashes. Files/size always come from the
  // deliver Lambda's result.
  const hasRowCounts = runs.some((r) => r.summary?.output_rows != null);
  // UIFPE-626: numeric columns right-aligned (Table takes { label, align })
  const num = (label) => ({ label, align: "right" });
  const runHead = [
    "Batch",
    "Status",
    "Trigger",
    // UIFPE-613: no "Filtered" — how many Gold rows the filter left out is
    // not the author's number (owner decision 2026-09-25); rejected is
    ...(hasRowCounts ? [num("Rows\u00a0out"), num("Rejected")] : []),
    num("Files"),
    num("Size"),
    // UIFPE-721: where each run went (insight page) and how long it took
    ...(view === "insight" ? ["Delivered to"] : []),
    "When",
    num("Duration"),
    "",
  ];

  const settings = latest?.settings || {};
  const mc = latest?.mapping_config || {};
  const sources = recipeSources(mc);
  const joins = sources.filter((x) => !x.driving);
  const spreads = recipeSpreads(mc);
  const filters = recipeFilters(mc, settings);
  const fields = recipeFields(mc);
  const format = String(settings.formats?.[0] || settings.format || "").toUpperCase();
  const delimiter = settings.format_options?.[String(settings.formats?.[0] || "").toLowerCase()]?.delimiter;
  const targets = deliveryTargets(settings, jobId, observedPrefix(runs), destinations, destLoaded);
  const lastFinished = runs.find((r) => !isInFlight(r) && r.status === "succeeded");
  // UIFPE-721: the delivered file's name, as the publisher sets it
  const fileName = deliveredFileName({ jobId, name: latest?.name, format: String(settings.formats?.[0] || "").toLowerCase(), zip: settings.packaging?.zip === true });
  const editTo = (step) => `/m/beacon/exports/${jobId}/edit?step=${step}`;
  // the card's Edit, only where editing is possible
  const edit = (step) =>
    latest && !retired ? (
      <Link to={editTo(step)} title="Change this in the wizard — publishing makes the next version">
        <Button size="sm">Edit</Button>
      </Link>
    ) : null;

  // UIFPE-698: the pause / resume / remove controls, shown on the export page's
  // Delivery & schedule card and on the insight page's Schedule card
  // UIFPE-752: `note` — the export page keeps the inline "Change the time via
  // Edit…" line; the insight page's Schedule card says it behind its ⓘ instead
  const scheduleControlsFor = (note) => schedule && !retired ? (
    <>
      {            pausePicker ? (
              // UIFPE-231: pause for a date window; the schedule resumes by
              // itself when the window passes
              <div className="mt-3 space-y-2">
                <div className="flex flex-wrap items-end gap-2">
                  <label className="text-label font-semibold uppercase tracking-wide text-ink-mute">
                    from
                    <input
                      type="date"
                      className={`${inputClass} mt-1 w-auto`}
                      value={pauseFrom}
                      min={todayInZone(schedule.timezone)}
                      onChange={(e) => setPauseFrom(e.target.value)}
                    />
                  </label>
                  <label className="text-label font-semibold uppercase tracking-wide text-ink-mute">
                    until (inclusive)
                    <input
                      type="date"
                      className={`${inputClass} mt-1 w-auto`}
                      value={pauseUntil}
                      min={pauseFrom || todayInZone(schedule.timezone)}
                      onChange={(e) => setPauseUntil(e.target.value)}
                    />
                  </label>
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="primary" onClick={confirmPause} disabled={schedBusy || !pauseUntil}>
                    Pause
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setPausePicker(false)}>
                    Cancel
                  </Button>
                </div>
                <div className="text-meta text-ink-mute">
                  Dates are calendar days in {schedule.timezone}. The schedule resumes by itself
                  after the until-date —{" "}
                  <button
                    className="text-accent underline"
                    onClick={() => putScheduleBody({ enabled: false })}
                    disabled={schedBusy}
                  >
                    or pause indefinitely
                  </button>.
                </div>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {pauseState(schedule) ? (
                  <Button size="sm" onClick={() => putScheduleBody({})} disabled={schedBusy}>
                    Resume now
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    disabled={schedBusy}
                    onClick={() => {
                      const today = todayInZone(schedule.timezone);
                      setPauseFrom(today);
                      setPauseUntil("");
                      setPausePicker(true);
                    }}
                  >
                    Pause…
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="danger"
                  className="border-transparent! bg-transparent! hover:border-transparent! hover:bg-signal-fail/10!"
                  onClick={removeSchedule}
                  disabled={schedBusy}
                >
                  Remove schedule
                </Button>
                {note && <span className="text-meta text-ink-mute">Change the time via Edit; no new version is published.</span>}
              </div>
            )}
    </>
  ) : null;

  // UIFPE-769: the range control's filled pill slides; the chart's bars grow
  // (recharts' own animation, off under prefers-reduced-motion)
  const rangeBoxRef = useRef(null);
  const rangePos = useSlidingPill(rangeBoxRef, [range]);
  const chartMotion = typeof window !== "undefined" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const runHistoryCard = (
          <div className={SECTION_CARD}>
            <SectionHead
              title="Run history"
              right={
                <div ref={rangeBoxRef} className="motion-seg relative inline-flex items-center gap-0.5 rounded-lg border border-line bg-paper-card p-1 shadow-card">
                  <SlidingPill pos={rangePos} />
                  {RANGES.map(([label, days]) => (
                    <button
                      key={label}
                      aria-pressed={range === days}
                      onClick={() => {
                        setRange(days);
                        clearPicked(); // a picked day may fall outside the new window
                      }}
                      className={`rounded-md px-2.5 py-1 text-meta font-semibold transition-colors ${
                        range === days ? "bg-pearson-purple text-white shadow-card" : "text-ink-mute hover:bg-paper-head hover:text-ink"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              }
            />
            {pending.runs ? (
              // UIFPE-631: the chart's place while the first run history loads
              <Placeholder className="h-[200px] w-full" />
            ) : chart.length ? (
              <>
              {/* the chart's svg takes focus on click; its outline would frame
                  the whole chart after every pick (UIFPE-748: a JSX comment,
                  not a line comment — the latter rendered as text) */}
              <div className="h-[200px] cursor-pointer select-none [&_*]:outline-none">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={chart}
                    barCategoryGap="25%"
                    onMouseDown={startDrag}
                    onMouseMove={extendDrag}
                    onMouseUp={endDrag}
                    onMouseLeave={endDrag}
                  >
                    <CartesianGrid vertical={false} stroke="var(--color-line)" />
                    <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--color-ink-mute)" }} axisLine={false} tickLine={false} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--color-ink-mute)" }} axisLine={false} tickLine={false} width={22} />
                    {/* UIFPE-721: "09-12 · 3 succeeded · 1 failed" */}
                    <Tooltip
                      cursor={{ fill: "var(--color-pearson-mist)", opacity: 0.6 }}
                      content={({ active, payload, label }) => {
                        if (!active || !payload?.length) return null;
                        const b = payload[0].payload;
                        return (
                          <div className="rounded-[8px] bg-pearson-purple px-2.5 py-1.5 text-meta font-semibold text-white shadow-lg">
                            {label} · {b.SUCCEEDED} succeeded · {b.FAILED} failed
                          </div>
                        );
                      }}
                    />
                    {shaded && (
                      <ReferenceArea
                        x1={shaded.from.slice(5)}
                        x2={shaded.to.slice(5)}
                        fill="var(--color-accent)"
                        fillOpacity={0.08}
                        stroke="var(--color-accent)"
                        strokeOpacity={0.35}
                      />
                    )}
                    {["SUCCEEDED", "FAILED"].map((key) => (
                      <Bar key={key} dataKey={key} stackId="s" fill={STATUS_FILL[key]} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={chartMotion} animationDuration={350} animationEasing="ease-out">
                        {/* days outside the selection fade so the picked ones stand out */}
                        {chart.map((b) => (
                          <Cell key={b.date} fillOpacity={shaded && !inSelection(b.date, shaded) ? 0.3 : 1} />
                        ))}
                      </Bar>
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
              {/* UIFPE-721: an empty window keeps its (empty) chart and says so */}
              {chartRuns === 0 && (
                <div className="mt-1 text-center text-meta text-ink-mute">No finished runs in this window — pick a longer range.</div>
              )}
              </>
            ) : (
              <EmptyState icon={BarChart3} title="No runs in this window.">
                Pick a longer range above to see older runs.
              </EmptyState>
            )}
            <div className="mt-2 flex flex-wrap gap-4 text-meta text-ink-mute">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-[2px] bg-turq" /> Succeeded
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-[2px] bg-fuchsia-brand" /> Failed
              </span>
              {chartRuns > 0 && (
                <span className="ml-auto">Click a bar, or drag across bars, to see those days&apos; runs below</span>
              )}
            </div>
            {historyCapped(runs) && (
              <div className="mt-1 text-meta text-ink-mute">
                Showing the latest {RUN_FETCH_LIMIT} runs — older runs are not drawn.
              </div>
            )}
          </div>
  );

  // UIFPE-698: the runs table — the export page shows the latest five, the
  // insight page's Runs tab a page of ten (or the days picked on the chart)
  const runsTable = (list) => (
    <>
            {/* UIFPE-721: the picked days as a compact chip, ✕ clears it */}
            {picked && (
              <div className="mb-2 flex flex-wrap items-center gap-2 text-meta">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-pearson-mist py-0.5 pl-3 pr-1 font-semibold text-accent">
                  {selectionLabel(picked)}
                  <button
                    type="button"
                    onClick={clearPicked}
                    aria-label="Clear the picked days"
                    title="Show the latest runs again"
                    className="grid h-5 w-5 place-items-center rounded-full hover:bg-white"
                  >
                    <X size={12} />
                  </button>
                </span>
                <span className="text-ink-mute">
                  {pickedSummary.text}
                  {pickedSummary.unplotted > 0 && ` · ${pickedSummary.unplotted} stopped, skipped or still running (not drawn)`}
                </span>
              </div>
            )}
            <div className="overflow-hidden rounded-brand border border-line [&_tbody_tr:last-child>td]:border-b-0">
            <Table head={runHead}>
              {showLiveRow && !runs.some((r) => r.batch_id === live.batchId) && (
                <tr className="bg-accent-soft/40">
                  <Td className="font-mono text-meta text-ink-soft">{live.batchId}</Td>
                  <Td>
                    <StatusBadge status="running" />
                  </Td>
                  <Td><Pill>manual</Pill></Td>
                  {hasRowCounts && (
                    <>
                      <Td className="tnum text-right">{DASH}</Td>
                      <Td className="tnum text-right">{DASH}</Td>
                    </>
                  )}
                  <Td className="tnum text-right">{DASH}</Td>
                  <Td className="tnum text-right text-ink-mute">{DASH}</Td>
                  {view === "insight" && <Td className="text-ink-mute">{DASH}</Td>}
                  <Td className="text-ink-mute">started {fmtElapsed(now - live.startedAt)} ago</Td>
                  <Td className="tnum text-right text-ink-mute">{fmtElapsed(now - live.startedAt)}</Td>
                  <Td />
                </tr>
              )}
              {/* UIFPE-631: shimmer rows until the first answer, then the empty
                  states with an icon; same conditions as before */}
              {pending.runs && !live && <SkeletonRows cols={runHead.length} rows={4} />}
              {!pending.runs && runs.length === 0 && !live && (
                <EmptyState colSpan={runHead.length} icon={Play} title="No runs yet.">
                  {primaryLabel} produces the first batch.
                </EmptyState>
              )}
              {picked && pickedRuns.length === 0 && (
                <EmptyState colSpan={runHead.length} icon={CalendarX} title="No runs on these days.">
                  Clear the selection to see the latest runs again.
                </EmptyState>
              )}
              {list.map((r) => (
                <React.Fragment key={r.batch_id}>
                  <tr className="transition-colors hover:bg-paper-head">
                    <Td className="font-mono text-meta text-ink-soft">{r.batch_id}</Td>
                    <Td>
                      <StatusBadge status={r.status} title={r.error} />
                    </Td>
                    {/* pre-scheduler rows carry no trigger — they were manual */}
                    <Td><Pill>{r.trigger || "manual"}</Pill></Td>
                    {hasRowCounts && (
                      <>
                        <Td className="tnum text-right">{fmtNum(r.summary?.output_rows)}</Td>
                        <Td className="tnum text-right">{fmtNum(r.summary?.rejected_rows)}</Td>
                      </>
                    )}
                    <Td className="tnum text-right">{fmtNum(deliveredFiles(r.summary))}</Td>
                    <Td className="tnum whitespace-nowrap text-right text-ink-mute">{fmtBytes(batchBytes(r.summary))}</Td>
                    {view === "insight" && (
                      <Td>
                        {(() => {
                          const went = runTargets(r, destinations, (c) => CHANNEL_TYPE[c] || String(c || "").toUpperCase());
                          return went ? (
                            <span className="flex flex-wrap gap-1">
                              {went.map((t, i) => (
                                <Pill key={i} tone={t.ok ? "ok" : "fail"} title={t.error || t.status}>
                                  {t.ok ? "✓ " : ""}
                                  {t.label}
                                  {t.ok ? "" : ` · ${t.status}`}
                                </Pill>
                              ))}
                            </span>
                          ) : (
                            <span className="text-ink-mute">{DASH}</span>
                          );
                        })()}
                      </Td>
                    )}
                    {/* UIFPE-537: actual date and time, raw stamp on hover (UIFPE-408 pattern) */}
                    <Td className="text-ink-mute" title={r.recorded_at}>
                      {fmtAbsolute(r.recorded_at)}
                    </Td>
                    {/* UIFPE-721: runs recorded before UIFPE-720 carry no start */}
                    <Td className="tnum whitespace-nowrap text-right text-ink-mute" title={r.started_at ? `started ${r.started_at}` : "no start time recorded for this run"}>
                      {runDurationMs(r) == null ? DASH : fmtDuration(runDurationMs(r) / 1000)}
                    </Td>
                    <Td className="text-right">
                      {r.status === "succeeded" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          busy={filesBusy === r.batch_id}
                          onClick={() => download(r.batch_id)}
                        >
                          <Download size={13} />{" "}
                          {files?.batch_id === r.batch_id ? "Hide" : "Files"}
                        </Button>
                      )}
                    </Td>
                  </tr>
                  {/* UIFPE-442: links live under their run row, Report-expander style.
                      UIFPE-626: framed, with the sizes formatted like the Size column */}
                  {files?.batch_id === r.batch_id && (
                    <tr>
                      <Td colSpan={runHead.length} className="bg-paper-head">
                        <div className="mb-1.5 text-label font-semibold uppercase tracking-wider text-ink-mute">
                          Delivered files · {files.batch_id} · links valid 15 minutes (signed by the API)
                        </div>
                        <div className="rounded-brand border border-line bg-paper-card px-3 py-1">
                          {files.files.map((f) => (
                            <div key={f.name} className="flex items-center justify-between gap-4 border-b border-line py-1.5 text-body last:border-b-0">
                              <a href={f.url} target="_blank" rel="noreferrer" className="font-semibold text-accent hover:underline">
                                {f.name}
                              </a>
                              <span className="tnum shrink-0 text-meta text-ink-mute" title={`${fmtNum(f.bytes)} bytes`}>
                                {fmtBytes(f.bytes)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </Td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </Table>
            {view === "setup" && picked && pickedRuns.length > pickedShown && (
              <div className="border-t border-line px-4 py-2 text-center">
                <Button size="sm" variant="ghost" onClick={() => setPickedShown((n) => n + SELECTION_PAGE)}>
                  Show more ({pickedRuns.length - pickedShown} left)
                </Button>
              </div>
            )}
            </div>
    </>
  );

  const versionsList = (
          <div>
            {pending.versions && (
              <div className="space-y-2 py-1" aria-hidden="true">
                <Placeholder className="h-3 w-full" />
                <Placeholder className="h-3 w-4/5" />
              </div>
            )}
            {!pending.versions && versions.length === 0 && <div className="py-1 text-body text-ink-mute">Nothing published.</div>}
            {/* UIFPE-640: each version opens a read-only popup of what it held */}
            {[...versions].reverse().map((v) => (
              <button
                key={v.version}
                type="button"
                aria-haspopup="dialog"
                onClick={() => setVersionOpen(v)}
                title={`See what version ${v.version} contained`}
                className="-mx-1.5 flex w-[calc(100%+0.75rem)] items-center justify-between gap-3 rounded-brand border-b border-ink/10 px-1.5 py-1.5 text-left text-body transition-colors last:border-b-0 hover:bg-accent-soft"
              >
                <span className="flex items-center gap-1.5 font-mono">
                  v{v.version}
                  {v.version === latest?.version && <Pill tone="ok">current</Pill>}
                </span>
                <span className="text-meta text-ink-mute" title={v.published_at}>
                  {fmtAbsolute(v.published_at)}
                </span>
              </button>
            ))}
          </div>
  );

  // ── UIFPE-698: the insight page — KPIs, run history, Job details / Runs,
  // versions and the schedule, over the same state and handlers ──
  const windowStart = Date.now() - range * 864e5;
  const inWindow = runs.filter((r) => Date.parse(r.recorded_at) >= windowStart);
  const okN = inWindow.filter((r) => r.status === "succeeded").length;
  const badN = inWindow.filter((r) => r.status === "failed").length;
  const skippedN = inWindow.filter((r) => r.status === "skipped").length;
  const stoppedN = inWindow.filter((r) => r.status === "stopped").length;
  const rowsN = inWindow.reduce((a, r) => a + (r.summary?.output_rows || 0), 0);
  const rejectedN = inWindow.reduce((a, r) => a + (r.summary?.rejected_rows || 0), 0);
  const filesN = inWindow.reduce((a, r) => a + (deliveredFiles(r.summary) || 0), 0);
  const lastFailed = inWindow.find((r) => r.status === "failed");
  const insList = picked ? pickedRuns : runs;
  const insPages = Math.max(1, Math.ceil(insList.length / INS_PAGE));
  const insPageSafe = Math.min(insPage, insPages - 1);
  const insStart = insPageSafe * INS_PAGE;
  const nextAt = schedule?.enabled && pauseState(schedule)?.kind !== "active" ? nextRun(schedule.expression, schedule.timezone) : null;

  if (view === "insight") {
    return (
      <section>
        {confirmDialog}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <Link to="/m/beacon/runs?tab=jobs" className="inline-flex items-center gap-1 text-meta font-semibold text-accent hover:underline">
              <ArrowLeft size={13} /> Insights
            </Link>
            <h1 className="mt-1.5 flex flex-wrap items-center gap-2.5 text-[24px] font-semibold leading-tight tracking-tight text-ink">
              {latest?.name ?? jobId}
              <Pill tone="mute" className="font-medium">{jobId}</Pill>
              {latest && <Pill tone="ok">v{latest.version} · current</Pill>}
              {!pending.schedule && (
                <Pill tone="mute">{schedule ? `scheduled · ${describeSchedule(schedule.expression)}` : "manual"}</Pill>
              )}
              {retired && <Pill tone="warn">Retired</Pill>}
            </h1>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <Link to={`/m/beacon/exports/${jobId}`}>
              <Button>Open export →</Button>
            </Link>
            <span className="text-meta text-ink-mute">
              {range === 1 ? "Last 24 hours" : `Last ${range} days`} · {inWindow.length} run{inWindow.length === 1 ? "" : "s"}
              {nextAt ? ` · next run ${fmtAbsolute(nextAt.toISOString())}` : ""}
            </span>
          </div>
        </div>

        <div className="pt-4">
          {live && (
            <Note tone="mute">
              <span className="flex items-center gap-2">
                <Loader2 size={13} className="shrink-0 animate-spin text-accent" />
                <span>
                  Running batch <b className="font-mono">{live.batchId}</b>
                  {live.version ? ` (v${live.version})` : ""} · elapsed {fmtElapsed(now - live.startedAt)}
                </span>
                <Button size="sm" variant="danger" className="ml-auto" onClick={stopRun} disabled={busy === "stop"}>
                  <Square size={12} /> {busy === "stop" ? "Stopping…" : "Stop"}
                </Button>
              </span>
            </Note>
          )}
          {msg && <Note tone={msg.tone}>{msg.text}</Note>}
        </div>

        {/* the four figures, for the chosen range */}
        <div className="grid gap-4 pt-2 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard label="Runs" dot="bg-accent" value={pending.runs ? DASH : <CountUp value={inWindow.length} />} ring={!pending.runs && okN + badN > 0 ? <Ring ok={okN} bad={badN} /> : null}
            hint={pending.runs ? null : okN + badN ? `${Math.round((okN / (okN + badN)) * 100)}% of finished runs succeeded` : "no finished runs in this range"} />
          {/* UIFPE-713: Succeeded and Failed carry the Run history chart's colours */}
          <KpiCard label="Succeeded" dot="bg-turq" edge="border-t-turq" value={pending.runs ? DASH : <CountUp value={okN} />} hint={`${skippedN} skipped · ${stoppedN} stopped`} />
          <KpiCard label="Failed" dot="bg-fuchsia-brand" edge="border-t-fuchsia-brand" value={pending.runs ? DASH : <CountUp value={badN} />}
            hint={lastFailed ? `latest: ${latest?.name ?? jobId} · ${fmtAbsolute(lastFailed.recorded_at)}` : "nothing failed"} />
          <KpiCard label="Rows delivered" dot="bg-turq" value={pending.runs ? DASH : <CountUp value={rowsN} format={fmtNum} />}
            hint={`in ${fmtNum(filesN)} file${filesN === 1 ? "" : "s"} · ${fmtNum(rejectedN)} rows rejected`} />
        </div>

        <div className="grid items-start gap-5 pt-5 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="min-w-0 space-y-5">
            {runHistoryCard}
            <div className={SECTION_CARD}>
              <div role="tablist" className="-mt-1 mb-4 flex gap-1 border-b border-line">
                {[
                  ["details", "Job details"],
                  ["runs", "Runs"],
                ].map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={insTab === id}
                    onClick={() => setInsTab(id)}
                    className={`-mb-px border-b-[3px] px-3.5 py-2 text-body font-semibold transition-colors ${
                      insTab === id ? "border-accent text-accent" : "border-transparent text-ink-soft hover:text-ink"
                    }`}
                  >
                    {label}
                    {id === "runs" && (
                      <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 text-label font-semibold text-accent">{insList.length}</span>
                    )}
                  </button>
                ))}
              </div>
              {insTab === "details" ? (
                <div className="space-y-4">
                  <div>
                    <div className="mb-1 border-b border-line pb-1.5 text-label font-bold uppercase tracking-wider text-ink-mute">Output &amp; delivery</div>
                    <dl>
                      <Kv label="Format">
                        {format ? <Pill tone="accent">{format}</Pill> : DASH}
                        {delimiter && <span className="ml-2 text-meta text-ink-mute">separator: {delimiter}</span>}
                      </Kv>
                      {settings.partner_schema && (
                        <Kv label="Partner schema">
                          {settings.partner_schema.filename || "schema.xsd"}
                          <span className="ml-2 text-meta text-ink-mute">the file follows this XSD</span>
                        </Kv>
                      )}
                      <Kv label="Packaging">
                        {settings.packaging?.zip === true
                          ? `Compressed as ZIP${settings.packaging?.manifest === false ? "" : " · with a manifest"}`
                          : "Uncompressed"}
                      </Kv>
                      <Kv label="File name">
                        <span className="break-all font-mono text-meta">{fileName}</span>
                      </Kv>
                      <Kv label="Delivers to">
                        {targets.map((t, i) => {
                          const Icon = t.channel === "email" ? Mail : t.channel === "sftp" ? Server : Send;
                          return (
                            // UIFPE-752: the name and its status; channel and address on hover
                            <div key={i} className="flex flex-wrap items-center gap-2 py-0.5" title={`${t.type} · ${t.address}`}>
                              <Icon size={14} strokeWidth={1.75} className="text-accent" aria-hidden="true" />
                              <b className="font-semibold">{t.name}</b>
                              {t.status && <Pill tone={statusTone(t.status)}>{t.status}</Pill>}
                            </div>
                          );
                        })}
                      </Kv>
                    </dl>
                  </div>
                  <div>
                    <div className="mb-1 border-b border-line pb-1.5 text-label font-bold uppercase tracking-wider text-ink-mute">Extraction</div>
                    <dl>
                      {/* UIFPE-752: the pill alone — the Watermark row below says the rest for incremental */}
                      <Kv label="Mode">
                        {extraction?.mode === "incremental" ? <Pill tone="accent">Incremental</Pill> : <Pill>Full load</Pill>}
                      </Kv>
                      {extraction?.mode === "incremental" && (
                        <Kv label="Watermark">{extraction.watermark ?? "none — next run baselines"}</Kv>
                      )}
                      <Kv label="Sources">
                        {sources.map((x) => (
                          <Pill key={x.alias} tone={x.driving ? "ok" : "mute"} className="mb-1 mr-1.5">
                            {x.table.split(".").pop()}{x.driving ? " · drives" : ""}
                          </Pill>
                        ))}
                      </Kv>
                    </dl>
                  </div>
                  <div>
                    <div className="mb-1 border-b border-line pb-1.5 text-label font-bold uppercase tracking-wider text-ink-mute">Schedule &amp; runtime</div>
                    <dl>
                      <Kv label="Schedule">
                        {schedule ? (
                          <>
                            {describeSchedule(schedule.expression, schedule.timezone)}
                            {pauseState(schedule) && <Pill tone="warn" className="ml-1.5">paused</Pill>}
                          </>
                        ) : (
                          <span className="text-ink-mute">no schedule — runs on demand</span>
                        )}
                      </Kv>
                      <Kv label="Next run">{nextAt ? fmtAbsolute(nextAt.toISOString()) : DASH}</Kv>
                      {/* UIFPE-753: the Runtime row (AWS Glue 5.0 · Spark) is gone — owner decision 2026-10-01 */}
                      <Kv label="Missed triggers">never caught up — skipped and recorded</Kv>
                    </dl>
                    {!schedule && !pending.schedule && !retired && (
                      <p className="mt-2 text-meta text-ink-mute">Add a schedule via Edit → Job properties.</p>
                    )}
                  </div>
                </div>
              ) : (
                <>
                  <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
                    <span className="mr-auto text-meta text-ink-mute">
                      Every run of this export, newest first · delivered to {targets.map((t) => t.name).join(", ")} · hover a status for the reason
                    </span>
                    <span className="tnum text-meta text-ink-mute">
                      {insList.length ? `${insStart + 1}–${Math.min(insStart + INS_PAGE, insList.length)} of ${insList.length}` : "0"}
                    </span>
                    <Button size="sm" className="w-7 px-0" aria-label="Previous page" disabled={insPageSafe === 0} onClick={() => setInsPage(insPageSafe - 1)}>‹</Button>
                    <Button size="sm" className="w-7 px-0" aria-label="Next page" disabled={insStart + INS_PAGE >= insList.length} onClick={() => setInsPage(insPageSafe + 1)}>›</Button>
                    <Button size="sm" onClick={load} title="Fetch the latest run states">
                      <RefreshCw size={13} /> Refresh
                    </Button>
                  </div>
                  {runsTable(insList.slice(insStart, insStart + INS_PAGE))}
                </>
              )}
            </div>
          </div>

          {/* UIFPE-713: Schedule first, Versions below it */}
          <aside className="space-y-5">
            {schedule && (
              <div className={SECTION_CARD}>
                {/* UIFPE-752: the notes sit behind ⓘ, the values keep one line */}
                <SectionHead
                  title="Schedule"
                  info="Change the time via Edit; no new version is published."
                  right={pauseState(schedule) ? <Pill tone="warn">paused</Pill> : <Pill tone="ok">active</Pill>}
                />
                <dl>
                  <Kv label="Runs" stacked>{describeSchedule(schedule.expression)}</Kv>
                  <Kv label="Timezone" stacked>{schedule.timezone}</Kv>
                  <Kv label="Next run" stacked>{nextAt ? fmtAbsolute(nextAt.toISOString()) : DASH}</Kv>
                </dl>
                {scheduleControlsFor(false)}
              </div>
            )}
            <div className={SECTION_CARD}>
              <SectionHead
                title={<>Versions <span className="text-meta font-medium text-ink-mute">({versions.length})</span></>}
                info="A published version never changes. Click one to see what it sends."
              />
              {versionsList}
            </div>
          </aside>
        </div>
        {versionOpen && (
          <VersionDialog
            version={versionOpen}
            versions={versions}
            jobId={jobId}
            destinations={destinations}
            destLoaded={destLoaded}
            onSelect={setVersionOpen}
            onClose={() => setVersionOpen(null)}
          />
        )}
      </section>
    );
  }

  // UIFPE-721: an id with no published version and no runs is not an export
  if (!pending.versions && !pending.runs && versions.length === 0 && runs.length === 0 && !live && msg?.tone !== "fail") {
    return (
      <section>
        <Link to="/m/beacon/exports" className="inline-flex items-center gap-1 text-meta font-semibold text-accent hover:underline">
          <ArrowLeft size={13} /> Exports
        </Link>
        <div className={`${SECTION_CARD} mt-3`}>
          <EmptyState icon={Table2} title="Export not found.">
            No published export has the id <b className="font-mono">{jobId}</b>. It may never have been published, or the
            link is wrong — open it from the Exports list.
          </EmptyState>
        </div>
      </section>
    );
  }

  return (
    <section>
      {confirmDialog}
      {/* UIFPE-696: header — back link, the name with its id / format /
          version / health pills and the publish line; the same actions */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Link
            to="/m/beacon/exports"
            className="inline-flex items-center gap-1 text-meta font-semibold text-accent transition-colors hover:underline"
          >
            <ArrowLeft size={13} /> Exports
          </Link>
          <h1 className="mt-1.5 flex flex-wrap items-center gap-2.5 text-[24px] font-semibold leading-tight tracking-tight text-ink">
            {latest?.name ?? jobId}
            <Pill tone="mute" className="font-medium">{jobId}</Pill>
            {format && <Pill tone="accent">{format}</Pill>}
            {latest ? (
              <Pill tone="ok">v{latest.version} · published</Pill>
            ) : pending.versions ? (
              <Placeholder className="inline-block h-5 w-24 align-middle" />
            ) : (
              <Pill tone="mute">not published yet</Pill>
            )}
            {/* UIFPE-631: the health pill waits for the first run history too */}
            {retired ? <Pill tone="warn">Retired</Pill> : pending.runs ? null : <Pill tone={state.tone}>{state.label}</Pill>}
          </h1>
          {latest && (
            <div className="mt-1 text-meta text-ink-mute">
              Published {fmtAbsolute(latest.published_at)}
              {latest.author ? ` · by ${latest.author}` : ""}
              {modifiedBy(latest) ? ` · modified by ${modifiedBy(latest)}` : ""}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {retired ? (
            <Button
              onClick={() => setRetired(false)}
              busy={busy === "retire"}
              title="Bring this export back into the catalog — nothing was lost"
            >
              {busy === "retire" ? "Restoring…" : "Restore"}
            </Button>
          ) : (
            <>
              {latest && (
                <Button
                  onClick={() => setRetired(true)}
                  busy={busy === "retire"}
                  title="Hide this export from the panel. History and files stay; reversible any time. Blocked while a schedule exists."
                >
                  <Archive size={14} /> {busy === "retire" ? "Retiring…" : "Retire"}
                </Button>
              )}
              {latest && (
                <Link to={`/m/beacon/exports/${jobId}/edit`} title="Change this export — publishes the next version (UIFPE-199)">
                  <Button>
                    <Pencil size={14} /> Edit
                  </Button>
                </Link>
              )}
              <Button variant="primary" onClick={runNow} disabled={busy === "run" || !!live || !latest}>
                {live ? <Loader2 size={14} className="animate-spin" /> : <PrimaryIcon size={14} />}
                {busy === "run" ? "Starting…" : live ? "Running…" : primaryLabel}
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="pt-4">
        {retired && (
          <Note tone="warn">
            This export is retired — it is hidden from the panel and cannot run,
            be edited, or be scheduled. Its versions, run history, and delivered
            files are all still here. Restore it to bring it back.
          </Note>
        )}

        {live && (
          <Note tone="mute">
            <span className="flex items-center gap-2">
              <Loader2 size={13} className="shrink-0 animate-spin text-accent" />
              <span>
                Running batch <b className="font-mono">{live.batchId}</b>
                {live.version ? ` (v${live.version})` : ""} · elapsed{" "}
                {fmtElapsed(now - live.startedAt)} — status updates every few seconds.
              </span>
              <Button size="sm" variant="danger" className="ml-auto" onClick={stopRun} disabled={busy === "stop"}>
                <Square size={12} /> {busy === "stop" ? "Stopping…" : "Stop"}
              </Button>
            </span>
          </Note>
        )}
        {msg && <Note tone={msg.tone}>{msg.text}</Note>}

        {/* UIFPE-220: gold drift — the frozen config vs the live catalog, said
            HERE before the schedule finds out at 00:30. Fix = edit + republish. */}
        {drift?.status === "drift" && (
          <Note tone="fail">
            <b>Gold drift:</b> this export's published config (v{drift.version}) no longer
            matches the gold catalog — its next run will fail.
            <ul className="mt-1 list-disc pl-5">
              {drift.issues.map((i, n) => (
                <li key={n}>
                  <span className="font-mono text-[11.5px]">{i.where}</span>: {i.message}
                </li>
              ))}
            </ul>
            Fix: Edit the export, remap against the current columns, and publish — the
            schedule picks up the new version automatically.
          </Note>
        )}
        {drift?.status === "unchecked" && (
          <Note tone="warn">
            Gold drift check could not verify this export against the live catalog
            {drift.issues[0] ? ` (${drift.issues[0].message})` : ""} — its sources may
            have moved. Re-check by reloading, or validate via Edit.
          </Note>
        )}
      </div>

      <div className="space-y-5 pt-2">
        {/* UIFPE-696: Output and Source side by side, the same height */}
        <div className="grid items-stretch gap-5 lg:grid-cols-2">
          <div className={SECTION_CARD}>
            <SectionHead title="Output" right={edit(0)} />
            {pending.versions ? (
              <Placeholder className="h-[110px] w-full" />
            ) : (
              <dl>
                <Kv label="Format">
                  {format ? <Pill tone="accent">{format}</Pill> : DASH}
                  {delimiter && <span className="ml-2 text-meta text-ink-mute">separator: {delimiter}</span>}
                </Kv>
                {settings.partner_schema && (
                  <Kv label="Partner schema">{settings.partner_schema.filename || "schema.xsd"}</Kv>
                )}
                <Kv label="Packaging">
                  {settings.packaging?.zip === true
                    ? `Compressed as ZIP${settings.packaging?.manifest === false ? "" : " · with a manifest"}`
                    : "Uncompressed"}
                </Kv>
                <Kv label="File name">
                  <span className="break-all font-mono text-meta">{fileName}</span>
                </Kv>
                <Kv label="Author">{latest?.author ?? DASH}</Kv>
              </dl>
            )}
          </div>
          <div className={SECTION_CARD}>
            <SectionHead
              title={<>Source {sources.length > 0 && <span className="text-meta font-medium text-ink-mute">({sources.length})</span>}</>}
              right={edit(1)}
            />
            {pending.versions ? <Placeholder className="h-[110px] w-full" /> : <SourceList sources={sources} />}
          </div>
        </div>

        <div className={SECTION_CARD}>
          <SectionHead
            title={<>Fields <span className="text-meta font-medium text-ink-mute">({fields.length})</span></>}
            right={edit(2)}
          />
          {pending.versions ? (
            <Placeholder className="h-[140px] w-full" />
          ) : (
            <div className="overflow-hidden rounded-brand border border-line [&_tbody_tr:last-child>td]:border-b-0">
              <Table head={["#", "Target field", "Source", "Transform"]}>
                {fields.length === 0 && (
                  <EmptyState colSpan={4} title="No fields in this version." />
                )}
                {fields.map((f) => (
                  <tr key={f.n} className="transition-colors hover:bg-paper-head">
                    <Td className="tnum w-10 text-ink-mute">{f.n}</Td>
                    <Td className="font-semibold text-ink">{f.target}</Td>
                    <Td className="text-ink-soft">{f.kind === "column" ? f.from : DASH}</Td>
                    <Td>
                      {f.kind === "column" ? (
                        <span className="text-meta text-ink-mute">straight copy</span>
                      ) : (
                        <span title={f.from} className="inline-flex max-w-[360px] items-center gap-1.5 truncate rounded-full bg-accent-soft px-2.5 py-0.5 text-meta font-semibold text-accent">
                          <i className="not-italic">{f.kind === "fixed" ? "fixed" : "fx"}</i>
                          <span className="truncate font-medium">{f.from}</span>
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </div>

        <div className={SECTION_CARD}>
          <SectionHead
            title={<>Rules <span className="text-meta font-medium text-ink-mute">({joins.length + spreads.length + filters.length})</span></>}
            right={edit(3)}
          />
          {pending.versions ? (
            <Placeholder className="h-[80px] w-full" />
          ) : joins.length + spreads.length + filters.length === 0 ? (
            <div className="text-body text-ink-mute">No rules — every row of the driving table is sent.</div>
          ) : (
            <div className="overflow-hidden rounded-brand border border-line">
              {joins.map((j) => (
                <RuleRow key={`j-${j.alias}`} tag="join" tone="turq" title={`${j.table.split(".").pop()} · ${j.join.type} join`}>
                  {j.join.through ? `through ${j.join.through} · ` : ""}
                  {j.join.on || "every row with every row"}
                </RuleRow>
              ))}
              {spreads.map((sp) => (
                <RuleRow key={`s-${sp.alias}`} tag="spread" tone="mute" title={`One row per ${sp.alias}`}>
                  {sp.list}
                </RuleRow>
              ))}
              {filters.map((f, i) => (
                <RuleRow key={`f-${i}`} tag={f.kind === "sql" ? "filter · Spark SQL" : "filter"} tone="accent" title={f.kind === "sql" ? "Custom condition" : f.text}>
                  {f.kind === "sql" ? f.text : null}
                </RuleRow>
              ))}
            </div>
          )}
          {lastFinished?.summary?.output_rows != null && (
            <div className="mt-2 text-meta text-ink-mute">
              <b className="tnum text-ink">{fmtNum(lastFinished.summary.output_rows)}</b> rows sent by the last successful run
              {lastFinished.summary.rejected_rows ? ` · ${fmtNum(lastFinished.summary.rejected_rows)} rejected` : ""}.
            </div>
          )}
        </div>

        <div className={SECTION_CARD}>
          <SectionHead title="Delivery & schedule" right={edit(4)} />
          <div className="overflow-hidden rounded-brand border border-line [&_tbody_tr:last-child>td]:border-b-0">
            <Table head={["Destination", "Type", "Address", "Status"]}>
              {targets.map((t, i) => {
                const Icon = t.channel === "email" ? Mail : t.channel === "sftp" ? Server : Send;
                return (
                  <tr key={i}>
                    <Td>
                      <span className="inline-flex items-center gap-2 font-semibold text-ink">
                        <Icon size={14} strokeWidth={1.75} className="text-accent" aria-hidden="true" /> {t.name}
                      </span>
                    </Td>
                    <Td className="text-ink-soft">{t.type}</Td>
                    <Td className="break-all text-meta text-ink-soft">{t.address}</Td>
                    <Td>{t.status ? <Pill tone={statusTone(t.status)}>{t.status}</Pill> : <span className="text-ink-mute">{DASH}</span>}</Td>
                  </tr>
                );
              })}
            </Table>
          </div>
          <dl className="mt-3">
            <Kv label="Schedule">
              {pending.schedule ? (
                <Placeholder className="h-4 w-20" />
              ) : schedule ? (
                <span>
                  {describeSchedule(schedule.expression, schedule.timezone)}
                  {(() => {
                    const paused = pauseState(schedule);
                    if (!paused) return null;
                    if (paused.kind === "active")
                      return <Pill tone="warn" className="ml-1.5">paused until {paused.until}</Pill>;
                    if (paused.kind === "upcoming")
                      return <Pill tone="warn" className="ml-1.5">pauses {paused.from} → {paused.until}</Pill>;
                    return <Pill tone="warn" className="ml-1.5">paused</Pill>;
                  })()}
                </span>
              ) : (
                <span className="text-ink-mute">Manual only — runs on demand</span>
              )}
            </Kv>
            {schedule?.enabled && pauseState(schedule)?.kind !== "active" && (
              <Kv label="Next run">
                {(() => {
                  const at = nextRun(schedule.expression, schedule.timezone);
                  return at ? fmtAbsolute(at.toISOString()) : DASH;
                })()}
              </Kv>
            )}
            {pauseState(schedule)?.kind === "active" && (
              <Kv label="Resumes">
                after {pauseState(schedule).until}
                <span className="ml-1 text-label text-ink-mute">automatic — no action needed</span>
              </Kv>
            )}
            {/* UIFPE-168: mode is versioned config; the watermark is runtime
                state the success branch advances — reset forces a re-baseline */}
            <Kv label="Delivery type">
              {extraction?.mode === "incremental" ? <Pill tone="accent">Incremental</Pill> : <Pill>Full load</Pill>}
              {extraction?.mode === "incremental" && (
                <span className="ml-2 text-meta text-ink-mute">
                  watermark {extraction.watermark ?? "none — next run baselines"}
                  {extraction.watermark && extraction.watermark_set_at ? ` · set ${fmtRelative(extraction.watermark_set_at)}` : ""}
                </span>
              )}
            </Kv>
          </dl>
          {extraction?.mode === "incremental" && (
            <div className="mt-2">
              <Button
                size="sm"
                onClick={resetWatermark}
                disabled={busy === "reset" || !extraction.watermark || !!live}
                title={
                  !extraction.watermark
                    ? "No watermark stored — the next run already baselines"
                    : "Next run re-delivers the full dataset as a new baseline"
                }
              >
                <RefreshCw size={13} /> {busy === "reset" ? "Resetting…" : "Reset watermark"}
              </Button>
            </div>
          )}
          {scheduleControlsFor(true)}
        </div>

          <div className={SECTION_CARD}>
            <SectionHead
              title={<>Recent runs {runs.length > 0 && <span className="text-meta font-medium text-ink-mute">({runs.length})</span>}</>}
              right={
                <span className="flex items-center gap-3">
                  <Link to={`/m/beacon/insights/${jobId}`} className="text-meta font-semibold text-accent hover:underline">
                    Open insight →
                  </Link>
                  {/* UIFPE-561: on-demand re-fetch — in-flight runs come from the
                      server now, so Refresh shows them without a full page reload */}
                  <Button size="sm" onClick={load} title="Fetch the latest run states">
                    <RefreshCw size={13} /> Refresh
                  </Button>
                </span>
              }
            />
            {runsTable(tableRuns)}
          </div>
      </div>
      {versionOpen && (
        <VersionDialog
          version={versionOpen}
          versions={versions}
          jobId={jobId}
          destinations={destinations}
          destLoaded={destLoaded}
          onSelect={setVersionOpen}
          onClose={() => setVersionOpen(null)}
        />
      )}
    </section>
  );
}

// UIFPE-640: one published version, read-only — versions never change (D22).
// UIFPE-760: redesigned — step to the version before or after, four output
// tiles, the destinations by name, then Mappings · Source · Rules and, from v2,
// what changed from the version before (lib/versionDiff.js). Closes with its
// close buttons, Escape or a click outside.
const VERSION_SEARCH_FROM = 10; // over this many fields, a search box appears
const CHANGE_MARK = { add: "+", del: "−", mod: "~" };
const CHANGE_WORD = { add: "added", del: "removed", mod: "changed" };
const CHANGE_TONE = { add: "bg-dot-ok/15 text-dot-ok", del: "bg-dot-fail/15 text-dot-fail", mod: "bg-pearson-amber-tint text-ink" };

function VersionDialog({ version, versions, jobId, destinations, destLoaded, onSelect, onClose }) {
  const closeRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [tab, setTab] = useState("fields");
  const [query, setQuery] = useState("");
  // the close button takes focus once, on open — not on every step between versions
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ordered = [...versions].sort((x, y) => x.version - y.version);
  const at = ordered.findIndex((v) => v.version === version.version);
  const prev = at > 0 ? ordered[at - 1] : null;
  const next = at >= 0 && at < ordered.length - 1 ? ordered[at + 1] : null;

  const s = version.settings || {};
  const mc = version.mapping_config || {};
  // `formats` since UIFPE-172; the oldest records carry a single `format`
  const formats = s.formats?.length ? s.formats : s.format ? [s.format] : [];
  const format = formats.map((f) => String(f).toUpperCase()).join(", ") || DASH;
  const delimiter = s.format_options?.[String(formats[0] || "").toLowerCase()]?.delimiter;
  const xsd = s.partner_schema?.id ? s.partner_schema.filename || "schema.xsd" : null;
  const zip = s.packaging?.zip === true;
  const tiles = [
    ["Format", [format, delimiter, xsd].filter(Boolean).join(" · ")],
    ["Packaging", `${zip ? "ZIP" : "Uncompressed"}${s.packaging?.manifest === false ? "" : " · with manifest"}`],
    ["Extraction", s.extraction?.mode === "incremental" ? "Incremental" : "Full load"],
    ["File name", deliveredFileName({ jobId, name: version.name, format: String(formats[0] || "").toLowerCase(), zip })],
  ];
  const targets = deliveryTargets(s, jobId, null, destinations, destLoaded);

  const fields = recipeFields(mc);
  const shown = searchFields(fields, query);
  const sources = recipeSources(mc);
  const rules = [
    ...sources
      .filter((x) => !x.driving)
      .map((x, i) => ({
        tag: "join",
        tone: "mute",
        title: `Join ${i + 1} · ${x.alias} (${x.join.type})`,
        text: x.join.through ? `through ${x.join.through}${x.join.on ? ` · ${x.join.on}` : ""}` : x.join.on || "every row with every row",
      })),
    ...recipeSpreads(mc).map((u) => ({ tag: "spread", tone: "mute", title: `${u.alias} · one row per element`, text: u.list })),
    ...recipeFilters(mc, s).map((f, i) => ({ tag: "filter", tone: "accent", title: `Filter ${i + 1}`, text: f.text })),
  ];
  const nameOf = (id) => destinations.find((d) => d.id === id)?.name || null;
  const changes = prev ? versionDiff(prev, version, { nameOf }) : null;
  // v1 has no Changes tab — fall back to Mappings when stepping onto it
  const open = tab === "changes" && !changes ? "fields" : tab;
  const leftOut = s.left_out_paths || [];

  const tabs = [
    ["fields", "Mappings", fields.length],
    ["source", "Source", sources.length],
    ["rules", "Rules", rules.length],
    ...(changes ? [["changes", `Changes from v${prev.version}`, changes.length]] : []),
  ];
  const stepBtn =
    "px-3 py-1.5 text-meta font-semibold text-accent hover:bg-accent-soft disabled:cursor-default disabled:text-ink-mute disabled:hover:bg-transparent";
  const th = "px-3 py-2 font-semibold";

  return (
    <div
      className="motion-fade-in fixed inset-0 z-50 flex items-center justify-center bg-pearson-purple/45 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="version-dialog-title"
        className="motion-pop-in flex max-h-[88vh] w-full max-w-[920px] flex-col overflow-hidden rounded-card border border-line bg-paper-card shadow-[0_24px_48px_-20px_rgb(13_0_77_/_0.55)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-start justify-between gap-3 px-5 pb-3 pt-5">
          <div className="min-w-0">
            <h2 id="version-dialog-title" className="flex flex-wrap items-center gap-2 text-[18px] font-semibold tracking-tight text-ink">
              Version {version.version}
              {next ? <Pill tone="mute">superseded by v{next.version}</Pill> : <Pill tone="ok">current</Pill>}
            </h2>
            <p className="mt-0.5 text-meta text-ink-mute">
              Published {fmtAbsolute(version.published_at)}
              {version.published_by ? ` · by ${version.published_by}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div role="group" aria-label="Step between versions" className="inline-flex overflow-hidden rounded-full border border-line bg-paper-card">
              <button type="button" disabled={!prev} onClick={() => prev && onSelect(prev)} aria-label="Older version" className={stepBtn}>
                ‹ {prev ? `v${prev.version}` : "oldest"}
              </button>
              <button
                type="button"
                disabled={!next}
                onClick={() => next && onSelect(next)}
                aria-label="Newer version"
                className={`border-l border-line ${stepBtn}`}
              >
                {next ? `v${next.version}` : "newest"} ›
              </button>
            </div>
            <button
              ref={closeRef}
              type="button"
              aria-label="Close"
              onClick={onClose}
              className="rounded p-1 text-ink-mute hover:bg-accent-soft hover:text-ink"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
          <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            {tiles.map(([k, v]) => (
              <div key={k} className="min-w-0 rounded-brand bg-paper-head px-3 py-2">
                <dt className="text-label font-semibold uppercase tracking-wider text-ink-mute">{k}</dt>
                <dd className={`mt-0.5 break-words text-ink ${k === "File name" ? "font-mono text-meta" : "text-body"}`}>{v}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-4">
            <div className="mb-2 text-label font-semibold uppercase tracking-wider text-ink-mute">
              Delivers to {targets.length} destination{targets.length === 1 ? "" : "s"}
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {targets.map((t, i) => {
                const Icon = t.channel === "email" ? Mail : t.channel === "sftp" ? Server : Send;
                return (
                  <div key={i} className="flex min-w-0 items-center gap-2.5 rounded-brand border border-line px-3 py-2.5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[8px] bg-accent-soft text-accent">
                      <Icon size={15} strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <b className="text-body font-semibold text-ink">{t.name}</b>
                        <Pill tone="mute">{String(t.type).toUpperCase()}</Pill>
                      </span>
                      <span className="block break-all font-mono text-meta text-ink-soft">{t.address}</span>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          <div role="tablist" aria-label="Parts of this version" className="mt-5 flex flex-wrap border-b border-line">
            {tabs.map(([id, label, n]) => {
              const on = open === id;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  aria-controls={`version-${id}`}
                  onClick={() => setTab(id)}
                  className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-body font-semibold transition-colors ${
                    on ? "border-accent text-accent" : "border-transparent text-ink-soft hover:text-ink"
                  }`}
                >
                  {label}
                  <span className="tnum min-w-[18px] rounded-full bg-accent-soft px-1.5 text-center text-[11px] leading-[18px] text-accent">{n}</span>
                </button>
              );
            })}
          </div>

          <div id={`version-${open}`} role="tabpanel" className="mt-3">
            {open === "fields" && (
              <>
                {fields.length > VERSION_SEARCH_FROM && (
                  <label className="mb-3 flex items-center gap-2">
                    <Search size={14} className="text-ink-mute" aria-hidden="true" />
                    <input
                      className={`${inputClass} max-w-xs`}
                      placeholder={`Search ${fields.length} fields`}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      aria-label="Search fields"
                    />
                  </label>
                )}
                {fields.length ? (
                  <div className="overflow-x-auto rounded-brand border border-line">
                    <table className="w-full text-body">
                      <thead className="bg-paper-head text-left text-label uppercase tracking-wider text-ink-mute">
                        <tr>
                          <th className={`w-10 ${th}`}>#</th>
                          <th className={th}>Target field</th>
                          <th className={th}>Source</th>
                          <th className={th}>Transform</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shown.map((f) => (
                          <tr key={f.n} className="border-t border-line">
                            <td className="tnum px-3 py-2 text-meta text-ink-mute">{f.n}</td>
                            <td className="px-3 py-2 font-semibold text-ink">{f.target}</td>
                            <td className="max-w-[320px] break-all px-3 py-2 font-mono text-meta text-ink-soft">
                              {f.kind === "column" ? f.from || DASH : DASH}
                            </td>
                            <td className="max-w-[320px] px-3 py-2">
                              {f.kind === "column" ? (
                                <span className="text-meta text-ink-mute">straight copy</span>
                              ) : (
                                <span className="inline-flex max-w-full items-center gap-1.5 rounded-brand bg-accent-soft px-2 py-0.5 text-meta text-accent">
                                  <i className="font-semibold">{f.kind === "fixed" ? "=" : "fx"}</i>
                                  <span className="break-all font-mono">{f.from || DASH}</span>
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                        {shown.length === 0 && (
                          <tr className="border-t border-line">
                            <td colSpan={4} className="px-3 py-3 text-meta text-ink-mute">No field matches “{query}”.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-body text-ink-mute">No fields in this version.</p>
                )}
                {xsd && leftOut.length > 0 && (
                  <p className="mt-2 text-meta text-ink-mute">
                    Left out of {xsd} (optional): <span className="font-mono">{leftOut.join(", ")}</span>
                  </p>
                )}
              </>
            )}

            {open === "source" &&
              (sources.length ? (
                <div className="overflow-x-auto rounded-brand border border-line">
                  <table className="w-full text-body">
                    <thead className="bg-paper-head text-left text-label uppercase tracking-wider text-ink-mute">
                      <tr>
                        <th className={th}>Table</th>
                        <th className={th}>Role</th>
                        <th className={th}>Join</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sources.map((x) => (
                        <tr key={x.alias} className="border-t border-line">
                          <td className="px-3 py-2">
                            <div className="font-semibold text-ink">{x.alias}</div>
                            <div className="font-mono text-meta text-ink-mute">{x.table}</div>
                          </td>
                          <td className="px-3 py-2 text-ink">
                            {x.driving ? "Drives the feed" : `${x.join.type.charAt(0).toUpperCase()}${x.join.type.slice(1)} join`}
                          </td>
                          <td className="max-w-[420px] break-all px-3 py-2 font-mono text-meta text-ink-soft">
                            {x.driving ? DASH : x.join.through ? `through ${x.join.through}` : x.join.on || DASH}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-body text-ink-mute">This version names no sources.</p>
              ))}

            {open === "rules" &&
              (rules.length ? (
                <div className="overflow-hidden rounded-brand border border-line">
                  {rules.map((r, i) => (
                    <RuleRow key={i} tag={r.tag} tone={r.tone} title={r.title}>
                      <span className="font-mono">{r.text}</span>
                    </RuleRow>
                  ))}
                </div>
              ) : (
                <p className="text-body text-ink-mute">No rules — every joined row is sent.</p>
              ))}

            {open === "changes" &&
              changes &&
              (changes.length ? (
                <div className="overflow-hidden rounded-brand border border-line">
                  {changes.map((c, i) => (
                    <div key={i} className="flex items-baseline gap-2.5 border-b border-line px-3 py-2 text-body last:border-b-0">
                      <b
                        className={`grid h-5 w-5 shrink-0 place-items-center rounded-[6px] text-[13px] ${CHANGE_TONE[c.kind]}`}
                        title={CHANGE_WORD[c.kind]}
                      >
                        {CHANGE_MARK[c.kind]}
                        <span className="sr-only"> {CHANGE_WORD[c.kind]}</span>
                      </b>
                      <span className="w-[74px] shrink-0 text-meta text-ink-soft">{c.area}</span>
                      <span className="min-w-0 break-words">
                        <b className="font-semibold text-ink">{c.subject}</b> <span className="text-ink-soft">{c.detail}</span>
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-body text-ink-mute">Nothing an author sees changed from v{prev.version} — the same export was published again.</p>
              ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-paper-head px-5 py-3">
          <span className="text-meta text-ink-mute">A published version never changes.</span>
          <Button variant="primary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}

// Total bytes actually written for a batch, from the delivery steps.
function batchBytes(summary) {
  if (!Array.isArray(summary?.delivery)) return null;
  const total = summary.delivery.reduce((n, d) => n + (d.bytes || 0), 0);
  return total || null;
}

function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function fmtBytes(n) {
  if (n === null || n === undefined) return DASH;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// The S3 prefix the last run actually wrote to. Preferred over guessing the
// managed-drop path: older versions were published under a different default.
function observedPrefix(runs) {
  const step = runs
    .find((r) => Array.isArray(r.summary?.delivery))
    ?.summary.delivery.find((d) => d.dest);
  return step ? step.dest.slice(0, step.dest.lastIndexOf("/") + 1) : null;
}

// The delivered-to line, per channel — same shape the wizard shows at publish
// time. No explicit delivery block on the version means the managed drop, so
// fall back to what the runs observed, then to the current default path.
function deliveryTarget(delivery, jobId, observed) {
  if (!delivery) return observed || `partner-drop/inbound/${jobId}`;
  if (delivery.channel === "s3") return delivery.destination_s3_prefix;
  if (delivery.channel === "email") return (delivery.recipients || []).join(", ");
  return `${delivery.host}:${delivery.port ?? 22}${delivery.remote_dir}`;
}

// UIFPE-698: one figure on the insight page — label, number, one quiet line,
// and the success ring on the right of the Runs figure
function KpiCard({ label, value, hint, dot, ring, edge = "border-t-pearson-purple" }) {
  return (
    <div className={`relative rounded-card border border-t-[3px] border-line bg-paper-card px-5 py-4 shadow-card ${edge}`}>
      <div className="flex items-center gap-2 text-meta font-bold text-ink">
        <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden="true" />
        {label}
      </div>
      <div className="tnum mt-1.5 text-kpi font-bold tracking-tight text-accent">{value}</div>
      {hint && <div className="mt-1.5 text-meta text-ink-mute">{hint}</div>}
      {ring && <div className="absolute right-5 top-1/2 -translate-y-1/2 leading-none">{ring}</div>}
    </div>
  );
}

function Ring({ ok, bad, size = 56 }) {
  const half = size / 2;
  const r = size * 0.4;
  const c = 2 * Math.PI * r;
  const share = ok + bad ? ok / (ok + bad) : 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={half} cy={half} r={r} fill="none" stroke="var(--color-dot-fail)" strokeWidth={size * 0.15} />
      <circle className="motion-arc" cx={half} cy={half} r={r} fill="none" stroke="var(--color-dot-ok)" strokeWidth={size * 0.15}
        strokeDasharray={`${share * c} ${c}`} transform={`rotate(-90 ${half} ${half})`} />
    </svg>
  );
}

// UIFPE-696: one label / value line in a card
// UIFPE-752: `stacked` puts the label above the value for the narrow right
// rail, where a 150px label column left "Daily at 06:00" wrapping onto three lines
function Kv({ label, children, stacked = false }) {
  if (stacked) {
    return (
      <div className="border-b border-line py-2 text-body last:border-b-0">
        <dt className="text-meta font-semibold text-ink-mute">{label}</dt>
        <dd className="min-w-0 truncate text-ink" title={typeof children === "string" ? children : undefined}>{children}</dd>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] items-baseline gap-3 border-b border-line py-2 text-body last:border-b-0">
      <dt className="text-meta font-semibold text-ink-mute">{label}</dt>
      <dd className="min-w-0 text-ink">{children}</dd>
    </div>
  );
}

// UIFPE-696: a join, a spread or a filter as one line — its tag, what it is,
// and the condition beneath
function RuleRow({ tag, tone, title, children }) {
  const pill = tone === "turq" ? "bg-turq/25 text-ink" : tone === "accent" ? "bg-accent-soft text-accent" : "bg-paper-head text-ink-soft";
  return (
    <div className="border-b border-line px-3.5 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2 text-body">
        <span className={`rounded-full px-2 py-0.5 text-meta font-semibold ${pill}`}>{tag}</span>
        <span className="font-semibold text-ink">{title}</span>
      </div>
      {children && <div className="mt-0.5 break-words text-meta text-ink-soft">{children}</div>}
    </div>
  );
}

// UIFPE-696: at most three lines so the Source card keeps Output's height;
// "+N more · Show all" opens every table, driving one first
const SOURCE_CAP = 3;
function SourceList({ sources }) {
  const [all, setAll] = useState(false);
  const shown = sources.length > SOURCE_CAP ? sources.slice(0, SOURCE_CAP - 1) : sources;
  const rest = sources.length - shown.length;
  const line = (x) => {
    const db = x.table.split(".")[0] || "";
    const domain = db.replace(/^.*gold_/, "");
    return (
      <div key={x.alias} className="flex items-center justify-between gap-3 border-b border-line px-3 py-2 last:border-b-0">
        <span className="flex min-w-0 items-center gap-2">
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-[7px] bg-accent-soft text-accent">
            <Table2 size={13} strokeWidth={1.75} aria-hidden="true" />
          </span>
          <span className="truncate font-semibold text-ink">{x.table.split(".").pop()}</span>
          {domain && domain !== x.table.split(".").pop() && <span className="text-meta text-ink-mute">{domain}</span>}
        </span>
        <Pill tone={x.driving ? "ok" : "mute"}>{x.driving ? "drives the feed" : "joins"}</Pill>
      </div>
    );
  };
  if (!sources.length) return <div className="text-body text-ink-mute">This version names no sources.</div>;
  return (
    <>
      <div className="overflow-hidden rounded-brand border border-line">
        {shown.map(line)}
        {rest > 0 && (
          <div className="flex items-center gap-2 px-3 py-2 text-meta text-ink-mute">
            + {rest} more table{rest === 1 ? "" : "s"} ·
            <button type="button" onClick={() => setAll(true)} className="font-semibold text-accent hover:underline">
              Show all {sources.length}
            </button>
          </div>
        )}
      </div>
      {all && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onKeyDown={(e) => e.key === "Escape" && setAll(false)}>
          <div className="motion-fade-in absolute inset-0 bg-pearson-purple/45" onClick={() => setAll(false)} />
          <div role="dialog" aria-modal="true" aria-label="Source tables" className="motion-pop-in relative w-full max-w-lg rounded-card border border-line bg-paper-card p-5 shadow-lg">
            <div className="mb-1 flex items-center justify-between">
              <h2 className="text-[18px] font-extrabold text-ink">Source tables ({sources.length})</h2>
              <button type="button" aria-label="Close" onClick={() => setAll(false)} className="rounded p-1 text-ink-mute hover:bg-accent-soft hover:text-ink">
                <X size={15} />
              </button>
            </div>
            <p className="mb-3 text-meta text-ink-mute">The first table drives the feed; the others join onto it, in this order.</p>
            <div className="max-h-[60vh] overflow-y-auto rounded-brand border border-line">{sources.map(line)}</div>
          </div>
        </div>
      )}
    </>
  );
}

// UIFPE-696: the delivery targets as the table shows them — named from the
// destinations list, typed and addressed per channel; no delivery block on
// the version means the managed partner drop
const CHANNEL_TYPE = { s3: "AWS S3", sftp: "SFTP", email: "Email" };
// verified reads green, a removed destination red, anything else amber
function statusTone(status) {
  return status === "verified" ? "ok" : status === "removed" ? "fail" : "warn";
}

function deliveryTargets(settings, jobId, observed, destinations, loaded = true) {
  const list = settings.deliveries?.length
    ? settings.deliveries
    : settings.delivery
      ? [{ ...settings.delivery, destination_id: settings.destination_id }]
      : [];
  if (!list.length)
    return [{ name: "Managed S3 partner-drop", channel: "s3", type: "AWS S3", address: deliveryTarget(null, jobId, observed), status: "managed" }];
  return list.map((t) => {
    const d = t.destination_id && destinations.find((x) => x.id === t.destination_id);
    // UIFPE-721: a destination the version names but the list no longer has
    const removed = Boolean(t.destination_id) && loaded && !d;
    return {
      name: d?.name || (removed ? "(removed destination)" : `${CHANNEL_TYPE[t.channel] || t.channel || "Destination"} destination`),
      channel: t.channel,
      type: CHANNEL_TYPE[t.channel] || String(t.channel || "").toUpperCase(),
      address: deliveryTarget(t, jobId, observed),
      status: d ? (isVerified(d) ? "verified" : statusText(d)) : removed ? "removed" : null,
    };
  });
}

function Row({ label, children }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-ink-mute">{label}</dt>
      <dd className="min-w-0 text-right text-ink">{children}</dd>
    </div>
  );
}

// UIFPE-626: one labelled group in the side rail — replaces a stacked card
function Group({ title, children }) {
  return (
    <div>
      <div className="border-b border-ink/10 pb-1.5 text-label font-semibold uppercase tracking-wider text-pearson-amethyst">
        {title}
      </div>
      <dl className="mt-2.5 space-y-2 text-body">{children}</dl>
    </div>
  );
}

// UIFPE-649: the body's sections are white cards on the page grey — the
// card radius, hairline, the one card shadow, 16px padding (design system)
const SECTION_CARD = "rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card";

// UIFPE-626: a titled section (chart, runs); since UIFPE-649 inside a card
// UIFPE-752: an optional `info` puts the card's one-line note behind an ⓘ
// beside the title (hover or focus shows it, a click pins it), the owner's
// rule for card descriptions
function SectionHead({ title, right, info }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
        {info && <InfoTip text={info} label={`About ${typeof title === "string" ? title : "this card"}`} />}
      </div>
      {right}
    </div>
  );
}

// UIFPE-631: a shimmer bar standing in for content whose first answer is
// still outstanding (the list screens' SkeletonRows idiom)
function Placeholder({ className = "" }) {
  return <span aria-hidden="true" className={`block animate-pulse rounded bg-paper-line/60 ${className}`} />;
}

// UIFPE-626: one figure on the facts strip — label, value (or a badge), hint
function Fact({ label, value, hint, tone, compact = false, children }) {
  return (
    <div className="border-l border-line px-5 first:border-l-0 first:pl-0">
      <div className="text-label font-semibold uppercase tracking-[0.07em] text-ink-mute">{label}</div>
      <div
        className={`tnum mt-2 flex min-h-[26px] items-center gap-2 font-semibold leading-none tracking-tight ${
          compact ? "text-[18px]" : "text-[24px]"
        } ${tone === "ok" ? "text-signal-ok" : "text-ink"}`}
      >
        {children ?? value}
      </div>
      {hint && <div className="mt-1.5 text-meta text-ink-mute">{hint}</div>}
    </div>
  );
}
