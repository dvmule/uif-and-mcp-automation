// Screens, restyled to the UIF platform vocabulary: KPI cards on the module
// overview, searchable/paged tables with uppercase status badges, side-panel
// forms, and helpful empty states.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Cloud, ExternalLink, Inbox, Mail, Pencil, Play, Plus, Search, Send, Server, Sparkles, Table2, X } from "lucide-react";
import { api } from "./api.js";
import { describeSchedule, pauseState } from "./schedule.js";
import { readShellSession } from "./lib/session.js";
import { downloadCsv } from "./lib/csv.js";
// UIFPE-595: one place decides what a destination's status means and what
// the action that would verify it is called — the picker reads the same rules
import {
  CHANNEL_ORDER,
  channelLabel,
  destinationAddress,
  filterDestinationRows,
  isVerified,
  statusText,
  testActionLabel,
} from "./lib/destinationPicker.js";
import MultiSelect from "./MultiSelect.jsx";
import FilterChips from "./FilterChips.jsx";
// UIFPE-599: the destination form's fields and request body are shared with
// the wizard's add-destination dialog — one definition, so they cannot drift
import DestinationFields from "./DestinationFields.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import RangePicker from "./RangePicker.jsx";
import { NEVER_RAN, exportFormats, filterExports, shortTable,
  isNewExport,
} from "./lib/exportFilters.js";
import { DEFAULT_RANGE, PRESETS, outcomeCounts, publishedInWindow, rangeFromParams, rangeToParams, rangeWindow, successRateOf } from "./lib/overviewRange.js";
import {
  EMPTY_FORM,
  createActionLabel,
  createBody,
  createBusyLabel,
  createHint,
  nextStepAfterCreate,
  readyMessage,
} from "./lib/destinationForm.js";
import {
  Button,
  Card,
  Chip,
  DASH,
  EmptyState,
  Field,
  InfoTip,
  Note,
  PageHeader,
  Pill,
  SkeletonRows,
  StatusBadge,
  Table,
  CountUp,
  TableActions,
  TableFoot,
  Td,
  fmtAbsolute,
  fmtDayTime,
  filterLabel,
  fmtNum,
  fmtRelative,
  inputClass,
} from "./ui.jsx";

const MODULE = "/m/beacon";
const PAGE_SIZE = 25;

// UIFPE-442: compact variant for the filter dropdowns — inputClass is w-full
// by design, which would give each select its own row in the toolbar
// UIFPE-624: fixed 32px so the toolbar's inputs, selects and buttons share one row height
const filterClass = `${inputClass.replace("w-full", "w-auto")} h-8`;
const searchClass = `${inputClass} pl-8`;
// UIFPE-624: the list screens' toolbar sits inside the table card, above the header row
const toolbarClass = "flex flex-wrap items-end gap-3 border-b border-line px-3 py-2.5"; // UIFPE-722: captions above
// UIFPE-762: the chip toolbars have no captions, so everything sits on one centre line
const chipToolbarClass = "flex flex-wrap items-center gap-3 border-b border-line px-3 py-2.5";

/* UIFPE-759: the old toolbar Pager is gone — the Exports list's counter, pager
   and Items per page now sit in the shared TableFoot (ui.jsx), as on Insights. */

