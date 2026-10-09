// Insights (UIFPE-697): /m/beacon/runs as three tabs — Overview, Jobs and
// Scheduled jobs. Everything is counted in the browser from the calls the
// panel already makes (run history, catalog, schedules, destinations); the
// rules are lib/insights.js (vitest). UIFPE-703 moves the counting to server
// aggregates — until then the figures cover the newest RUN_FETCH_LIMIT runs.
// The tab rides in the address (?tab=overview|jobs|scheduled) so the export's
// insight page can link back to the list it came from.
import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, ExternalLink, Inbox, ListChecks, Search } from "lucide-react";
import { api } from "./api.js";
import { describeSchedule, pauseState } from "./schedule.js";
import { CountUp, DASH, EmptyState, Note, PageHeader, Pill, SkeletonRows, Table, TableActions, TableFoot, Td, fmtAbsolute, fmtNum, inputClass } from "./ui.jsx";
import { downloadCsv } from "./lib/csv.js";
import FilterChips from "./FilterChips.jsx";
import RangePicker from "./RangePicker.jsx";
import { DEFAULT_RANGE, rangeFromParams, rangeWindow } from "./lib/overviewRange.js";
import { RUN_FETCH_LIMIT } from "./lib/runSelection.js";
import {
  DAY,
  HOUR,
  axisTicks,
  bucketLabel,
  buckets,
  changeText,
  countOutcomes,
  filesByDestination,
  heatGrid,
  lastFive,
  nextFire,
  outcome,
  peakBucket,
  perExport,
  rankBy,
  runsBetween,
  scheduleOccurrences,
  successPct,
  tableOf,
  dayLabel,
  rangeCaption,
} from "./lib/insights.js";

const MODULE = "/m/beacon";
const TABS = [
  ["overview", "Overview"],
  ["jobs", "Jobs"],
  ["scheduled", "Scheduled jobs"],
];
const CARD = "rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card";
const C = { ok: "#1f8a4c", warn: "#b8960b", bad: "#d0342c", now: "#512eab", prev: "#56e2e1" };
const CHANNEL_WORD = { s3: "AWS S3", sftp: "SFTP", email: "Email" };
const CHANNEL_FILL = { "AWS S3": "#56e2e1", SFTP: "#512eab", Email: "#df41cf" };
const compact = (n) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}K` : String(Math.round(n));

// an export's delivery targets, named from the destinations list
function destinationsOf(entry, destinations) {
  const s = entry?.settings || {};
  const list = s.deliveries?.length ? s.deliveries : s.delivery ? [{ ...s.delivery, destination_id: s.destination_id }] : [];
  if (!list.length) return [{ name: "Managed S3 partner-drop", channel: "AWS S3" }];
  return list.map((t) => {
    const d = t.destination_id && destinations.find((x) => x.id === t.destination_id);
    const channel = CHANNEL_WORD[t.channel] || String(t.channel || "").toUpperCase();
    return { name: d?.name || `${channel} destination`, channel };
  });
}

function CardHead({ title, right }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
      {right}
    </div>
  );
}

function WindowSelect({ value, onChange, options, label }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className={`${inputClass.replace("w-full", "w-auto")} h-8 py-0`}>
      {options.map(([v, l]) => (
        <option key={v} value={v}>{l}</option>
      ))}
    </select>
  );
}

function Legend3() {
  return (
    <div className="mt-3 flex flex-wrap gap-4 text-meta text-ink-soft">
      {[["Success", C.ok], ["Warning", C.warn], ["Failed", C.bad]].map(([l, c]) => (
        <span key={l} className="inline-flex items-center gap-1.5">
          <i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: c }} /> {l}
        </span>
      ))}
    </div>
  );
}

// UIFPE-713: the Activity chart — rows (one colour) or runs stacked by
// outcome, stretched across the card, with gridlines, a time axis and a
// hover per bar. An empty bucket is a faint stub, never a gap.
function ActivityChart({ bs, stacked, window }) {
  const n = bs.length, U = 10, W = n * U, H = 100, bw = n > 24 ? 7.4 : 6.6;
  const vals = bs.map((b) => (stacked ? b.runs.length : b.runs.reduce((a, r) => a + (r.summary?.output_rows || 0), 0)));
  const max = Math.max(1, ...vals);
  const ticks = axisTicks(n);
  return (
    <div className="min-w-0">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-[92px] w-full overflow-visible @2xl:h-[112px]" aria-hidden="true">
        {[1, H / 2].map((y) => (
          <line key={y} x1="0" x2={W} y1={y} y2={y} stroke="var(--color-line)" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
        ))}
        {bs.map((b, i) => {
          const x = i * U + (U - bw) / 2;
          const label = bucketLabel(b.from, window);
          if (!stacked) {
            const h = vals[i] ? Math.max(3, (vals[i] / max) * (H - 4)) : 2;
            return (
              <rect key={i} className="motion-grow-y" x={x} y={H - h} width={bw} height={h} fill={C.now} opacity={vals[i] ? 1 : 0.25}>
                <title>{`${label} · ${compact(vals[i])} rows`}</title>
              </rect>
            );
          }
          const c = countOutcomes(b.runs);
          if (!(c.succeeded + c.warning + c.failed)) {
            return (
              <rect key={i} className="motion-grow-y" x={x} y={H - 2} width={bw} height={2} fill={C.ok} opacity={0.25}>
                <title>{`${label} · no finished runs`}</title>
              </rect>
            );
          }
          let y = H;
          const tip = `${label} · ${c.succeeded} success · ${c.warning} warning · ${c.failed} failed`;
          return [["succeeded", C.ok], ["warning", C.warn], ["failed", C.bad]].map(([k, fill]) => {
            if (!c[k]) return null;
            const h = (c[k] / max) * (H - 4);
            y -= h;
            return (
              <rect key={`${i}-${k}`} className="motion-grow-y" x={x} y={y} width={bw} height={h} fill={fill}>
                <title>{tip}</title>
              </rect>
            );
          });
        })}
      </svg>
      <div className="tnum mt-1.5 flex justify-between gap-2 border-t border-line pt-1.5 text-label text-ink-mute">
        {ticks.map((t, i) => (
          <span key={t}>{i === ticks.length - 1 && window === "24h" ? "now" : bucketLabel(bs[t].from, window)}</span>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- Overview */

function Overview({ runs, catalog, schedules, destinations }) {
  const [act, setAct] = useState("24h");
  const [top, setTop] = useState("7d");
  const [heat, setHeat] = useState("week");
  const now = Date.now();
  const SPAN = { "24h": [24, HOUR], "7d": [7, DAY], "30d": [30, DAY] };
  const entryOf = (job) => catalog.find((c) => c.job_id === job);

  const last24 = runsBetween(runs, now - DAY, now);
  const prev24 = runsBetween(runs, now - 2 * DAY, now - DAY);
  const cur = countOutcomes(last24);
  const prv = countOutcomes(prev24);
  const running = runs.filter((r) => outcome(r) === "running");
  const soon = scheduleOccurrences(schedules, now, now + DAY);
  const channelsOf = (jobs) => [...new Set(jobs.flatMap((j) => destinationsOf(entryOf(j), destinations).map((d) => d.channel)))];

  const [n, span] = SPAN[act];
  const actRuns = runsBetween(runs, now - n * span, now);
  const actBuckets = buckets(actRuns, now - n * span, n, span);
  const rows = actRuns.reduce((a, r) => a + (r.summary?.output_rows || 0), 0);
  const peak = peakBucket(actBuckets);
  const actCount = countOutcomes(actRuns);

  const [tn, tspan] = SPAN[top];
  const len = tn * tspan;
  const topNow = runsBetween(runs, now - len, now);
  const topPrev = runsBetween(runs, now - 2 * len, now - len);
  const nameOf = (t) =>
    (t.destination_id && destinations.find((d) => d.id === t.destination_id)?.name) ||
    (!t.destination_id && t.channel === "s3" ? "Managed S3 partner-drop" : `${CHANNEL_WORD[t.channel] || "Other"} destination`);
  const filesNow = filesByDestination(topNow, nameOf);
  // UIFPE-722: the channel beside each destination's name
  const channelOfName = {};
  for (const r of topNow) for (const t of r?.summary?.targets || []) channelOfName[nameOf(t)] = CHANNEL_WORD[t.channel] || t.channel;
  const filesPrev = filesByDestination(topPrev, nameOf);
  const destNames = Object.keys(filesNow).sort((a, b) => filesNow[b] - filesNow[a]).slice(0, 4);
  const destMax = Math.max(1, ...destNames.map((k) => Math.max(filesNow[k], filesPrev[k] || 0)));

  const bySource = rankBy(topNow, (r) => (entryOf(r.job_id)?.mapping_config?.sources || []).map((s) => tableOf(s.path)));
  const byFormat = rankBy(topNow, (r) => {
    // D17: exactly one format per export
    const e = entryOf(r.job_id);
    const f = e?.format || e?.settings?.format;
    return f ? [String(f).toUpperCase()] : [];
  });

  const weeks = heat === "week" ? 1 : 4;
  const hFrom = now - weeks * 7 * DAY;
  const grid = heatGrid(runsBetween(runs, hFrom, now), weeks);
  const hMax = Math.max(1, ...grid.flat());
  const mix = (t) => {
    const a = [239, 237, 251], b = [81, 46, 171];
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
  };
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const topLabel = { "24h": "24 hours", "7d": "7 days", "30d": "30 days" }[top];

  const Rank = ({ title, list, none }) => {
    const mx = Math.max(1, ...list.map((x) => x.n));
    return (
      <div className={CARD}>
        <CardHead title={title} right={<span className="text-meta text-ink-mute">{topLabel}</span>} />
        {list.length ? (
          list.map((x) => (
            <div key={`${top}-${x.key}`} className="my-2.5 grid grid-cols-[160px_minmax(0,1fr)_72px] items-center gap-3 text-body">
              <span className="truncate">{x.key}</span>
              <span className="flex h-3.5 overflow-hidden rounded-[3px] bg-accent-soft" title={`${x.succeeded} success · ${x.warning} warning · ${x.failed} failed`}>
                <b className="motion-grow-x" style={{ width: `${(x.succeeded / mx) * 100}%`, background: C.ok }} />
                <b className="motion-grow-x" style={{ width: `${(x.warning / mx) * 100}%`, background: C.warn }} />
                <b className="motion-grow-x" style={{ width: `${(x.failed / mx) * 100}%`, background: C.bad }} />
              </span>
              <span className="text-meta text-ink-mute">{x.n} run{x.n === 1 ? "" : "s"}</span>
            </div>
          ))
        ) : (
          <p className="text-body text-ink-mute">{topNow.length ? none : "No finished runs in this window."}</p>
        )}
        <Legend3 />
      </div>
    );
  };

  const Figure = ({ icon, color, title, sub, value, foot, tip }) => (
    <div className="min-w-0 border-l border-line px-5 first:border-l-0 first:pl-0" title={tip}>
      <div className="flex items-center gap-2 text-[15px] font-bold text-ink">
        <span className="grid h-[18px] w-[18px] place-items-center rounded-full text-[10px] font-extrabold text-white" style={{ background: color }}>{icon}</span>
        {title}
      </div>
      <div className="mt-0.5 text-meta text-ink-mute">{sub}</div>
      <div className="tnum my-1.5 text-[32px] font-extrabold leading-none text-accent">{typeof value === "number" ? <CountUp value={value} /> : value}</div>
      <div className="text-meta text-ink-mute">{foot}</div>
    </div>
  );

  return (
    <div className="space-y-5">
      <div className={CARD}>
        <CardHead title="Jobs" />
        <div className="grid gap-y-4 sm:grid-cols-2 xl:grid-cols-5">
          <Figure icon="✓" color={C.ok} title="Success" sub="24 hours" value={cur.succeeded} foot={changeText(cur.succeeded, prv.succeeded)} />
          <Figure icon="!" color={C.warn} title="Warning" sub="24 hours" tip="A warning is a run that succeeded but rejected some rows." value={cur.warning} foot={changeText(cur.warning, prv.warning)} />
          <Figure icon="✕" color={C.bad} title="Failed" sub="24 hours" value={cur.failed} foot={changeText(cur.failed, prv.failed, "abs")} />
          <Figure icon="↻" color="#139aa5" title="Running" sub="Currently" value={running.length}
            foot={running.length ? `to ${channelsOf(running.map((r) => r.job_id)).join(", ")}` : "nothing running"} />
          <Figure icon="▦" color={C.now} title="Scheduled" sub="Next 24 hours" value={soon.length}
            foot={soon.length ? `to ${channelsOf(soon.map((o) => o.job_id)).join(", ")}` : "nothing due"} />
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        {/* UIFPE-713: the charts use the card's width — beside the number on a
            wide card, under it on a narrow one (container query) */}
        <div className={`${CARD} @container`}>
          <CardHead title="Activity" right={<WindowSelect label="Activity window" value={act} onChange={setAct} options={[["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]]} />} />
          <div className="grid items-center gap-2.5 py-3.5 @2xl:grid-cols-[minmax(150px,210px)_minmax(0,1fr)] @2xl:gap-7">
            <div>
              <div className="text-body text-ink-soft">Rows processed</div>
              <div className="tnum mt-0.5 text-[32px] font-extrabold leading-none text-ink">{compact(rows)}</div>
              <div className="mt-1.5 text-meta text-ink-soft">
                {peak.index >= 0 ? (
                  <>
                    Peak <b className="text-ink">{compact(peak.rows)}</b> · {bucketLabel(actBuckets[peak.index].from, act)}
                    <br />≈ {compact(Math.round(rows / actBuckets.length))} per {act === "24h" ? "hour" : "day"}
                  </>
                ) : (
                  "Nothing processed in this window"
                )}
              </div>
            </div>
            <ActivityChart bs={actBuckets} window={act} />
          </div>
          <div className="grid items-center gap-2.5 border-t border-line py-3.5 @2xl:grid-cols-[minmax(150px,210px)_minmax(0,1fr)] @2xl:gap-7">
            <div>
              <div className="text-body text-ink-soft">Runs</div>
              <div className="tnum mt-0.5 text-[32px] font-extrabold leading-none text-ink">{actRuns.length}</div>
              <div className="mt-1.5 text-meta text-ink-soft">
                <b style={{ color: C.ok }}>{actCount.succeeded}</b> success · <b style={{ color: C.warn }}>{actCount.warning}</b> warning ·{" "}
                <b style={{ color: C.bad }}>{actCount.failed}</b> failed
              </div>
            </div>
            <ActivityChart bs={actBuckets} window={act} stacked />
          </div>
          <Legend3 />
        </div>

        <div className={CARD}>
          <CardHead title="Top destinations" right={<WindowSelect label="Destinations window" value={top} onChange={setTop} options={[["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]]} />} />
          {destNames.length ? (
            destNames.map((k) => (
              <div key={k} className="my-3">
                <div className="mb-1.5 text-body font-semibold text-ink">
                  {k}
                  {channelOfName[k] && !k.startsWith(channelOfName[k]) && <span className="font-normal text-ink-mute"> · {channelOfName[k]}</span>}
                </div>
                {[[filesNow[k], C.now], [filesPrev[k] || 0, C.prev]].map(([v, c], i) => (
                  <div key={`${top}-${i}`} className="my-1 flex items-center gap-2.5">
                    <b className="motion-grow-x block h-3.5 rounded-[3px]" style={{ width: `${(v / destMax) * 85}%`, background: c }} />
                    <span className="tnum text-meta text-ink-soft">{fmtNum(v)}</span>
                  </div>
                ))}
              </div>
            ))
          ) : (
            <p className="text-body text-ink-mute">Nothing delivered in this window.</p>
          )}
          <div className="mt-3 flex flex-wrap gap-4 text-meta text-ink-soft">
            <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: C.now }} /> This period (files delivered)</span>
            <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: C.prev }} /> Previous period</span>
          </div>
        </div>

        <Rank title="Top source tables by runs" list={bySource} none="These runs name no source tables." />
        <Rank title="Top formats by runs" list={byFormat} none="These exports record no format." />
      </div>

      <div className={CARD}>
        <CardHead title="Data volume" right={<WindowSelect label="Data volume window" value={heat} onChange={setHeat} options={[["week", "1 week actual"], ["avg", "4 weeks average"]]} />} />
        <div className="grid grid-cols-[90px_minmax(0,1fr)] gap-4 lg:grid-cols-[90px_minmax(0,1fr)_150px]">
          <div className="grid auto-rows-[24px] gap-y-1">
            {DAYS.map((d) => (
              <div key={d} className="text-right text-body leading-6 text-ink-soft">{d}</div>
            ))}
          </div>
          <div>
            <div className="grid auto-rows-[24px] grid-cols-[repeat(24,minmax(0,1fr))] gap-1">
              {grid.map((row, di) =>
                row.map((v, hi) => (
                  <i
                    key={`${heat}-${di}-${hi}`}
                    className="motion-cell-in block rounded-[4px]"
                    style={{ "--row": di, background: v ? mix(Math.min(1, 0.18 + (v / hMax) * 0.82)) : mix(0) }}
                    title={`${DAYS[di]} ${String(hi).padStart(2, "0")}:00 · ${compact(v)} rows`}
                  />
                ))
              )}
            </div>
            <div className="mt-2 flex justify-between text-meta text-ink-mute">
              {["00:00", "06:00", "12:00", "18:00", "24:00"].map((t) => <span key={t}>{t}</span>)}
            </div>
            {/* UIFPE-722: which days the grid covers */}
            <div className="mt-1 text-center text-meta text-ink-mute">
              {dayLabel(hFrom)} – {dayLabel(now)} · {weeks === 1 ? "one week, actual rows" : "average per week over 4 weeks"}
            </div>
          </div>
          <div className="hidden rounded-card border border-line p-3.5 lg:block">
            <div className="text-body font-semibold text-ink">Rows processed</div>
            <div className="mt-2.5 flex gap-2.5">
              <i className="block h-24 w-3.5 rounded-[4px]" style={{ background: "linear-gradient(#efedfb,#512eab)" }} />
              <div className="flex flex-col justify-between text-meta text-ink-soft">
                <span>0</span>
                <span>{compact(hMax / 2)}</span>
                <span>{compact(hMax)}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------- Jobs */

const DOT = {
  // UIFPE-716: the prototype's run dots
  succeeded: ["bg-dot-ok text-white", "✓"],
  warning: ["bg-pearson-amber text-ink", "!"],
  failed: ["bg-dot-fail text-white", "✕"],
  skipped: ["bg-dot-mute text-white", "–"],
  stopped: ["bg-dot-mute text-white", "■"],
};
const RING = { succeeded: "#1f9d55", warning: C.warn, failed: "#e0453a", skipped: "#b9b5cc", stopped: "#b9b5cc" };
function RunDot({ run, last }) {
  const size = last ? "h-[26px] w-[26px] text-[13px] outline outline-2 outline-offset-2" : "h-5 w-5 text-[11px]";
  if (!run) return <span className={`inline-block rounded-full border-[1.5px] border-dashed border-line ${last ? "h-[26px] w-[26px]" : "h-5 w-5"}`} title="no run" />;
  const o = outcome(run);
  const tip = `${o} · ${fmtAbsolute(run.recorded_at)} · ${run.batch_id}${run.error ? ` — ${run.error}` : ""}`;
  if (o === "running")
    return <span className={`inline-block animate-spin rounded-full border-[3px] border-turq/40 border-t-[#139aa5] ${last ? "h-[26px] w-[26px] outline outline-2 outline-offset-2 outline-[#139aa5]" : "h-5 w-5"}`} title={tip} />;
  const [cls, glyph] = DOT[o] || ["bg-dot-mute text-white", "?"];
  return (
    <span className={`inline-grid place-items-center rounded-full font-extrabold ${size} ${cls}`} style={last ? { outlineColor: RING[o] || "#b9b5cc" } : undefined} title={tip}>
      {glyph}
    </span>
  );
}