// UIFPE-630: a titled section on the open page — heading, one-line purpose,
// an optional control on the right (Destinations, Registries)
function Section({ title, sub, right, className = "", children }) {
  return (
    <section className={className}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
          {sub && <p className="mt-0.5 max-w-3xl text-meta text-ink-mute">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

// UIFPE-624: the list screens' empty row — an icon, the headline the screen
// already used, and one quieter line under it
function ListEmpty({ colSpan, icon: Icon, title, children }) {
  return (
    <EmptyState colSpan={colSpan}>
      <span className="flex flex-col items-center gap-1.5">
        <span className="mb-1 grid h-9 w-9 place-items-center rounded-full bg-accent-soft text-accent">
          <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
        </span>
        <b className="font-semibold text-ink">{title}</b>
        {children && <span>{children}</span>}
      </span>
    </EmptyState>
  );
}

/* ------------------------------------------------------------------ Overview */

// UIFPE-668: a small ring beside the run count. Plain SVG (no chart
// dependency; the charting lib is lazy-loaded only on the export page). Same
// split as the old 250px ring: only the finished outcomes are divided.
// UIFPE-694: the ring takes a size — the Home figure draws it at 92px
function MiniRing({ succeeded, failed, size = 40 }) {
  const total = succeeded + failed;
  const half = size / 2;
  const r = size * 0.4;
  const w = size * 0.15;
  const c = 2 * Math.PI * r;
  const okShare = total ? succeeded / total : 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="shrink-0">
      <circle cx={half} cy={half} r={r} fill="none" stroke="var(--color-dot-fail)" strokeWidth={w} />
      <circle
        className="motion-arc"
        cx={half}
        cy={half}
        r={r}
        fill="none"
        stroke="var(--color-dot-ok)"
        strokeWidth={w}
        strokeDasharray={`${okShare * c} ${c}`}
        transform={`rotate(-90 ${half} ${half})`}
      />
    </svg>
  );
}

// UIFPE-668: one figure on the welcome card's run-health strip — label, large
// number, one quiet line; thin lines divide the four. Values are unchanged.
// UIFPE-694: `ring` sits on the figure's right, centred and filling its height
// UIFPE-754: with `to`, the whole figure is a link to the page behind its number
function StripFigure({ label, value, hint, lead, ring, dot = "bg-accent", mono = false, testid, to }) {
  const body = (
    <>
      {ring && <div className="absolute right-6 top-1/2 -translate-y-1/2 leading-none">{ring}</div>}
      <div className="flex items-center gap-2 text-meta font-bold text-ink">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
        {label}
      </div>
      <div className="flex items-center gap-3">
        {lead}
        <span className="tnum text-kpi font-bold tracking-tight text-accent">{value}</span>
      </div>
      {hint && <div className={`text-meta text-ink-mute ${mono ? "font-mono" : ""}`}>{hint}</div>}
    </>
  );
  const base = "relative grid content-start gap-1.5 bg-paper-head px-6 py-4";
  if (!to) {
    return (
      <div data-testid={testid} className={base}>
        {body}
      </div>
    );
  }
  return (
    <Link
      to={to}
      data-testid={testid}
      title="Open"
      className={`${base} outline-none transition-colors hover:bg-paper-card focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent`}
    >
      {body}
    </Link>
  );
}

// UIFPE-668: how data moves through Beacon — Gold in, delivered by SFTP, S3 or
// email. Decoration: the dashes drift and a dot travels each line (theme.css,
// stilled under prefers-reduced-motion); one text label for screen readers.
const FLOW_LINES = [
  { d: "M58 75H130", stroke: "#c2903a" },
  { d: "M186 75C214 75 212 32 244 32", stroke: "#c1bfff" },
  { d: "M186 75H244", stroke: "#df41cf" },
  { d: "M186 75C214 75 212 118 244 118", stroke: "#56e2e1" },
];
function PipelineDrawing() {
  return (
    <div className="relative hidden h-[150px] w-[360px] shrink-0 lg:block" role="img" aria-label="Gold tables flow through self service and out by SFTP, AWS S3 or email">
      <svg width="360" height="150" viewBox="0 0 360 150" aria-hidden="true">
        {/* UIFPE-711: drawn for the Pearson-purple card — light lines and labels */}
        <g fill="none" stroke="#ffffff" strokeOpacity="0.14" strokeWidth="6" strokeLinecap="round">
          {FLOW_LINES.map((l) => <path key={l.d} d={l.d} />)}
        </g>
        <g className="flow-dash" fill="none" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="8 12">
          {FLOW_LINES.map((l) => <path key={l.d} d={l.d} stroke={l.stroke} />)}
        </g>
        <circle cx="34" cy="75" r="22" fill="#c2903a" />
        <text x="34" y="80" textAnchor="middle" fontSize="14" fontWeight="700" fill="#fff">G</text>
        <circle cx="158" cy="75" r="26" fill="#512eab" />
        {/* UIFPE-676: the centre names what happens there — self service — on
            two lines so it sits inside the node */}
        <text textAnchor="middle" fontSize="9.5" fontWeight="700" fill="#fff">
          <tspan x="158" y="73">Self</tspan>
          <tspan x="158" y="85">service</tspan>
        </text>
        <circle cx="158" cy="75" r="33" fill="none" stroke="#ffce00" strokeOpacity="0.5" strokeWidth="2" />
        <g fontSize="12" fontWeight="700" fill="#ffffff">
          {[["SFTP", 18], ["AWS S3", 61], ["Email", 104]].map(([name, y]) => (
            <g key={name}>
              <rect x="250" y={y} width="28" height="28" rx="8" fill="#ffffff" fillOpacity="0.14" />
              <text x="286" y={y + 19}>{name}</text>
            </g>
          ))}
        </g>
      </svg>
      {[[Server, 18], [Cloud, 61], [Mail, 104]].map(([Icon, y]) => (
        <Icon
          key={y}
          size={14}
          strokeWidth={2}
          aria-hidden="true"
          className="absolute text-pearson-light-purple"
          style={{ left: 257, top: y + 7 }}
        />
      ))}
      {FLOW_LINES.map((l, i) => (
        <span
          key={l.d}
          aria-hidden="true"
          className="flow-dot"
          style={{ background: l.stroke, offsetPath: `path('${l.d}')`, animationDelay: `${-0.9 * i}s` }}
        />
      ))}
    </div>
  );
}

// UIFPE-670: where a run's files went, for the Recent activity table. The
// run's own record first (the deliver step writes summary.targets since
// UIFPE-170); a run without it (older, or failed before delivery) falls back
// to the export's published config. Names come from the destinations list;
// a destination the viewer cannot see, or one that is gone, shows its channel.
const CHANNEL_WORD = { s3: "AWS S3", sftp: "SFTP", email: "Email" };
function runDestinationLabels(run, exportEntry, destinations) {
  const s = exportEntry?.settings;
  const configured =
    s && (Array.isArray(s.deliveries) && s.deliveries.length
      ? s.deliveries.map((d) => ({ id: d.destination_id || null, channel: d.channel || null }))
      : s.delivery
        ? [{ id: s.destination_id || null, channel: s.delivery.channel || null }]
        : null);
  // no delivery on the config = the publisher's default target
  const managed = !!s && !configured;
  const recorded = Array.isArray(run.summary?.targets) && run.summary.targets.length
    ? run.summary.targets.map((t) => ({ id: t.destination_id || null, channel: t.channel || null }))
    : null;
  const list = recorded ?? configured ?? (managed ? [{ id: null, channel: "s3" }] : []);
  return list.map((d) => {
    const hit = d.id && destinations.find((x) => x.id === d.id);
    if (hit) return hit.name;
    if (!d.id && managed) return "Managed S3 partner-drop";
    return d.channel ? `${CHANNEL_WORD[d.channel] ?? d.channel.toUpperCase()} destination` : DASH;
  });
}

// "Sep 19 to Sep 26" — the window's edges as short dates, for the Runs figure
const shortDay = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

// UIFPE-536: the landing screen. This is the old Home dashboard (UIFPE-195)
// and the module overview merged into one — the Modules rail is gone, so the
// panel opens here.
export function Overview() {
  const [catalog, setCatalog] = useState([]);
  const [runs, setRuns] = useState(null);
  // UIFPE-220: gold drift for every export — null = checking, false = the
  // check itself failed (shown honestly, never as "all fine")
  const [drift, setDrift] = useState(null);
  // UIFPE-670: destination names for Recent activity — null = loading; a
  // failed call leaves [] so the column falls back to channels
  const [destinations, setDestinations] = useState(null);
  // UIFPE-675: each export's schedule, keyed by job, from the same bulk call
  // the Exports page makes — null = loading, false = the call failed (shown
  // as the dash, never as "manual")
  const [schedules, setSchedules] = useState(null);
  // UIFPE-610: the chosen time range, and its counts from GET /runs/summary
  // (null = loading). The counts used to come from the newest 50 runs across
  // every export, which was silently low on a busy week.
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [summary, setSummary] = useState(null);
  const latestAsk = useRef(0);

  useEffect(() => {
    api.catalog().then(setCatalog).catch(() => setCatalog([]));
    api.runHistory().then(setRuns).catch(() => setRuns({ enabled: false, runs: [] }));
    api.destinations().then(setDestinations).catch(() => setDestinations([]));
    api
      .listSchedules()
      .then((r) => setSchedules(Object.fromEntries((r.schedules || []).map((s) => [s.job_id, s]))))
      .catch(() => setSchedules(false));
    api.drift().then(setDrift).catch(() => setDrift(false));
  }, []);

  // worked out once per choice: a quick pick ends "now", so recomputing it on
  // every render would move the window and refetch in a loop
  const window_ = useMemo(() => rangeWindow(range), [range]);
  useEffect(() => {
    if (window_.error) return;
    const ask = ++latestAsk.current; // a slower, older answer must not win
    setSummary(null);
    api
      .runSummary(window_.since, window_.until)
      .then((s) => ask === latestAsk.current && setSummary(s))
      .catch((e) => ask === latestAsk.current && setSummary({ enabled: false, reason: e.message }));
  }, [window_.since, window_.until]);

  const list = runs?.runs ?? [];
  // UIFPE-198: id and name are separate columns — name comes from the catalog
  const nameFor = (jobId) => catalog.find((c) => c.job_id === jobId)?.name;
  // stopped / skipped runs are neutral (UIFPE-203/206) — out of the rate
  const counts = outcomeCounts(summary?.by_status);
  const loaded = summary !== null;
  const rateValue = successRateOf(counts);
  const rate = rateValue === null ? DASH : `${rateValue}%`;
  const lastOk = list.find((r) => r.status === "succeeded");
  // UIFPE-754: the tiles link to the page behind each number, carrying the
  // range; Published exports now counts versions published inside the window
  const rangeQs = new URLSearchParams(rangeToParams(range)).toString();
  const publishedCount = window_.error ? DASH : catalog.filter((e) => publishedInWindow(e, window_)).length;
  // UIFPE-668: the name the top bar's user menu already shows (the shared
  // shell session, read once like Shell does); no session = plain "Home" (UIFPE-694)
  const [welcomeName] = useState(() => readShellSession()?.username || "");

  return (
    <section>
      {/* UIFPE-668: one welcome card — greeting and the two main actions, a
          drawing of how data moves, and the run-health strip along its foot.
          The card itself does not clip (the range picker's Custom panel opens
          below it); only the decorative rings are clipped to its corners. */}
      <div className="relative mb-5 rounded-card bg-paper-card shadow-card">
        {/* UIFPE-711: the welcome half in Pearson purple with reversed text;
            its rings are clipped to it, the run-health strip stays light */}
        <div className="beacon-welcome relative flex items-center justify-between gap-6 overflow-hidden rounded-t-card px-8 py-6">
          {/* UIFPE-713: the hex mesh over the glows and grain (theme.css) */}
          <div className="beacon-welcome-hex" aria-hidden="true" />
          <svg className="pointer-events-none absolute -right-10 -top-[70px]" width="480" height="300" viewBox="0 0 480 300" aria-hidden="true">
            <g fill="none" stroke="#ffffff" strokeOpacity="0.10" strokeWidth="2">
              {[54, 104, 156, 208].map((r) => <circle key={r} cx="310" cy="150" r={r} />)}
            </g>
            <g fill="none" stroke="#56e2e1" strokeOpacity="0.22" strokeWidth="1.5">
              {[80, 182].map((r) => <circle key={r} cx="310" cy="150" r={r} />)}
            </g>
          </svg>
          <div className="relative min-w-0">
            <div className="text-label font-bold uppercase tracking-[0.08em] text-pearson-light-purple">
              Self service exports
            </div>
            <h1 className="mt-1.5 text-[28px] font-bold leading-tight tracking-tight text-white">
              {welcomeName ? `Welcome, ${welcomeName}.` : "Home"}
            </h1>
            <p className="mt-1.5 max-w-xl text-body text-white/80">
              Publishing and run health across every export, for the window you choose.
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                to={`${MODULE}/exports/new`}
                className="inline-flex h-10 items-center gap-2 rounded-full bg-white px-5 text-body font-bold text-pearson-purple transition-colors hover:bg-pearson-mist"
              >
                <Sparkles size={14} /> New Export
              </Link>
              <Link
                to={`${MODULE}/destinations`}
                className="inline-flex h-10 items-center gap-2 rounded-full border-[1.5px] border-white px-5 text-body font-bold text-white transition-colors hover:bg-white/10"
              >
                <Send size={14} /> Destinations
              </Link>
            </div>
          </div>
          <PipelineDrawing />
        </div>

        {/* the strip: its header carries the window and the range control */}
        <div className="relative flex flex-wrap items-center justify-between gap-3 border-t border-line bg-paper-head px-8 py-2.5">
          <span className="text-meta font-bold text-ink-soft">
            Run health ·{" "}
            {range.kind === "custom" ? "custom range" : `${shortDay(window_.since)} to ${shortDay(window_.until)}`}
          </span>
          {/* UIFPE-610: one range for the ring and the run figures */}
          <RangePicker value={range} onChange={setRange} />
        </div>
        {/* hairlines come from the 1px gaps over the line colour, so they
            follow the layout: four across on wide screens, two by two below */}
        <div className="relative grid grid-cols-1 gap-px overflow-hidden rounded-b-card border-t border-line bg-line sm:grid-cols-2 lg:grid-cols-4" data-tour="home-kpis">
          <StripFigure
            label={`Published exports · ${window_.label}`}
            value={typeof publishedCount === "number" ? <CountUp value={publishedCount} /> : publishedCount}
            hint={`${catalog.length} live in the catalog`}
            testid="kpi-active"
            to={`${MODULE}/exports?${rangeQs}`}
          />
          <StripFigure
            label={`Runs · ${window_.label}`}
            value={loaded ? <CountUp value={counts.total} /> : DASH}
            to={`${MODULE}/runs?tab=jobs&${rangeQs}`}
            ring={
              loaded && summary.enabled !== false && counts.succeeded + counts.failed > 0 ? (
                <MiniRing succeeded={counts.succeeded} failed={counts.failed} size={92} />
              ) : null
            }
            hint={
              !loaded ? null : summary.enabled === false ? (
                summary.reason
              ) : counts.total === 0 ? (
                "No runs in this time range."
              ) : (
                <>
                  <b className="tnum text-signal-ok">{counts.succeeded}</b> succeeded ·{" "}
                  <b className={`tnum ${counts.failed ? "text-signal-fail" : ""}`}>{counts.failed}</b> failed
                </>
              )
            }
          />
          <StripFigure
            label={`Success rate · ${window_.label}`}
            value={loaded && rateValue !== null ? <CountUp value={rateValue} format={(n) => `${n}%`} /> : loaded ? rate : DASH}
            hint="of finished runs"
            dot="bg-signal-ok"
            to={`${MODULE}/runs?tab=jobs&${rangeQs}&last=succeeded`}
          />
          <StripFigure
            label="Last successful delivery"
            value={lastOk ? fmtRelative(lastOk.recorded_at) : DASH}
            hint={lastOk?.job_id}
            mono
            to={lastOk ? `${MODULE}/exports/${lastOk.job_id}` : undefined}
          />
        </div>
      </div>

      {/* UIFPE-610: stopped / skipped are neutral — said, never drawn.
          UIFPE-676: said only when the window has some; the standing sentence is gone */}
      {loaded && counts.neutral > 0 && (
        <p className="-mt-2 mb-5 text-meta text-ink-mute">
          {`+ ${counts.neutral} stopped or skipped, counted in the total but never in the success rate.`}
        </p>
      )}

      {/* UIFPE-220: gold drift — say it on the landing screen BEFORE the 00:30
          schedule finds out. Rendered only when there is something to say. */}
      {drift === false && (
        <div className="mb-4">
          <Note tone="warn">
            The gold drift check could not run — export configs were not verified
            against the live catalog just now.
          </Note>
        </div>
      )}
      {drift && drift.drift_count > 0 && (
        <div className="mb-4">
          <Note tone="fail">
            <b>Gold drift:</b> {drift.drift_count} export
            {drift.drift_count === 1 ? "" : "s"} no longer match the gold catalog and
            will fail their next run —{" "}
            {drift.exports
              .filter((e) => e.status === "drift")
              .map((e, i, all) => (
                <span key={e.job_id}>
                  <Link
                    to={`${MODULE}/exports/${e.job_id}`}
                    className="font-mono font-semibold text-accent hover:underline"
                  >
                    {e.job_id}
                  </Link>
                  {e.name ? ` (${e.name})` : ""}
                  {i < all.length - 1 ? ", " : ""}
                </span>
              ))}
            . Each export's page lists what moved; the fix is edit + republish.
          </Note>
        </div>
      )}

      {/* UIFPE-625: Recent activity as a titled section; the name leads, the id
          sits beneath. UIFPE-694: the table sits on a white card, as in the
          approved redesign */}
      <div className="pt-6" data-tour="home-runs">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <h2 className="text-[18px] font-extrabold tracking-tight text-ink">Recent activity</h2>
          <Link to={`${MODULE}/runs`} className="inline-flex items-center gap-1 text-meta font-semibold text-accent hover:underline">
            All runs <ArrowRight size={13} />
          </Link>
        </div>
        {/* UIFPE-668: the batch id gives way to the rows each run published
            (the Runs page keeps the batch); same field as its Rows out column */}
        <div className="rounded-card border border-white/70 bg-paper-card px-5 py-2 shadow-card">
        <Table head={["Export", "Status", "When", "Destination", "Schedule", { label: "Rows published", align: "right" }]}>
          {/* UIFPE-418: null = first load in flight — shimmer, never "empty" */}
          {runs === null && <SkeletonRows cols={6} rows={5} />}
          {runs !== null && list.length === 0 && (
            <ListEmpty colSpan={6} icon={Inbox} title={runs?.enabled === false ? runs.reason : "No runs yet"}>
              {runs?.enabled === false ? null : "Publish an export and trigger it from its page."}
            </ListEmpty>
          )}
          {list.slice(0, 8).map((r) => (
            <tr key={`${r.job_id}-${r.batch_id}`} className="transition-colors hover:bg-paper-head">
              <Td>
                <Link to={`${MODULE}/exports/${r.job_id}`} className="font-semibold text-ink hover:text-accent hover:underline">
                  {nameFor(r.job_id) ?? r.job_id}
                </Link>
                {nameFor(r.job_id) && (
                  <div className="font-mono text-meta text-ink-mute">{r.job_id}</div>
                )}
              </Td>
              <Td>
                <StatusBadge status={r.status} title={r.error} />
              </Td>
              {/* UIFPE-408: the run's actual date and time, not "12 hours ago" —
                  this health view is read against schedules and logs. UIFPE-537
                  extended it to the Runs and export-detail tables.
                  fmtAbsolute renders in the viewer's timezone with no zone label,
                  so the raw recorded_at rides along as the hover tooltip — the one
                  unambiguous value (the Schedule column's title= pattern) */}
              <Td className="min-w-[8rem] whitespace-nowrap text-ink-mute" title={r.recorded_at}>
                {/* UIFPE-722: "Mon 29 Sep · 09:12" */}
                {fmtDayTime(r.recorded_at)}
              </Td>
              {/* UIFPE-670: the first destination by name, "+N" for more; the
                  full list is the tooltip. Dash while the names load. */}
              {(() => {
                const labels = destinations === null
                  ? []
                  : runDestinationLabels(r, catalog.find((c) => c.job_id === r.job_id), destinations);
                return (
                  <Td className="text-ink-soft" title={labels.length > 1 ? labels.join(", ") : undefined}>
                    <span className="block max-w-[180px] truncate lg:max-w-[260px]">
                      {labels.length ? labels[0] : DASH}
                      {labels.length > 1 && <span className="ml-1 font-semibold text-ink-mute">+{labels.length - 1}</span>}
                    </span>
                  </Td>
                );
              })()}
              {/* UIFPE-675: the export's schedule in the Exports page's words
                  (describeSchedule; raw schedule and timezone on hover) with its
                  pause pill; no schedule reads "manual", as there */}
              {(() => {
                const schedule = schedules ? schedules[r.job_id] : undefined;
                if (!schedules) return <Td className="text-meta text-ink-mute">{DASH}</Td>;
                if (!schedule) return <Td className="text-meta text-ink-mute">manual</Td>;
                const paused = pauseState(schedule);
                return (
                  <Td className="whitespace-nowrap text-meta text-ink-soft" title={describeSchedule(schedule.expression, schedule.timezone)}>
                    {describeSchedule(schedule.expression)}
                    {paused && (
                      <Pill tone="warn" className="ml-1">
                        {paused.kind === "active"
                          ? `paused until ${paused.until}`
                          : paused.kind === "upcoming"
                            ? `pauses ${paused.from}`
                            : "paused"}
                      </Pill>
                    )}
                  </Td>
                );
              })()}
              {/* a run without a count (failed, still running, or from before
                  row counts were recorded) shows the platform's dash */}
              <Td className="tnum text-right">{fmtNum(r.summary?.output_rows)}</Td>
            </tr>
          ))}
        </Table>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- Exports */

export function Exports() {
  // null = first load in flight (UIFPE-418); [] = loaded and empty
  const [rows, setRows] = useState(null);
  const [runs, setRuns] = useState([]);
  const [schedules, setSchedules] = useState({});
  const [query, setQuery] = useState("");
  // UIFPE-442: structured filters next to the free-text search. UIFPE-695:
  // multi-select dropdowns that remember what is turned OFF — every option
  // (and any that appears later) starts on, as the old "All …" did
  const [formatsOff, setFormatsOff] = useState([]);
  const [scheduleOff, setScheduleOff] = useState([]);
  const [lastOff, setLastOff] = useState([]);
  // UIFPE-446: retired exports stay hidden until explicitly asked for
  const [showRetired, setShowRetired] = useState(false);
  const [page, setPage] = useState(0);
  const [per, setPer] = useState(PAGE_SIZE); // UIFPE-759: Items per page
  // UIFPE-754: "Published" — exports whose current version was published in a
  // window; null = any time. Home's tile deep-links with ?range= / ?from&to.
  const [params, setParams] = useSearchParams();
  const [published, setPublished] = useState(() => rangeFromParams(params));
  // the URL can change while the list stays mounted (back/forward, a second
  // tile) — follow it; choosePublished writes both sides, so this never loops
  useEffect(() => {
    const next = rangeFromParams(params);
    if (JSON.stringify(next) !== JSON.stringify(published)) setPublished(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const publishedWindow = useMemo(() => (published ? rangeWindow(published) : null), [published]);
  const choosePublished = (next) => {
    setPublished(next);
    setPage(0);
    setParams(next ? rangeToParams(next) : {}, { replace: true });
  };
  // UIFPE-749: Run now from the row — the export page's call, with the
  // outcome said above the table (the list has no live row to follow)
  const [starting, setStarting] = useState(null); // job_id in flight
  const [runMsg, setRunMsg] = useState(null); // { tone, text, jobId? }

  useEffect(() => {
    setRows(null);
    api.catalog(showRetired).then(setRows).catch(() => setRows([]));
  }, [showRetired]);

  const runNow = async (r) => {
    setStarting(r.job_id);
    setRunMsg(null);
    try {
      await api.startRun(r.job_id);
      setRunMsg({ tone: "ok", text: `Run started for ${r.name ?? r.job_id}.`, jobId: r.job_id });
    } catch (e) {
      setRunMsg({ tone: "fail", text: `${r.name ?? r.job_id}: ${e.message}` });
    } finally {
      setStarting(null);
    }
  };

  useEffect(() => {
    api.runHistory().then((r) => setRuns(r.runs || [])).catch(() => setRuns([]));
    // UIFPE-207: one bulk call, keyed by job — never a GET per row
    api
      .listSchedules()
      .then((r) =>
        setSchedules(Object.fromEntries((r.schedules || []).map((s) => [s.job_id, s])))
      )
      .catch(() => setSchedules({}));
  }, []);

  const lastRunFor = (jobId) => runs.find((r) => r.job_id === jobId);

  // dropdown options come from the data itself — never a hardcoded list that
  // drifts from what the registries actually offer
  // UIFPE-749: exportFormats reads the single `settings.format` too
  const formatOptions = useMemo(
    () => [...new Set((rows ?? []).flatMap((r) => exportFormats(r)))].sort(),
    [rows]
  );
  const statusOptions = useMemo(
    () => [...new Set(runs.map((r) => r.status))].sort(),
    [runs]
  );

  // UIFPE-695: the rules are lib/exportFilters.js (vitest)
  const filtered = useMemo(
    () =>
      filterExports(
        rows ?? [],
        { query, formatsOff, scheduleOff, lastOff, publishedWindow: publishedWindow?.error ? null : publishedWindow },
        { runs, schedules }
      ),
    [rows, query, formatsOff, scheduleOff, lastOff, publishedWindow, runs, schedules]
  );
  // a dropdown hands back what is ON; keep the complement
  const offFrom = (keys, setOff) => (on) => {
    setOff(keys.filter((k) => !on.includes(k)));
    setPage(0);
  };
  const scheduleKeys = ["scheduled", "manual"];
  const lastKeys = [...statusOptions, NEVER_RAN];

  const safePage = Math.min(page, Math.max(0, Math.ceil(filtered.length / per) - 1));
  const start = safePage * per;
  const pageRows = filtered.slice(start, start + per);

  // UIFPE-759: the filtered rows, every page, as the table reads them
  const exportCsv = () => {
    const head = ["ID", "Export", "Source", "Format", "Version", "Schedule", "Last run", "Last run time"];
    const body = filtered.map((r) => {
      const run = lastRunFor(r.job_id);
      const s = schedules[r.job_id];
      const p = s ? pauseState(s) : null;
      return [
        r.export_id ?? r.job_id,
        r.name ?? r.job_id,
        (r.mapping_config?.sources || []).map((x) => x.path).join("; "),
        exportFormats(r).map((f) => f.toUpperCase()).join("; "),
        `v${r.version}`,
        s ? `${describeSchedule(s.expression)}${p?.kind === "active" ? ` (paused until ${p.until})` : p ? " (paused)" : ""}` : "manual",
        run?.status ?? "",
        run?.recorded_at ?? "",
      ];
    });
    downloadCsv("beacon-exports.csv", [head, ...body]);
  };
  const clearAll = () => {
    setQuery("");
    setFormatsOff([]);
    setScheduleOff([]);
    setLastOff([]);
    choosePublished(null);
    setShowRetired(false);
    setRunMsg(null);
    setPage(0);
  };

  return (
    <section>
      <PageHeader
        title="Exports"
        sub="Published, versioned export jobs. Publishing appends an immutable version; rollback is running an earlier one."
        actions={
          <Link to={`${MODULE}/exports/new`} data-tour="new-export">
            <Button variant="primary">
              <Sparkles size={14} /> New Export
            </Button>
          </Link>
        }
      />

      <Card pad={false} data-tour="exports-list">
      {/* UIFPE-762: Find, then one chip per active filter and "+ Filter" — the
          filters and what they hide are unchanged (lib/exportFilters.js) */}
      <div className={chipToolbarClass}>
        <span className="relative block w-[240px] max-w-full">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            placeholder="Export or source table"
            aria-label="Find an export or source table"
            className={searchClass}
          />
        </span>
        <FilterChips
          filters={[
            {
              key: "format", name: "Format", kind: "multi", noun: "formats",
              items: formatOptions.map((f) => ({ key: f, label: f.toUpperCase() })),
              selected: formatOptions.filter((f) => !formatsOff.includes(f)),
              onChange: offFrom(formatOptions, setFormatsOff),
            },
            {
              key: "schedule", name: "Schedule", kind: "multi", noun: "schedules",
              items: [
                { key: "scheduled", label: "Scheduled" },
                { key: "manual", label: "Manual only" },
              ],
              selected: scheduleKeys.filter((k) => !scheduleOff.includes(k)),
              onChange: offFrom(scheduleKeys, setScheduleOff),
              start: ["scheduled"],
            },
            {
              key: "last", name: "Last run", kind: "multi", noun: "last runs",
              items: lastKeys.map((k) => ({ key: k, label: k === NEVER_RAN ? "never ran" : k })),
              selected: lastKeys.filter((k) => !lastOff.includes(k)),
              onChange: offFrom(lastKeys, setLastOff),
              start: ["failed"],
            },
            {
              // UIFPE-754: the window the current version was published in
              key: "published", name: "Published", kind: "single",
              items: [
                { key: "any", label: "Any time" },
                ...PRESETS.map((p) => ({ key: p.id, label: `Last ${p.label}` })),
                ...(published?.kind === "custom" ? [{ key: "custom", label: publishedWindow?.label || "Custom" }] : []),
              ],
              value: published ? (published.kind === "custom" ? "custom" : published.id) : "any",
              defaultValue: "any",
              start: DEFAULT_RANGE.id,
              onChange: (v) => {
                if (v === "any") choosePublished(null);
                else if (v !== "custom") choosePublished({ kind: "preset", id: v });
              },
            },
            {
              // UIFPE-446: retired exports stay hidden until explicitly asked for
              key: "retired", name: "Retired exports", kind: "toggle", onText: "shown",
              on: showRetired,
              onChange: (v) => {
                setShowRetired(v);
                setPage(0);
              },
            },
          ]}
        />
        {/* UIFPE-759: Export and Clear all; the pager moved to the table's foot */}
        <TableActions onExport={exportCsv} exportDisabled={!filtered.length} onClear={clearAll} className="pb-0" />
      </div>

        {runMsg && (
          <div className="border-b border-line px-3 py-2">
            <Note tone={runMsg.tone}>
              {runMsg.text}
              {runMsg.jobId && (
                <>
                  {" "}
                  <Link to={`${MODULE}/exports/${runMsg.jobId}`} className="font-semibold text-accent hover:underline">
                    Open the export
                  </Link>{" "}
                  to follow it.
                </>
              )}
            </Note>
          </div>
        )}

        {/* UIFPE-749: fixed widths for the short columns so the name keeps the
            room and wraps to two lines at most at 1024px */}
        <Table
          stagger
          head={[
            { label: "ID", width: 84 },
            { label: "Export" },
            { label: "Source", width: 150 },
            { label: "Format", width: 90 },
            { label: "Version", width: 80 },
            { label: "Schedule", width: 210 },
            { label: "Last run", width: 160, title: "The newest run's status and when it finished" },
            { label: "", width: 96 },
          ]}
        >
          {rows === null && <SkeletonRows cols={8} rows={5} />}
          {rows !== null && pageRows.length === 0 && (
            rows.length ? (
              <ListEmpty colSpan={8} icon={Search} title="No exports match those filters.">
                Clear a filter or widen the search to see more.
              </ListEmpty>
            ) : (
              <ListEmpty colSpan={8} icon={Inbox} title="No exports published yet.">
                Publish your first one with New Export above.
              </ListEmpty>
            )
          )}
          {pageRows.map((r) => {
            const run = lastRunFor(r.job_id);
            const schedule = schedules[r.job_id];
            const source = r.mapping_config?.sources?.[0]?.path || DASH;
            // UIFPE-237: multi-source feeds show the driving source + a count
            const extraSources = (r.mapping_config?.sources?.length || 0) - 1;
            return (
              <tr key={r.job_id} className="transition-colors hover:bg-paper-head">
                <Td className="tnum font-mono text-meta text-ink-mute">{r.export_id ?? r.job_id}</Td>
                <Td className="min-w-[200px]">
                  {/* UIFPE-638: plain text — the row's Open button is the one way in */}
                  <span className="font-semibold text-ink">{r.name ?? r.job_id}</span>
                  {/* UIFPE-722: a first version published in the last day */}
                  {isNewExport(r) && (
                    <Pill tone="ok" className="ml-1.5">
                      new
                    </Pill>
                  )}
                  {r.retired && (
                    <Pill tone="warn" className="ml-1.5">
                      retired
                    </Pill>
                  )}
                </Td>
                {/* UIFPE-695: the short table name an author reads, "+N"
                    naming the rest on hover */}
                <Td className="whitespace-nowrap text-ink">
                  {source === DASH ? (
                    <span className="text-ink-mute">{DASH}</span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 font-medium" title={source}>
                      <Table2 size={14} strokeWidth={1.75} aria-hidden="true" className="shrink-0 text-accent" />
                      {shortTable(source)}
                    </span>
                  )}
                  {extraSources > 0 && (
                    <span title={(r.mapping_config?.sources || []).map((x) => x.path).join(", ")}>
                      <Pill tone="mute" className="ml-1.5">
                        +{extraSources}
                      </Pill>
                    </span>
                  )}
                </Td>
                <Td>
                  {exportFormats(r).map((f) => (
                    <Pill key={f} tone="accent" className="mr-1 uppercase">
                      {f}
                    </Pill>
                  ))}
                </Td>
                <Td className="tnum font-mono">v{r.version}</Td>
                <Td>
                  {schedule ? (
                    <span
                      className="text-meta"
                      title={describeSchedule(schedule.expression, schedule.timezone)}
                    >
                      {describeSchedule(schedule.expression)}
                      {(() => {
                        const paused = pauseState(schedule);
                        if (!paused) return null;
                        return (
                          <Pill tone="warn" className="ml-1">
                            {paused.kind === "active"
                              ? `paused until ${paused.until}`
                              : paused.kind === "upcoming"
                                ? `pauses ${paused.from}`
                                : "paused"}
                          </Pill>
                        );
                      })()}
                    </span>
                  ) : (
                    <span className="text-meta text-ink-mute">manual</span>
                  )}
                </Td>
                <Td>
                  {run ? (
                    <span className="flex flex-col items-start gap-0.5">
                      <StatusBadge status={run.status} title={run.error} />
                      {/* UIFPE-749: when it finished, in Home's day-and-time form */}
                      <span className="whitespace-nowrap text-meta text-ink-mute">{fmtDayTime(run.recorded_at)}</span>
                    </span>
                  ) : (
                    <span className="text-ink-mute">{DASH}</span>
                  )}
                </Td>
                <Td className="whitespace-nowrap text-right">
                  {/* UIFPE-749: Run now beside Open — the export page's startRun,
                      disabled for a retired export or while its last run is live */}
                  {(() => {
                    const liveRun = run && /^(running|starting)$/i.test(run.status);
                    const why = r.retired ? "Retired exports don't run" : liveRun ? "A run is already in progress" : "Run now";
                    return (
                      <button
                        type="button"
                        onClick={() => runNow(r)}
                        disabled={r.retired || liveRun || starting === r.job_id}
                        aria-label={`Run ${r.name ?? r.job_id} now`}
                        title={why}
                        className="inline-grid h-8 w-8 place-items-center rounded-full text-accent transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                      >
                        <Play size={15} strokeWidth={2} aria-hidden="true" className={starting === r.job_id ? "animate-pulse" : ""} />
                      </button>
                    );
                  })()}
                  {/* UIFPE-711: the open icon alone — the name is its label */}
                  <Link
                    to={`${MODULE}/exports/${r.job_id}`}
                    aria-label={`Open ${r.name ?? r.job_id}`}
                    title="Open"
                    className="ml-1 inline-grid h-8 w-8 place-items-center rounded-full text-accent transition-colors hover:bg-accent-soft"
                  >
                    <ExternalLink size={16} strokeWidth={2} aria-hidden="true" />
                  </Link>
                </Td>
              </tr>
            );
          })}
        </Table>
        <TableFoot total={filtered.length} page={safePage} setPage={setPage} per={per} setPer={setPer} noun="exports" />
      </Card>
    </section>
  );
}

/* ------------------------------------------------------------ Destinations */

const CHANNEL_ICON = { s3: Cloud, sftp: Server, email: Mail };

export function Destinations() {
  // null = first load in flight (UIFPE-418); [] = loaded and empty
  const [rows, setRows] = useState(null);
  const [channels, setChannels] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [msg, setMsg] = useState(null);
  // UIFPE-546: the signed-in shell user — rows someone else owns lose their
  // manage buttons here (the API enforces 403 regardless). No session (auth
  // off, local dev) = everything manageable, as before.
  const [me] = useState(() => readShellSession()?.username || null);
  const mine = (d) => !me || !d.owner || d.owner === me;
  const [testing, setTesting] = useState(null); // destination id under test
  const [deleting, setDeleting] = useState(null); // destination id being deleted
  const [saving, setSaving] = useState(false); // add/edit submit in flight
  // UIFPE-634: which half of an add is running — "creating", then "testing"
  const [addPhase, setAddPhase] = useState(null);
  const [editing, setEditing] = useState(null); // destination being edited
  // UIFPE-755: the popup opens read-only from the row's Open; Edit inside it
  // switches to the form (same `editing`, same filled form)
  const [viewing, setViewing] = useState(false);
  // UIFPE-699: the add/edit popup, and the list's find box and Type filter
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [typesOff, setTypesOff] = useState([]);
  // UIFPE-440: destination whose partner bucket-policy popup is open
  const [policyFor, setPolicyFor] = useState(null);
  // UIFPE-602: in-app confirmation instead of window.confirm
  const [confirm, confirmDialog] = useConfirm();

  const reload = () => api.destinations().then(setRows).catch(() => {});
  useEffect(() => {
    reload();
    api.channels().then(setChannels).catch(() => {});
  }, []);

  // UIFPE-631 (owner-approved): Escape closes the partner-policy popup, as its
  // backdrop and Close button already do — it is read-only, nothing is lost
  useEffect(() => {
    if (!policyFor) return undefined;
    const onKey = (e) => e.key === "Escape" && setPolicyFor(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [policyFor]);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const openAdd = () => {
    setMsg(null);
    setEditing(null);
    setViewing(false);
    setForm(EMPTY_FORM);
    setOpen(true);
  };

  // `view` (UIFPE-755): open read-only; the popup's Edit button flips it to the form
  const startEdit = (d, view = false) => {
    setMsg(null);
    setOpen(true);
    setEditing(d);
    setViewing(view);
    setForm({
      name: d.name,
      channel: d.channel,
      destination_s3_prefix: d.destination_s3_prefix || "",
      host: d.host || "",
      remote_dir: d.remote_dir || "",
      username: "",
      password: "",
      recipients: (d.recipients || []).join(", "),
      // legacy rows carry no visibility and behave as public (UIFPE-546)
      visibility: d.visibility || "public",
    });
  };

  // closes the popup after a save — the result message stays for the page
  const cancelEdit = () => {
    setOpen(false);
    setEditing(null);
    setViewing(false);
    setForm(EMPTY_FORM);
  };

  // Cancel / ✕ / backdrop / Escape — never while a save or test is running
  const closeForm = () => {
    if (saving || addPhase) return;
    cancelEdit();
    setMsg(null);
  };

  useEffect(() => {
    if (!open || policyFor) return undefined;
    const onKey = (e) => e.key === "Escape" && closeForm();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const runDelete = async (d) => {
    const ok = await confirm({
      title: `Delete ${d.name}?`,
      body: "It is removed from your list, and exports can no longer deliver to it.",
      points: [
        // only sftp stores a credential (email and s3 ride on IAM)
        ...(d.channel === "sftp"
          ? ["Its stored password is deleted too — an admin can recover it for 7 days."]
          : []),
        "If a published export still uses it, the delete is refused.",
      ],
      action: "Delete destination",
    });
    if (!ok) return;
    setMsg(null);
    setDeleting(d.id);
    try {
      const r = await api.deleteDestination(d.id);
      setMsg({
        tone: "ok",
        text: `${d.name} deleted${r.secret_scheduled_for_deletion ? " — credential deletion scheduled (7-day recovery)" : ""}.`,
      });
      reload();
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setDeleting(null);
    }
  };

  const runTest = async (d) => {
    setMsg(null);
    setTesting(d.id);
    try {
      const r = await api.testDestination(d.id);
      if (r.ok) {
        setMsg({ tone: "ok", text: `${d.name} verified${r.host_key_pinned ? " — server host key pinned on first contact" : ""}.` });
      } else {
        // UIFPE-722: a partner's bucket fails until its policy is applied — hand it over here
        setMsg({
          tone: "fail",
          text: `${d.name}: connection test failed — ${r.error}`,
          action: d.partner_policy ? { label: "Show partner policy", run: () => setPolicyFor(d) } : undefined,
        });
      }
      reload();
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
    } finally {
      setTesting(null);
    }
  };

  // UIFPE-440: hand the partner the exact policy JSON, not a description of it
  const copyPolicy = async (d) => {
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(d.partner_policy.statement, null, 2)
      );
      setMsg({ tone: "ok", text: "Policy statement copied — send it to the partner to add to their bucket policy." });
    } catch {
      setMsg({ tone: "fail", text: "Could not copy — select the JSON and copy it manually." });
    }
  };

  const submit = async () => {
    setMsg(null);
    setSaving(true);
    try {
      await doSubmit();
    } finally {
      setSaving(false);
    }
  };

  const doSubmit = async () => {
    if (editing) {
      // UIFPE-606: the name is editable — a label; the stored secret and every
      // export pointing here are unaffected. The API refuses a taken name.
      const body = { name: form.name.trim() };
      if (editing.channel === "s3") body.destination_s3_prefix = form.destination_s3_prefix;
      else if (editing.channel === "email") body.recipients = form.recipients.split(",").map((s) => s.trim()).filter(Boolean);
      else {
        body.host = form.host;
        body.remote_dir = form.remote_dir;
        // credential only when re-typed — blank means "keep the stored one"
        if (form.username || form.password) {
          body.credential = { username: form.username, password: form.password };
        }
      }
      // UIFPE-546: visibility is channel-independent and owner-editable
      body.visibility = form.visibility;
      try {
        const saved = await api.updateDestination(editing.id, body);
        const name = saved?.name || body.name;
        const renamed = name !== editing.name ? ` (was ${editing.name})` : "";
        // a rename alone changes no endpoint, so the API keeps the status —
        // ask for a re-test only when the edit actually re-opened it
        const retest =
          editing.channel === "sftp" &&
          saved?.status === "pending_connection_test" &&
          editing.status !== "pending_connection_test";
        setMsg({
          tone: "ok",
          text: retest
            ? `${name}${renamed} updated — run its connection test again (edits are unproven until re-tested).`
            : `${name}${renamed} updated.`,
        });
        cancelEdit();
        reload();
      } catch (e) {
        setMsg({ tone: "fail", text: e.message });
      }
      return;
    }
    let created;
    try {
      setAddPhase("creating");
      created = await api.addDestination(createBody(form));
    } catch (e) {
      setMsg({ tone: "fail", text: e.message });
      setAddPhase(null);
      return;
    }
    // the destination exists from here on: clear the form whatever the test says
    setForm({ ...form, name: "", destination_s3_prefix: "", host: "", remote_dir: "", username: "", password: "", recipients: "" });
    // UIFPE-634: "Test & save" used to only save. SFTP and S3 now run the
    // connection test straight after creating — the same rule the wizard's
    // dialog follows (lib/destinationForm, UIFPE-633).
    const next = nextStepAfterCreate(created);
    if (next === "policy") {
      // UIFPE-635: a partner's own bucket fails its test until they apply our
      // bucket policy — say the next step and open the policy in one click
      setMsg({
        tone: "warn",
        text: `${created.name} created. It is on the partner's own bucket, so it can only pass its connection test after they add Beacon's bucket policy. Send them the policy, then press Test on its row once they confirm.`,
        action: { label: "Show partner policy", run: () => setPolicyFor(created) },
      });
    } else if (next === "test") {
      setAddPhase("testing");
      setTesting(created.id);
      try {
        const r = await api.testDestination(created.id);
        setMsg(
          r.ok
            ? {
                tone: "ok",
                text: `${created.name} created and verified${
                  r.host_key_pinned ? " — server host key pinned on first contact" : ""
                }. Exports can use it now.`,
              }
            : {
                tone: "fail",
                text: `${created.name} was created, but its connection test failed — ${
                  r.error || "no reason given"
                }. Fix it if needed and press Re-test on its row.`,
              }
        );
      } catch (e) {
        setMsg({
          tone: "fail",
          text: `${created.name} was created, but its connection test could not run — ${e.message}. Press Re-test on its row.`,
        });
      } finally {
        setTesting(null);
      }
    } else {
      // UIFPE-636: said true for the channel (email today) — no credential,
      // not waiting for anything
      setMsg({ tone: "ok", text: readyMessage(created) });
    }
    setAddPhase(null);
    // the destination exists: the popup closes and the result shows on the page
    setOpen(false);
    reload();
  };

  // UIFPE-699: the list's Type filter and find box
  const typeIds = [...new Set([...CHANNEL_ORDER, ...(rows ?? []).map((d) => d.channel)])];
  const shown = filterDestinationRows(rows, { query, channelsOff: typesOff });
  const verified = (rows ?? []).filter(isVerified).length;
  const busy = saving || !!addPhase;

  const policyNote = editing
    ? editing.partner_policy
      ? "the policy shown is for the saved prefix"
      : "no partner policy needed for this prefix"
    : form.channel === "s3"
      ? "s3://bucket/prefix — a partner's own bucket gets its policy right after you create it"
      : "";

  return (
    <section>
      {confirmDialog}
      <PageHeader
        title="Destinations"
        sub="Your partner endpoints — you own these; platform admins own only the channel types they ride on. An SFTP or AWS S3 destination can be used once its connection test passes; an email destination right away."
        actions={
          <Button variant="primary" onClick={openAdd}>
            <Plus size={14} /> Add destination
          </Button>
        }
      />
      <Card pad={false} data-tour="destinations-list">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <span className={filterLabel}>Type</span>
              <MultiSelect
                size="sm"
                label="Filter by type"
                noun="types"
                className="min-w-[180px]"
                items={typeIds.map((id) => ({ key: id, label: channelLabel(id), meta: String((rows ?? []).filter((d) => d.channel === id).length) }))}
                selected={typeIds.filter((id) => !typesOff.includes(id))}
                onChange={(on) => setTypesOff(typeIds.filter((id) => !on.includes(id)))}
              />
            </div>
            <label className="block">
              <span className={filterLabel}>Find</span>
              <span className="relative block w-[240px] max-w-full">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Name or address"
                  aria-label="Find a destination"
                  className={`${inputClass} pl-8`}
                />
              </span>
            </label>
          </div>
          {rows !== null && (
            <span className="text-meta text-ink-mute">
              {rows.length} destination{rows.length === 1 ? "" : "s"} · {verified} verified
              {shown.length !== rows.length ? ` · showing ${shown.length}` : ""}
            </span>
          )}
        </div>
        <div className="px-2">
          {/* UIFPE-722: the prototype's column widths. UIFPE-766: no Credential
              column (it stays in the Open sheet); its width goes to Delivers to */}
          <Table
            stagger
            head={[
              { label: "Destination", width: "20%" },
              "Delivers to",
              { label: "Channel", width: "9%" },
              { label: "Status", width: "12%" },
              { label: "", width: "20%" },
            ]}
          >
            {rows === null && <SkeletonRows cols={5} rows={3} />}
            {rows !== null && shown.length === 0 && (
              <ListEmpty colSpan={5} icon={rows.length ? Search : Inbox} title={rows.length ? "No destination matches." : "No destinations yet."}>
                {rows.length
                  ? "Clear the find box or switch a type back on."
                  : "Add one — an SFTP or AWS S3 destination can be used once its connection test passes; an email destination right away."}
              </ListEmpty>
            )}
            {shown.map((d) => {
              const Icon = CHANNEL_ICON[d.channel] || Cloud;
              return (
                <tr key={d.id} className="transition-colors hover:bg-paper-head">
                  <Td>
                    <span className="inline-flex items-center gap-2 font-semibold text-ink">
                      <Icon size={15} className="shrink-0 text-accent" aria-hidden="true" />
                      {d.name}
                    </span>
                    {/* UIFPE-546: who sees this row — private is the owner's own;
                        public rows owned by someone else say whose they are */}
                    {(d.visibility || "public") === "private" ? (
                      <Pill tone="mute" className="ml-1.5">private</Pill>
                    ) : d.owner && !mine(d) ? (
                      <Pill tone="mute" className="ml-1.5">{`shared · ${d.owner}`}</Pill>
                    ) : null}
                  </Td>
                  <Td className="min-w-[230px] break-all font-mono text-[13px] text-ink-soft">{destinationAddress(d)}</Td>
                  <Td>
                    {/* UIFPE-766: the label's own casing — "AWS S3", "SFTP", "Email" */}
                    <Pill tone="accent" className="whitespace-nowrap">{channelLabel(d.channel)}</Pill>
                  </Td>
                  <Td>
                    {/* UIFPE-595: an untested destination says what it is waiting
                        for; the API's status is unchanged, this is how it is said */}
                    {/* UIFPE-722: green with a dot when verified; amber while it waits for its test */}
                    {isVerified(d) ? (
                      <Pill tone="ok" title={statusText(d)}>
                        <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-current" aria-hidden="true" /> verified
                      </Pill>
                    ) : (
                      <Pill tone="warn" title={statusText(d)} className="whitespace-normal">
                        {d.channel === "email" ? "not tested" : "pending connection test"}
                      </Pill>
                    )}
                  </Td>
                  {/* UIFPE-440 feedback: uniform compact buttons, short labels, never wrapping */}
                  <Td className="whitespace-nowrap">
                    <span className="flex items-center justify-end gap-1.5">
                      {/* UIFPE-546: manage buttons only for the owner */}
                      {!mine(d) && (
                        <span className="text-meta text-ink-mute" title={`Only ${d.owner} can change this destination`}>
                          owned by {d.owner}
                        </span>
                      )}
                      {/* UIFPE-595: email is tested too — a real mail through Pearson Notifications */}
                      {mine(d) && ["sftp", "s3", "email"].includes(d.channel) && (
                        <Button
                          size="sm"
                          onClick={() => runTest(d)}
                          busy={testing === d.id}
                          title={
                            d.channel === "sftp"
                              ? "Connect from the delivery IPs, check the folder is writable, pin the server's host key"
                              : d.channel === "s3"
                                ? "Write and remove a tiny test file under the prefix — proves the bucket accepts our deliveries"
                                : "Sends a REAL email to the recipients through Pearson Notifications — the only way to prove the address accepts mail"
                          }
                        >
                          {/* UIFPE-755: one word for every channel once verified */}
                          {testing === d.id
                            ? d.channel === "email" ? "Sending…" : "Testing…"
                            : isVerified(d)
                              ? "Re-test"
                              : testActionLabel(d.channel)}
                        </Button>
                      )}
                      {/* UIFPE-755: Open shows the destination read-only; Edit and the
                          partner bucket policy live inside that popup now */}
                      {mine(d) && (
                        <Button variant="ghost" size="sm" onClick={() => startEdit(d, true)} title="Open this destination">
                          Open
                        </Button>
                      )}
                      {mine(d) && (
                        <Button
                          // UIFPE-630: a red ghost — destructive, but quieter than the test
                          variant="danger"
                          className="border-transparent! bg-transparent! hover:border-transparent! hover:bg-signal-fail/10!"
                          size="sm"
                          onClick={() => runDelete(d)}
                          busy={deleting === d.id}
                          title="Delete this destination (sftp: also deletes its stored credential, 7-day recovery). Blocked while a published export delivers here."
                        >
                          {deleting === d.id ? "Deleting…" : "Delete"}
                        </Button>
                      )}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </Table>
        </div>
        {msg && !open && (
          <div className="px-4 pb-4">
            <MsgNote msg={msg} />
          </div>
        )}
      </Card>

      {/* UIFPE-699: add and edit in a centred popup (the approved design) */}
      {open && (
        <div
          className="motion-fade-in fixed inset-0 z-50 flex items-center justify-center bg-pearson-purple/45 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={editing ? `${viewing ? "Destination" : "Edit destination"} ${editing.name}` : "Add destination"}
          onClick={busy ? undefined : closeForm}
        >
          <div
            className="motion-pop-in flex max-h-[90vh] w-full max-w-[1040px] flex-col overflow-hidden rounded-card border border-line bg-paper-card shadow-[0_24px_48px_-20px_rgb(13_0_77_/_0.55)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 border-b border-line px-6 py-4">
              <div className="flex items-center gap-1.5">
                <h2 className="text-[18px] font-extrabold tracking-tight text-ink">
                  {editing ? `${viewing ? "Destination" : "Edit destination"} · ${editing.name}` : "Add destination"}
                </h2>
                {/* UIFPE-722: the tip follows the popup's job */}
                <InfoTip
                  label="About destinations"
                  text={
                    editing
                      ? viewing
                        ? "What this destination is and where it delivers. Edit changes its name, who can use it and its address; the channel cannot change."
                        : "The name is a label — the stored secret and every export pointing here are unaffected. The channel cannot change: a different channel is a different destination."
                      : "An SFTP password goes to Secrets Manager and Beacon keeps only a reference to it. AWS S3 and email need no credential."
                  }
                />
              </div>
              <Button variant="ghost" size="sm" onClick={closeForm} disabled={busy} aria-label="Close">
                <X size={15} />
              </Button>
            </div>
            <div className="overflow-y-auto px-6 py-5">
              {viewing && editing ? (
                /* UIFPE-755: read-only first; Edit in the footer swaps in the form */
                <DestinationSummary d={editing} mine={mine(editing)} />
              ) : (
                /* UIFPE-599: the same fields the wizard's add-destination dialog renders */
                <DestinationFields
                  form={form}
                  set={set}
                  channels={channels}
                  editing={editing}
                  onShowPolicy={editing?.partner_policy ? () => setPolicyFor(editing) : null}
                  policyNote={policyNote}
                />
              )}
              {msg && (
                <div className="mt-4">
                  <MsgNote msg={msg} />
                </div>
              )}
            </div>
            {viewing && editing ? (
              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper-head px-6 py-3">
                <span className="mr-auto max-w-[60ch] text-meta text-ink-mute">
                  {editing.partner_policy ? "A partner's own bucket: deliveries work once they add the bucket policy." : null}
                </span>
                {editing.partner_policy && (
                  <Button onClick={() => setPolicyFor(editing)} title="The bucket-policy statement the partner must add before deliveries work">
                    Show bucket policy
                  </Button>
                )}
                <Button onClick={closeForm}>Close</Button>
                <Button variant="primary" onClick={() => setViewing(false)} title="Change the name, who can use it or the address (the channel cannot change)">
                  <Pencil size={13} /> Edit
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper-head px-6 py-3">
                <span className="mr-auto max-w-[60ch] text-meta text-ink-mute">
                  {editing
                    ? editing.channel === "sftp"
                      ? "Changing the host, directory or password needs a fresh connection test before exports can use it. A rename does not."
                      : null
                    : createHint(form.channel, "screen") /* UIFPE-636: follows the channel picker */}
                </span>
                <Button onClick={closeForm} disabled={busy}>
                  Cancel
                </Button>
                <Button variant="primary" onClick={submit} busy={busy} disabled={!form.name.trim()}>
                  {editing
                    ? saving ? "Saving…" : "Save changes"
                    : addPhase
                      ? createBusyLabel(addPhase)
                      : createActionLabel(form.channel, "screen")}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* UIFPE-440 feedback: the policy JSON opens as a popup; it stacks above
          the edit popup when opened from "Show bucket policy" */}
      {policyFor && (
        // UIFPE-631: the shared dialog look — header, the JSON in the dark code
        // block, a tinted footer with Close and Copy JSON (primary last)
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="motion-fade-in absolute inset-0 bg-pearson-purple/45" onClick={() => setPolicyFor(null)} />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={`Partner bucket policy for ${policyFor.name}`}
            className="motion-pop-in relative w-full max-w-[640px] overflow-hidden rounded-card border border-line bg-paper-card shadow-[0_24px_48px_-20px_rgb(13_0_77_/_0.55)]"
          >
            <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5">
              <div className="min-w-0">
                <h2 className="text-[18px] font-extrabold tracking-tight text-ink">Partner bucket policy</h2>
                <p className="mt-0.5 text-body text-ink-soft">
                  <b className="text-ink">{policyFor.name}</b> · Send this to the partner; they add it to
                  their bucket policy (S3 → bucket → Permissions → Bucket policy).
                </p>
              </div>
              <button aria-label="Close" onClick={() => setPolicyFor(null)} className="rounded p-0.5 text-ink-mute hover:bg-accent-soft hover:text-ink">
                <X size={15} />
              </button>
            </div>
            <div className="px-5 pb-4">
              <pre className="max-h-72 overflow-auto rounded-card bg-pearson-purple px-4 py-3 font-mono text-[11px] leading-relaxed text-[#e6e3ff]">
                {JSON.stringify(policyFor.partner_policy.statement, null, 2)}
              </pre>
              <p className="mt-2 text-meta leading-relaxed text-ink-soft">{policyFor.partner_policy.note}</p>
            </div>
            <div className="flex justify-end gap-2 border-t border-line bg-paper-head px-5 py-3">
              <Button onClick={() => setPolicyFor(null)}>Close</Button>
              <Button variant="primary" onClick={() => copyPolicy(policyFor)}>
                Copy JSON
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// a page message, with its next step as a button when it carries one (UIFPE-635)
// UIFPE-755: the destination read-only — what the row says, in full, before
// the author decides to Edit. Values only; nothing here calls the API.
function DestinationSummary({ d, mine }) {
  const Icon = CHANNEL_ICON[d.channel] || Cloud;
  const row = (label, value) => (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] items-baseline gap-3 border-b border-line py-2.5 text-body last:border-b-0">
      <dt className="text-meta font-semibold text-ink-mute">{label}</dt>
      <dd className="min-w-0 text-ink">{value}</dd>
    </div>
  );
  const visibility = (d.visibility || "public") === "private" ? "Private — just me" : "Public — everyone";
  return (
    <dl>
      {row(
        "Channel",
        <span className="inline-flex items-center gap-2">
          <Icon size={15} className="shrink-0 text-accent" aria-hidden="true" />
          <Pill tone="accent">{channelLabel(d.channel)}</Pill>
        </span>
      )}
      {row("Name", <span className="font-semibold">{d.name}</span>)}
      {row("Who can use it", d.owner && !mine ? `${visibility} · owned by ${d.owner}` : visibility)}
      {row("Delivers to", <span className="break-all font-mono text-[13px] text-ink-soft">{destinationAddress(d)}</span>)}
      {d.channel === "sftp" && row("Folder", <span className="font-mono text-[13px] text-ink-soft">{d.remote_dir || DASH}</span>)}
      {/* UIFPE-766: email needs no credential — say so instead of "IAM (no secret)" */}
      {row(
        "Credential",
        d.secret_ref ? (
          <Chip>{d.secret_ref}</Chip>
        ) : (
          <span className="text-ink-mute">{d.channel === "email" ? "none needed" : "IAM (no secret)"}</span>
        )
      )}
      {row(
        "Status",
        isVerified(d) ? (
          <Pill tone="ok">
            <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-current" aria-hidden="true" /> verified
          </Pill>
        ) : (
          <span className="inline-flex flex-wrap items-center gap-2">
            <Pill tone="warn">{d.channel === "email" ? "not tested" : "pending connection test"}</Pill>
            <span className="text-meta text-ink-mute">{statusText(d)}</span>
          </span>
        )
      )}
    </dl>
  );
}

function MsgNote({ msg }) {
  return (
    <Note tone={msg.tone}>
      {msg.text}
      {msg.action && (
        <Button size="sm" className="ml-2 align-middle" onClick={msg.action.run}>
          {msg.action.label}
        </Button>
      )}
    </Note>
  );
}

/* -------------------------------------------------------------- Registries */

const SENSITIVITY = { internal: "mute", commercial: "warn", restricted: "fail" };

export function Registries() {
  const [sources, setSources] = useState([]);
  const [formats, setFormats] = useState([]);
  const [channels, setChannels] = useState([]);

  useEffect(() => {
    api.sources().then(setSources).catch(() => {});
    api.formats().then(setFormats).catch(() => {});
    api.channels().then(setChannels).catch(() => {});
  }, []);

  return (
    <section>
      <PageHeader
        title="Registries"
        sub="Everything the wizard offers comes from here. Registering an entry makes it appear in authoring — no release required."
      />
      {/* UIFPE-630: one titled section per registry, stacked, instead of a
          grid of four equal cards */}
      <div className="space-y-7">
        <Section title="Source registry" sub="Gold tables the wizard can read, with their data class and eligible formats.">
          <Card pad={false}>
            <Table head={["Source", "Kind", "Class", "Eligible formats"]}>
              {sources.map((s) => (
                <tr key={s.id} className="transition-colors hover:bg-paper-head">
                  <Td className="font-mono text-meta">{s.id}</Td>
                  <Td>
                    <Pill tone="mute">{s.kind}</Pill>
                  </Td>
                  <Td>
                    <Pill tone={SENSITIVITY[s.sensitivity]}>{s.sensitivity}</Pill>
                  </Td>
                  <Td>
                    <span className="flex flex-wrap gap-1">
                      {(s.eligible_formats || []).map((f) => (
                        <Chip key={f}>{f}</Chip>
                      ))}
                    </span>
                  </Td>
                </tr>
              ))}
            </Table>
          </Card>
        </Section>

        <Section className="border-t border-line pt-6" title="Format registry" sub="Output formats and the options each one takes.">
          <Card pad={false}>
            <Table head={["Format", "Kind", "Options"]}>
              {formats.map((f) => (
                <tr key={f.id} className="transition-colors hover:bg-paper-head">
                  <Td className="font-mono">{f.id}</Td>
                  <Td className="text-meta text-ink-mute">{f.kind}</Td>
                  <Td className="text-meta text-ink-mute">
                    {Object.keys(f.options_schema?.properties || {}).join(", ")}
                  </Td>
                </tr>
              ))}
            </Table>
          </Card>
        </Section>

        <Section className="border-t border-line pt-6" title="Delivery channels" sub="Channel types platform admins make available to destinations.">
          <Card pad={false}>
            <Table head={["Channel", "Adapters", "Credential", "Status"]}>
              {channels.map((c) => (
                <tr key={c.id} className="transition-colors hover:bg-paper-head">
                  <Td className="font-mono">{c.id}</Td>
                  <Td className="text-meta text-ink-mute">{(c.adapters || []).join(", ") || DASH}</Td>
                  <Td className="text-meta text-ink-mute">{c.requires_credential ? "secret" : "IAM"}</Td>
                  <Td>
                    {/* UIFPE-630: the channel's own status, not a run badge
                        (an available channel read "SUCCEEDED") */}
                    <Pill tone={c.status === "available" ? "ok" : "mute"}>{c.status}</Pill>
                  </Td>
                </tr>
              ))}
            </Table>
          </Card>
        </Section>

        <Section
          className="border-t border-line pt-6"
          title="Function allowlist"
          sub="Functions permitted inside mapping expressions. Governed config — changes are audited, no engine release."
        >
          <div className="flex flex-wrap gap-1.5">
            {["concat_ws", "coalesce", "length", "upper", "lower", "trim", "split", "cast", "round"].map((f) => (
              <Chip key={f}>{f}</Chip>
            ))}
            <Chip>+41 more</Chip>
          </div>
        </Section>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- Roles */

export function Roles() {
  const roles = [
    ["Author", "draft.create · config.validate · preview.run · publish.submit · destination.manage", "gg-uif-product-authors", "Product"],
    ["Platform admin", "registry.manage · channel.manage · allowlist.manage · role.manage", "gg-uif-platform-admins", "All"],
    ["Viewer", "run.view", "gg-uif-all-engineering", "All"],
  ];
  return (
    <section>
      <PageHeader
        title="Roles & access"
        sub="Roles are data, not code: admin-editable permission sets, mapped from AD groups, scoped per domain, to be enforced at the API."
      />
      <Card pad={false}>
        <Table head={["Role", "Permissions", "AD group", "Scope"]}>
          {roles.map(([role, perms, group, scope]) => (
            <tr key={role} className="transition-colors hover:bg-paper-head">
              <Td className="font-semibold text-ink">{role}</Td>
              {/* UIFPE-630: one chip per permission, split on the " · " already in the string */}
              <Td>
                <span className="flex flex-wrap gap-1">
                  {perms.split(" · ").map((p) => (
                    <Chip key={p}>{p}</Chip>
                  ))}
                </span>
              </Td>
              <Td className="font-mono text-meta">{group}</Td>
              <Td>{scope}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Note>Enforcement lands with the Entra ID slice — this screen reflects the intended model.</Note>
    </section>
  );
}