const STATUS_KEYS = ["succeeded", "warning", "failed", "running", "skipped", "stopped", "none"];
const PAGE = 10;

function Jobs({ runs, catalog, schedules, destinations, range, loaded, onlyLast = null, onClearUrl = () => {} }) {
  const [query, setQuery] = useState("");
  // UIFPE-754: Home's Success rate tile arrives with ?last=succeeded — every
  // other status starts turned off, exactly as if the author had picked it
  const [lastOff, setLastOff] = useState(() => (onlyLast ? STATUS_KEYS.filter((k) => k !== onlyLast) : []));
  const [trigOff, setTrigOff] = useState([]);
  const [page, setPage] = useState(0);
  const [per, setPer] = useState(PAGE); // UIFPE-759: Items per page
  const win = rangeWindow(range);
  const since = win.error ? 0 : Date.parse(win.since);
  const until = win.error ? Date.now() : Date.parse(win.until);
  const sched = Object.fromEntries((schedules || []).map((s) => [s.job_id, s]));

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return perExport(catalog, runsBetween(runs, since, until))
      .filter((x) => {
        const key = x.last ? outcome(x.last) : "none";
        if (lastOff.includes(key)) return false;
        if (trigOff.includes(sched[x.entry.job_id] ? "scheduled" : "manual")) return false;
        if (q && !(`${x.entry.job_id}`.toLowerCase().includes(q) || (x.entry.name || "").toLowerCase().includes(q))) return false;
        return true;
      })
      .sort((a, b) => (b.last ? Date.parse(b.last.recorded_at) : 0) - (a.last ? Date.parse(a.last.recorded_at) : 0));
  }, [catalog, runs, since, until, query, lastOff, trigOff, schedules]);

  const pages = Math.max(1, Math.ceil(rows.length / per));
  const safe = Math.min(page, pages - 1);
  const shown = rows.slice(safe * per, safe * per + per);
  const off = (keys, set) => (on) => {
    set(keys.filter((k) => !on.includes(k)));
    setPage(0);
  };

  // UIFPE-759: the filtered rows, every page, as the table reads them
  const exportCsv = () => {
    const head = ["Export", "Export id", "Trigger", "Schedule", "Destination", "Last 5 runs", "Success %", "Next run"];
    const body = rows.map((x) => {
      const e = x.entry;
      const s = sched[e.job_id];
      const pz = s ? pauseState(s) : null;
      const next = s && s.enabled !== false ? nextFire(s) : null;
      const pct = successPct(x.runs);
      return [
        e.name ?? e.job_id,
        e.job_id,
        s ? "scheduled" : "manual",
        s ? (s.expression ? describeSchedule(s.expression, s.timezone) : "On a schedule") : "Run now only",
        destinationsOf(e, destinations).map((d) => d.name).join("; "),
        lastFive(x.runs).map(outcome).join(" "),
        pct === null ? "" : pct,
        pz?.kind === "indefinite" ? "paused" : next ? next.toISOString() : "",
      ];
    });
    const tag = range?.kind === "custom" ? `${range.from}_${range.to}` : range?.id || "7d";
    downloadCsv(`beacon-jobs-${tag}.csv`, [head, ...body]);
  };
  const clearAll = () => {
    setQuery("");
    setLastOff([]);
    setTrigOff([]);
    setPage(0);
    onClearUrl(); // UIFPE-766: drop ?last= too, or a reload brings the chip back
  };

  return (
    <div className={`${CARD} p-0`}>
      {/* UIFPE-762: Find, then one chip per active filter and "+ Filter" — the
          filters and what they hide are unchanged */}
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-3 py-2.5">
        <span className="relative block w-[240px] max-w-full">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            placeholder="Find an export…"
            aria-label="Find an export"
            className={`${inputClass} pl-8`}
          />
        </span>
        <FilterChips
          filters={[
            {
              key: "last", name: "Last run", kind: "multi", noun: "statuses",
              items: STATUS_KEYS.map((k) => ({ key: k, label: k === "none" ? "no runs in range" : k })),
              selected: STATUS_KEYS.filter((k) => !lastOff.includes(k)),
              onChange: off(STATUS_KEYS, setLastOff),
              start: ["failed"],
            },
            {
              key: "trigger", name: "Trigger", kind: "multi", noun: "triggers",
              items: [{ key: "scheduled", label: "scheduled" }, { key: "manual", label: "manual" }],
              selected: ["scheduled", "manual"].filter((k) => !trigOff.includes(k)),
              onChange: off(["scheduled", "manual"], setTrigOff),
              start: ["scheduled"],
            },
          ]}
        />
        {/* UIFPE-759: Export and Clear all; the pager moved to the table's foot */}
        <TableActions onExport={exportCsv} exportDisabled={!rows.length} onClear={clearAll} className="pb-0" />
      </div>
      <div className="px-3">
        <Table stagger head={["Export", "Trigger", "Destination", { label: "Last 5 runs", title: "Oldest on the left, the latest run ringed on the right" }, { label: "Success", align: "right" }, "Next run", ""]}>
          {!loaded && <SkeletonRows cols={7} rows={5} />}
          {loaded && shown.length === 0 && (
            <EmptyState colSpan={7} icon={catalog.length ? Search : Inbox} title={catalog.length ? "No exports match those filters." : "No exports published yet."}>
              {catalog.length ? "Clear a filter or widen the time range." : "Publish one with New Export."}
            </EmptyState>
          )}
          {shown.map((x) => {
            const e = x.entry;
            const s = sched[e.job_id];
            const five = lastFive(x.runs);
            const pad = 5 - five.length;
            const pct = successPct(x.runs);
            const dests = destinationsOf(e, destinations);
            const running = x.last && outcome(x.last) === "running";
            const pz = s ? pauseState(s) : null;
            const next = s && s.enabled !== false ? nextFire(s) : null;
            return (
              <tr key={e.job_id} className="transition-colors hover:bg-paper-head">
                <Td>
                  <Link to={`${MODULE}/exports/${e.job_id}`} className="font-semibold text-ink hover:text-accent hover:underline">{e.name ?? e.job_id}</Link>
                  <div className="text-meta text-ink-mute">{e.job_id}</div>
                </Td>
                <Td>
                  <Pill tone="mute">{s ? "scheduled" : "manual"}</Pill>
                  <div className="mt-0.5 text-meta text-ink-mute">{s ? (s.expression ? describeSchedule(s.expression, s.timezone) : "On a schedule") : "Run now only"}</div>
                </Td>
                <Td className="text-ink-soft" title={dests.map((d) => d.name).join(", ")}>
                  {dests[0]?.name}
                  {dests.length > 1 && <b className="ml-1 text-ink-mute">+{dests.length - 1}</b>}
                </Td>
                <Td className="whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    {Array.from({ length: pad }, (_, i) => <RunDot key={`p${i}`} last={false} />)}
                    {five.map((r, i) => <RunDot key={r.batch_id} run={r} last={i === five.length - 1} />)}
                  </span>
                  {running && (
                    <div className="mt-1.5">
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-turq/20 px-2 py-0.5 text-label font-bold text-[#0e7c85]">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#139aa5]" /> In progress · {Math.max(1, Math.round((Date.now() - Date.parse(x.last.started_at || x.last.recorded_at)) / 6e4))} min
                      </span>
                    </div>
                  )}
                  {!x.runs.length && <div className="mt-1 text-meta text-ink-mute">no runs in range</div>}
                </Td>
                <Td
                  className="tnum text-right"
                  title={(() => {
                    // UIFPE-722: the numbers behind the percentage
                    const c = countOutcomes(x.runs);
                    const fin = c.succeeded + c.warning + c.failed;
                    return fin ? `${c.succeeded + c.warning} of ${fin} finished run${fin === 1 ? "" : "s"}` : "no finished runs in range";
                  })()}
                >
                  {pct === null ? DASH : `${pct}%`}
                </Td>
                <Td className="text-meta text-ink-soft">
                  {pz?.kind === "indefinite" ? <Pill tone="warn">paused</Pill> : next ? fmtAbsolute(next.toISOString()) : DASH}
                  {pz?.until && <div className="mt-0.5"><Pill tone="warn">{pz.kind === "active" ? "paused" : "pause"} to {pz.until}</Pill></div>}
                </Td>
                <Td className="whitespace-nowrap text-right">
                  {/* UIFPE-711: the open icon alone — "Open insight" is its label */}
                  <Link
                    to={`${MODULE}/insights/${e.job_id}`}
                    aria-label={`Open insight for ${e.name ?? e.job_id}`}
                    title="Open insight"
                    className="inline-grid h-8 w-8 place-items-center rounded-full text-accent transition-colors hover:bg-accent-soft"
                  >
                    <ExternalLink size={16} strokeWidth={2} aria-hidden="true" />
                  </Link>
                </Td>
              </tr>
            );
          })}
        </Table>
      </div>
      <TableFoot total={rows.length} page={safe} setPage={setPage} per={per} setPer={setPer} noun="exports" />
    </div>
  );
}

/* ---------------------------------------------------------- Scheduled jobs */

function Scheduled({ catalog, schedules, destinations }) {
  const [win, setWin] = useState("24h");
  const [asc, setAsc] = useState(true);
  const [page, setPage] = useState(0);
  const [per, setPer] = useState(25);
  const now = Date.now();
  const len = win === "24h" ? DAY : 7 * DAY;
  const entryOf = (job) => catalog.find((c) => c.job_id === job);
  const occ = scheduleOccurrences(schedules, now, now + len).filter((o) => entryOf(o.job_id));
  if (!asc) occ.reverse();
  const paused = (schedules || []).filter((s) => pauseState(s));

  const n = win === "24h" ? 24 : 7;
  const step = win === "24h" ? HOUR : DAY;
  const start = win === "24h" ? Math.ceil(now / HOUR) * HOUR : new Date(new Date(now).setHours(24, 0, 0, 0)).getTime();
  const slots = Array.from({ length: n }, (_, i) => ({ from: start + (i - 1) * step, to: start + i * step, c: {} }));
  for (const o of occ) {
    const s = slots.find((x) => o.at > x.from && o.at <= x.to);
    const ch = destinationsOf(entryOf(o.job_id), destinations)[0]?.channel || "Other";
    if (s) s.c[ch] = (s.c[ch] || 0) + 1;
  }
  const max = Math.max(2, ...slots.map((s) => Object.values(s.c).reduce((a, b) => a + b, 0)));
  const W = 1000, H = 180, L = 30, B = 34, T = 8, iw = W - L - 10, ih = H - T - B, slot = iw / n;
  const y = (v) => T + ih - (v / max) * ih;

  const pages = Math.max(1, Math.ceil(occ.length / per));
  const safe = Math.min(page, pages - 1);
  const rows = occ.slice(safe * per, safe * per + per);

  const exportCsv = () => {
    const lines = [["Export", "Export id", "Schedule", "Timezone", "Scheduled time", "Destination"]].concat(
      occ.map((o) => {
        const e = entryOf(o.job_id);
        return [e?.name ?? o.job_id, o.job_id, describeSchedule(o.schedule.expression), o.schedule.timezone, new Date(o.at).toISOString(), destinationsOf(e, destinations).map((d) => d.name).join("; ")];
      })
    );
    // UIFPE-759: the shared writer (lib/csv.js) — same quoting, plus the formula guard
    downloadCsv(`beacon-scheduled-jobs-${win}.csv`, lines);
  };

  return (
    <div className="space-y-5">
      <div className={CARD}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-[18px] font-extrabold text-ink"><ListChecks size={16} className="text-accent" /> Scheduled jobs</h2>
          <span className="text-meta text-ink-mute">Based on data available as of {fmtAbsolute(new Date(now).toISOString())}</span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="text-meta font-bold text-ink">Filter</span>
          <WindowSelect label="Scheduled time" value={win} onChange={(v) => { setWin(v); setPage(0); }} options={[["24h", "Scheduled time: Next 24 hours"], ["7d", "Scheduled time: Next 7 days"]]} />
          {paused.length > 0 && (
            // UIFPE-722: which exports, and their pause windows, on hover
            <Pill
              tone="warn"
              className="cursor-help"
              title={paused
                .map((s) => {
                  const p = pauseState(s);
                  const name = entryOf(s.job_id)?.name ?? s.job_id;
                  const when = p?.kind === "active" ? `paused until ${p.until}` : p?.kind === "upcoming" ? `pauses ${p.from} → ${p.until}` : "paused until resumed";
                  return `${name} — ${when}`;
                })
                .join("\n")}
            >
              {paused.length} paused export{paused.length === 1 ? "" : "s"} not shown
            </Pill>
          )}
          <button type="button" className="ml-auto text-meta font-bold text-accent hover:underline" onClick={() => { setWin("24h"); setAsc(true); setPage(0); }}>Clear all</button>
        </div>
        <div className="mt-2 text-center text-meta font-bold text-ink">By destination type</div>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label="Scheduled runs by destination type">
          {[0, Math.ceil(max / 2), max].map((v) => (
            <g key={v}>
              <line x1={L} x2={W - 10} y1={y(v)} y2={y(v)} stroke="var(--color-line)" />
              <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--color-ink-mute)">{v}</text>
            </g>
          ))}
          {slots.map((s, i) => {
            const keys = Object.keys(s.c);
            const bw = Math.min(14, slot / 4);
            const cx = L + i * slot + slot / 2;
            const d = new Date(win === "24h" ? s.to : s.from);
            const label = win === "24h" ? `${String(d.getHours()).padStart(2, "0")}:00` : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
            return (
              <g key={i}>
                {keys.map((k, j) => (
                  <rect key={k} x={cx - (keys.length * bw + (keys.length - 1) * 2) / 2 + j * (bw + 2)} y={y(s.c[k])} width={bw} height={y(0) - y(s.c[k])} rx="2" fill={CHANNEL_FILL[k] || "#999"}>
                    <title>{`${k} · ${s.c[k]}`}</title>
                  </rect>
                ))}
                {(win !== "24h" || i % 2 === 0) && <text x={cx} y={H - 12} textAnchor="middle" fontSize="11" fill="var(--color-ink-mute)">{label}</text>}
                {/* UIFPE-722: a day marker where the 24 hours cross midnight */}
                {win === "24h" && d.getHours() === 0 && (
                  <>
                    <line x1={cx} x2={cx} y1={T} y2={y(0)} stroke="var(--color-pearson-light-purple)" strokeDasharray="3 3" />
                    <text x={cx + 4} y={T + 10} fontSize="11" fontWeight="700" fill="var(--color-ink)">
                      {dayLabel(d.getTime())}
                    </text>
                  </>
                )}
              </g>
            );
          })}
        </svg>
        <div className="flex flex-wrap justify-center gap-4 text-meta text-ink-soft">
          {Object.entries(CHANNEL_FILL).map(([k, c]) => (
            <span key={k} className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: c }} /> {k}</span>
          ))}
        </div>
      </div>

      <div className={CARD}>
        <CardHead
          title={<span className="flex items-center gap-2"><ListChecks size={16} className="text-accent" /> Jobs</span>}
          right={
            <button type="button" onClick={exportCsv} disabled={!occ.length} className="inline-flex items-center gap-1.5 rounded-full border border-accent px-3.5 py-1.5 text-meta font-bold text-accent hover:bg-accent-soft disabled:opacity-40">
              <Download size={13} /> Export
            </button>
          }
        />
        <Table
          head={[
            "Export",
            "Schedule",
            {
              // UIFPE-722: the header sorts, as in the prototype
              label: (
                <button
                  type="button"
                  onClick={() => setAsc((a) => !a)}
                  aria-label={`Sort by scheduled time, ${asc ? "latest" : "soonest"} first`}
                  className="font-bold text-ink hover:text-accent"
                >
                  Scheduled time {asc ? "↑" : "↓"}
                </button>
              ),
            },
            "Destination",
          ]}
        >
          {rows.length === 0 && <EmptyState colSpan={4} title="Nothing is scheduled in this window." />}
          {rows.map((o) => {
            const e = entryOf(o.job_id);
            const dests = destinationsOf(e, destinations);
            return (
              <tr key={`${o.job_id}-${o.at}`} className="transition-colors hover:bg-paper-head">
                <Td>
                  <Link to={`${MODULE}/exports/${o.job_id}`} className="font-semibold text-accent hover:underline">{e?.name ?? o.job_id}</Link>
                  <div className="text-meta text-ink-mute">{o.job_id}</div>
                </Td>
                <Td className="text-ink-soft">{describeSchedule(o.schedule.expression)} <span className="text-meta text-ink-mute">({o.schedule.timezone})</span></Td>
                <Td className="text-ink-soft">{fmtAbsolute(new Date(o.at).toISOString())}</Td>
                <Td className="text-ink-soft">{dests[0]?.name} <span className="text-meta text-ink-mute">· {dests[0]?.channel}</span></Td>
              </tr>
            );
          })}
        </Table>
        {/* UIFPE-759: the shared foot (ui.jsx) — Jobs and Exports use the same one */}
        <div className="-mx-3 mt-1">
          <TableFoot total={occ.length} page={safe} setPage={setPage} per={per} setPer={setPer} noun="items" />
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- page */

export default function Insights() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(([id]) => id === params.get("tab")) ? params.get("tab") : "overview";
  const [data, setData] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [destinations, setDestinations] = useState([]);
  // UIFPE-754: a range in the URL (Home's tiles) wins over the default
  const [range, setRange] = useState(() => rangeFromParams(params) || DEFAULT_RANGE);
  const onlyLast = STATUS_KEYS.includes(params.get("last")) ? params.get("last") : null;
  // UIFPE-766: Jobs' Clear all removes Home's ?last= deep link; the tab and the range stay
  const dropLast = () => {
    if (!params.has("last")) return;
    const next = new URLSearchParams(params);
    next.delete("last");
    setParams(next, { replace: true });
  };

  useEffect(() => {
    // 200 = the endpoint's cap (UIFPE-442); UIFPE-703 brings server aggregates
    api.runHistory(null, RUN_FETCH_LIMIT).then(setData).catch(() => setData({ enabled: false, runs: [], reason: "API unreachable" }));
    api.catalog().then(setCatalog).catch(() => setCatalog([]));
    api.listSchedules().then((r) => setSchedules(r.schedules || [])).catch(() => setSchedules([]));
    api.destinations().then((d) => setDestinations(d || [])).catch(() => setDestinations([]));
  }, []);
  const runs = data?.runs ?? [];

  return (
    <section>
      <PageHeader
        title="Insights"
        sub="Batch history across all exports — successes and failures, written by the pipeline itself. Overview sums them up, Jobs shows each export's latest runs, and Scheduled jobs lists what the schedules will start next."
        actions={
          tab === "jobs" ? (
            <div className="flex flex-col items-end gap-1">
              <RangePicker value={range} onChange={setRange} />
              {/* UIFPE-722: the range said in days, runs and exports */}
              {(() => {
                const w = rangeWindow(range);
                if (w.error) return null;
                const from = Date.parse(w.since);
                const to = Date.parse(w.until);
                return <span className="text-meta text-ink-mute">{rangeCaption(from, to, runsBetween(runs, from, to))}</span>;
              })()}
            </div>
          ) : null
        }
      />
      {data && data.enabled === false && <Note>{data.reason}</Note>}
      <div role="tablist" data-tour="runs-list" className="mb-5 flex gap-1 border-b border-line">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setParams(id === "overview" ? {} : { tab: id }, { replace: true })}
            className={`-mb-px border-b-[3px] px-4 py-2.5 text-[15px] font-semibold transition-colors ${
              tab === id ? "border-accent text-accent" : "border-transparent text-ink-soft hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview runs={runs} catalog={catalog} schedules={schedules} destinations={destinations} />}
      {tab === "jobs" && (
        <Jobs runs={runs} catalog={catalog} schedules={schedules} destinations={destinations} range={range} loaded={data !== null} onlyLast={onlyLast} onClearUrl={dropLast} />
      )}
      {tab === "scheduled" && <Scheduled catalog={catalog} schedules={schedules} destinations={destinations} />}
    </section>
  );
}
