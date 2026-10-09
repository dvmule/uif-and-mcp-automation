// Shared primitives, matching the UIF platform shell's visual language:
// paper cards with light-purple hairlines, soft sentence-case status pills
// (UIFPE-667), tabular numerics, em-dash for nulls, generous empty states.
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Download, FunnelX, Info } from "lucide-react";
import { apiActivity } from "./api.js";

export const DASH = "—"; // em dash: the platform's universal "no value"

/* ---------------------------------------------- loading states (UIFPE-418)
   A page that is loading must never look like a page that is empty or stuck.
   Three tools: TopLoader (global, automatic, catches every API call),
   BeaconSweep (on-brand first-load indicator), SkeletonRows (table shimmer).
*/

export function Spinner({ className = "" }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent ${className}`}
    />
  );
}

// Thin indeterminate bar pinned under the shell header while ANY API call is
// in flight. Appears only after a 200 ms grace so warm sub-100 ms calls never
// flicker; rendered inside a zero-height wrapper so nothing shifts.
export function TopLoader() {
  const busy = React.useSyncExternalStore(apiActivity.subscribe, apiActivity.isBusy);
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => {
    if (!busy) {
      setVisible(false);
      return undefined;
    }
    const t = setTimeout(() => setVisible(true), 200);
    return () => clearTimeout(t);
  }, [busy]);
  if (!visible) return null;
  return (
    <div className="relative h-0" role="progressbar" aria-label="Loading">
      <div className="absolute inset-x-0 top-0 z-20 h-[2.5px] overflow-hidden">
        <div className="toploader-bar absolute top-0 h-full rounded-full bg-accent" />
      </div>
    </div>
  );
}

// Beacon is named after a lighthouse — so the loader is one: a light beam
// sweeping around the lamp, with a rotating line of copy. Pure CSS.
const SWEEP_LINES = [
  "Lighting the beacon…",
  "Scanning the gold layer…",
  "Waking the Lambda…",
  "Charting the delivery route…",
];

export function BeaconSweep({ label }) {
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (label) return undefined;
    const t = setInterval(() => setTick((n) => n + 1), 1800);
    return () => clearInterval(t);
  }, [label]);
  return (
    <div className="flex flex-col items-center gap-2.5 py-10" role="status" aria-label="Loading">
      <span className="relative inline-block h-10 w-10">
        <span
          className="beacon-sweep absolute inset-0 rounded-full"
          style={{
            background:
              "conic-gradient(from 0deg, var(--color-accent), color-mix(in srgb, var(--color-accent) 10%, transparent) 80deg, transparent 130deg)",
          }}
        />
        <span className="absolute inset-[14px] rounded-full bg-accent" />
      </span>
      <span className="text-[11.5px] text-ink-mute">
        {label ?? SWEEP_LINES[tick % SWEEP_LINES.length]}
      </span>
    </div>
  );
}

// Shimmer placeholder rows for a table whose FIRST load is in flight. Widths
// vary deterministically so the block reads as content, not stripes.
export function SkeletonRows({ cols, rows = 4 }) {
  return Array.from({ length: rows }, (_, r) => (
    <tr key={`skeleton-${r}`} aria-hidden className="motion-fade-in">
      {Array.from({ length: cols }, (__, c) => (
        <Td key={c}>
          <span
            className="block h-3 animate-pulse rounded bg-paper-line/60"
            style={{ width: `${55 + ((r * 7 + c * 13) % 40)}%` }}
          />
        </Td>
      ))}
    </tr>
  ));
}

export function Card({ title, right, className = "", children, pad = true, ...rest }) {
  // UIFPE-622: a bare table card (no header, no padding) clips its children so
  // the table's header surface follows the rounded corners; cards with a
  // header or padding stay unclipped because popovers may open inside them
  const flush = !title && !right && !pad;
  const head = title || right;
  return (
    // UIFPE-716: the prototype card — near-white edge, the shadow draws it;
    // 22/26px padding and an 18px heading with no rule under it
    <section
      className={`rounded-card border border-white/70 bg-paper-card shadow-card ${flush ? "overflow-hidden" : ""} ${className}`}
      {...rest}
    >
      {head && (
        <header className={`flex items-center justify-between gap-3 px-[26px] pt-[22px] ${pad ? "" : "pb-3"}`}>
          <h3 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h3>
          {right}
        </header>
      )}
      <div className={pad ? (head ? "px-[26px] pb-[22px] pt-3" : "px-[26px] py-[22px]") : ""}>{children}</div>
    </section>
  );
}

const DOT = {
  SUCCEEDED: "bg-turq",
  FAILED: "bg-fuchsia-brand",
  WARNING: "bg-pearson-amber",
  RUNNING: "bg-signal-live pulse-ring",
  STARTING: "bg-signal-live pulse-ring",
  STOPPED: "bg-pearson-amber", // operator stop (UIFPE-203) — neutral, not a failure
  SKIPPED: "bg-pearson-amber-tint", // scheduler skip/miss (UIFPE-206) — also neutral
  MISSED: "bg-pearson-amber-tint",
  DISABLED: "bg-paper-line",
  PUBLISHED: "bg-turq",
  SUBMITTED: "bg-pearson-amber",
  DRAFT: "bg-paper-line",
  REJECTED: "bg-fuchsia-brand",
};

/** How many DOWNLOADABLE files a run delivered (UIFPE-201). The pipeline
 *  also delivers the manifest (checksums — partner-facing, by design), but
 *  the Files download list deliberately hides it (UIFPE-202) — so a raw
 *  `delivered` count of 2 next to a list of 1 file reads as a bug. Counting
 *  the delivery steps minus the manifest also fixes historical rows. */
export function deliveredFiles(summary) {
  const steps = summary?.delivery;
  if (Array.isArray(steps)) {
    const named = steps.filter((s) => s.file || s.name);
    if (named.length) {
      return named.filter(
        (s) => !/\.manifest\.json$/.test(s.file || s.name)
      ).length;
    }
  }
  return summary?.delivered ?? null;
}

// UIFPE-622: the badge's tint follows the status family so a status column
// scans by colour. UIFPE-667: a soft pill — tinted ground, text in the
// family's colour, no border. UIFPE-716: the prototype's pills — succeeded
// carries a dot, a run in flight is teal, and skipped / stopped / missed are
// grey (neutral, never a failure). Statuses outside the map (NOT TESTED,
// PENDING_CONNECTION_TEST, UNKNOWN) are neutral too.
const BADGE = {
  SUCCEEDED: "bg-signal-ok-bg text-signal-ok",
  PUBLISHED: "bg-signal-ok-bg text-signal-ok",
  FAILED: "bg-signal-fail-bg text-signal-fail",
  REJECTED: "bg-signal-fail-bg text-signal-fail",
  WARNING: "bg-signal-warn-bg text-signal-warn",
  SUBMITTED: "bg-signal-warn-bg text-signal-warn",
  RUNNING: "bg-signal-live-bg text-signal-live",
  STARTING: "bg-signal-live-bg text-signal-live",
};
const PILL_SHAPE = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-[11px] py-[3px] text-[12px] font-bold";

/** The status in sentence case ("Succeeded", "Pending connection test"); the
 *  raw wire value stays one hover away in the tooltip. An in-flight run keeps
 *  its pulsing dot, the one status that is still changing. */
export function StatusBadge({ status, title }) {
  const value = (status || "UNKNOWN").toUpperCase();
  const words = value.toLowerCase().replace(/_/g, " ");
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  const live = value === "RUNNING" || value === "STARTING";
  return (
    <span title={title || value} className={`${PILL_SHAPE} ${BADGE[value] || "bg-pearson-mist text-ink-soft"}`}>
      {live && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[value]}`} />}
      {value === "SUCCEEDED" && <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-current" />}
      {label}
    </span>
  );
}

// UIFPE-622: `...rest` so a caller's title= (the hover explanation on
// destination status and filter type pills) actually reaches the DOM
export function Pill({ children, tone = "mute", className = "", ...rest }) {
  // UIFPE-667: soft pills like the status badge — tint only, no border
  // UIFPE-716: the prototype's pill — 12px bold on the family's tint
  const tones = {
    mute: "bg-pearson-mist text-ink-soft",
    ok: "bg-signal-ok-bg text-signal-ok",
    warn: "bg-signal-warn-bg text-signal-warn",
    fail: "bg-signal-fail-bg text-signal-fail",
    live: "bg-signal-live-bg text-signal-live",
    accent: "bg-pearson-mist text-accent",
  };
  return (
    <span
      className={`${PILL_SHAPE} ${tones[tone]} ${className}`}
      {...rest}
    >
      {children}
    </span>
  );
}

export function Chip({ children }) {
  return (
    <code className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-meta text-ink">
      {children}
    </code>
  );
}

export function Button({
  children,
  variant = "default",
  size = "md",
  className = "",
  busy = false,
  disabled = false,
  ...rest
}) {
  // UIFPE-667: pill-shaped and flat. UIFPE-716: the prototype's buttons —
  // 38px and bold, a neutral hairline that turns light purple on hover,
  // Pearson purple for the primary, and an outlined red for danger
  const variants = {
    primary: "border border-pearson-purple bg-pearson-purple text-white hover:border-accent hover:bg-accent disabled:hover:border-pearson-purple disabled:hover:bg-pearson-purple",
    default:
      "border border-line bg-paper-card text-ink hover:border-paper-line hover:bg-paper-head",
    ghost: "text-accent hover:bg-accent-soft",
    danger:
      "border border-line bg-paper-card text-signal-fail hover:border-[#f3c2be] hover:bg-signal-fail-bg",
  };
  const sizes = { sm: "h-7 text-[12.5px] font-semibold", md: "h-[38px] text-body font-bold" };
  // a caller that sets its own horizontal padding (the icon-only pager
  // buttons pass "w-7 px-0") keeps it; otherwise the pill's roomier default
  const pad = /(^|\s)px-/.test(className) ? "" : size === "sm" ? "px-3" : "px-5";
  return (
    <button
      // UIFPE-418: `busy` locks the button while its call runs (no double
      // submits) and shows a spinner beside the label
      disabled={busy || disabled}
      aria-busy={busy || undefined}
      className={`inline-flex items-center justify-center gap-1.5 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${variants[variant]} ${sizes[size]} ${pad} ${className}`}
      {...rest}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

// UIFPE-693: a description that waits behind an ⓘ — shown on hover and on
// keyboard focus, pinned open by a click (click again or Escape to close), so
// the page never jumps when it appears
export function InfoTip({ text, label = "About this section" }) {
  const [pinned, setPinned] = React.useState(false);
  if (!text) return null;
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label}
        aria-expanded={pinned}
        onClick={() => setPinned((p) => !p)}
        onKeyDown={(e) => e.key === "Escape" && setPinned(false)}
        onBlur={() => setPinned(false)}
        className={`grid h-[26px] w-[26px] place-items-center rounded-full border transition-colors ${
          pinned
            ? "border-accent bg-accent-soft text-accent"
            : "border-line bg-paper-card text-ink-mute hover:border-accent hover:bg-accent-soft hover:text-accent"
        }`}
      >
        <Info size={13} strokeWidth={2} aria-hidden="true" />
      </button>
      <span
        role="tooltip"
        // UIFPE-716: the prototype's tip — an arrow points back at the ⓘ
        className={`pointer-events-none absolute left-0 top-full z-40 mt-2 w-[min(460px,80vw)] rounded-[10px] bg-pearson-purple px-3.5 py-3 text-body font-medium normal-case leading-relaxed tracking-normal text-white shadow-[0_8px_24px_rgb(13_0_77_/_0.3)] transition-opacity duration-150 before:absolute before:-top-1.5 before:left-2 before:border-x-[6px] before:border-b-[6px] before:border-x-transparent before:border-b-pearson-purple before:content-[''] group-hover:opacity-100 group-focus-within:opacity-100 ${
          pinned ? "opacity-100" : "opacity-0"
        }`}
      >
        {text}
      </span>
    </span>
  );
}

export function PageHeader({ title, sub, actions, back }) {
  return (
    // UIFPE-716: the prototype's page head — a 24px bold title, no rule under it
    <div className="mb-[18px]">
      {back}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="text-page font-extrabold tracking-tight text-ink">{title}</h1>
          {/* UIFPE-693: the page description sits behind the ⓘ */}
          <InfoTip text={sub} label={`About ${typeof title === "string" ? title : "this page"}`} />
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function Kpi({ label, value, hint, testid }) {
  return (
    <div
      data-testid={testid}
      className="rounded-card border border-white/70 bg-paper-card px-4 py-3.5 shadow-card"
    >
      <div className="text-label font-semibold uppercase tracking-wider text-ink-mute">
        {label}
      </div>
      <div className="tnum mt-1.5 text-kpi font-semibold tracking-tight text-ink">{value}</div>
      {hint && <div className="mt-1.5 text-meta text-ink-mute">{hint}</div>}
    </div>
  );
}

// UIFPE-626: a head entry is a string, or { label, align: "right" } for a
// numeric column whose cells are right-aligned by the caller. UIFPE-722:
// also `width` (a CSS width) and `title` (the header's hover text); a label
// may be an element (a sort button)
// UIFPE-768: `stagger` fades the rows up, 22ms apart, when they mount (theme.css .motion-rows)
export function Table({ head, children, stagger = false }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-body">
        <thead>
          <tr>
            {head.map((h, i) => {
              const col = typeof h === "string" ? { label: h } : h;
              return (
                // UIFPE-667: bold sentence-case header over a 2px dark rule
                <th
                  key={i}
                  title={col.title}
                  style={col.width ? { width: col.width } : undefined}
                  className={`border-b-2 border-ink px-3 py-2.5 text-meta font-bold text-ink ${
                    col.align === "right" ? "text-right" : "text-left"
                  }`}
                >
                  {col.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className={stagger ? "motion-rows" : undefined}>{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className = "", ...rest }) {
  return (
    <td className={`border-b border-line/60 px-3 py-2.5 align-middle ${className}`} {...rest}>
      {children}
    </td>
  );
}

// UIFPE-631: optional `icon` and `title` give the icon-and-headline empty
// state the list screens use; without them it renders exactly as before
export function EmptyState({ children, colSpan, icon: Icon, title }) {
  const content =
    Icon || title ? (
      <span className="flex flex-col items-center gap-1.5 text-body text-ink-mute">
        {Icon && (
          <span className="mb-1 grid h-9 w-9 place-items-center rounded-full bg-accent-soft text-accent">
            <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
          </span>
        )}
        {title && <b className="font-semibold text-ink">{title}</b>}
        {children && <span>{children}</span>}
      </span>
    ) : (
      <span className="text-body text-ink-mute">{children}</span>
    );
  if (colSpan) {
    return (
      <tr>
        <Td colSpan={colSpan} className="py-10 text-center">
          {content}
        </Td>
      </tr>
    );
  }
  return <div className="py-10 text-center">{content}</div>;
}

// UIFPE-716: the prototype's callout — a 3px bar on the left in the tone's
// colour over its tint. The children keep their own layout (some carry buttons).
export function Note({ children, tone = "mute" }) {
  const tones = {
    mute: "border-pearson-light-purple bg-paper-head text-ink-soft",
    ok: "border-signal-ok bg-signal-ok-bg text-signal-ok",
    warn: "border-signal-warn-line bg-signal-warn-bg text-signal-warn",
    fail: "border-signal-fail bg-signal-fail-bg text-[#8a1c16]",
  };
  return (
    <div className={`my-3 rounded-r-[10px] border-l-[3px] px-3.5 py-2.5 text-[13px] ${tones[tone] || tones.mute}`}>
      {children}
    </div>
  );
}

// UIFPE-722: the prototype's form label — bold, sentence case, its hint inline
export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[14px] font-bold text-ink">
        {label}
        {hint && <span className="ml-1.5 text-meta font-normal text-ink-mute">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

// UIFPE-716: the prototype's field — 10px radius, roomier, same height as a button
// UIFPE-722: the caption above a list filter — bold sentence case
export const filterLabel = "mb-1 block text-[12.5px] font-bold text-ink";

export const inputClass =
  "w-full rounded-[10px] border border-line bg-paper-card px-3 py-2 text-body text-ink outline-none transition-colors placeholder:text-ink-mute/70 hover:border-paper-line focus:border-accent focus:ring-2 focus:ring-accent/25";

/* UIFPE-759: the list tables' two toolbar actions and their foot — the
   Scheduled jobs tab's controls, shared by Insights → Jobs and Exports */
export function TableActions({ onExport, exportDisabled = false, onClear, className = "pb-1" }) {
  return (
    <span className={`ml-auto flex shrink-0 items-center gap-4 ${className}`}>
      <button
        type="button"
        onClick={onExport}
        disabled={exportDisabled}
        title="Download the rows below as CSV — every page, with the filters applied"
        className="inline-flex items-center gap-1.5 rounded-full border border-accent px-3.5 py-1.5 text-meta font-bold text-accent hover:bg-accent-soft disabled:opacity-40"
      >
        <Download size={13} aria-hidden="true" /> Export
      </button>
      <button type="button" onClick={onClear} className="inline-flex items-center gap-1 text-meta font-bold text-accent hover:underline">
        <FunnelX size={14} aria-hidden="true" /> Clear all
      </button>
    </span>
  );
}

// UIFPE-770 (reused by UIFPE-769): a figure that counts up to its value over
// 400ms, eased, with tabular digits so the width never jitters. It renders the
// final value at once under prefers-reduced-motion or while the tab is hidden,
// and counts from the previous value when the value changes.
export function CountUp({ value, format = (n) => n.toLocaleString("en-GB"), duration = 400, className = "" }) {
  const target = Number(value) || 0;
  const [shown, setShown] = useState(target);
  const prev = useRef(null);
  useEffect(() => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden;
    const from = prev.current ?? 0;
    prev.current = target;
    if (still || from === target) {
      setShown(target);
      return undefined;
    }
    const t0 = performance.now();
    let raf = 0;
    const tick = (now) => {
      const k = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(from + (target - from) * eased));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return <span className={`tnum ${className}`}>{format(shown)}</span>;
}

// UIFPE-769: a segmented control's filled pill as ONE element that slides
// between buttons (theme.css .motion-seg / .motion-seg-ind). The hook measures
// the button carrying aria-pressed="true" inside `ref`; the pill is hidden
// (and the pressed button keeps its own fill) under prefers-reduced-motion.
export function useSlidingPill(ref, deps) {
  const [pos, setPos] = useState(null);
  useLayoutEffect(() => {
    const measure = () => {
      const on = ref.current?.querySelector('[aria-pressed="true"]');
      setPos(on ? { left: on.offsetLeft, width: on.offsetWidth } : null);
    };
    measure();
    document.fonts?.ready.then(measure).catch(() => {});
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return pos;
}

export function SlidingPill({ pos }) {
  return pos ? <span aria-hidden="true" className="motion-seg-ind" style={{ left: pos.left, width: pos.width }} /> : null;
}

export const PER_PAGE_OPTIONS = [10, 25, 50];

/** "1–N of M noun" · ‹ Page X of Y › · Items per page. `page` is 0-based. */
export function TableFoot({ total, page, setPage, per, setPer, noun = "items" }) {
  const pages = Math.max(1, Math.ceil(total / per));
  const safe = Math.min(page, pages - 1);
  const start = safe * per;
  const step = "grid h-7 w-7 place-items-center rounded-brand border border-line disabled:opacity-40";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-3 text-meta text-ink-mute">
      <span className="tnum">{total ? `${start + 1}–${Math.min(start + per, total)} of ${total} ${noun}` : `0 ${noun}`}</span>
      <span className="flex items-center gap-2">
        <button type="button" aria-label="Previous page" disabled={safe === 0} onClick={() => setPage(safe - 1)} className={step}>
          <ChevronLeft size={14} />
        </button>
        <span className="tnum">Page {safe + 1} of {pages}</span>
        <button type="button" aria-label="Next page" disabled={safe + 1 >= pages} onClick={() => setPage(safe + 1)} className={step}>
          <ChevronRight size={14} />
        </button>
      </span>
      <label className="flex items-center gap-2">
        Items per page
        <select
          value={per}
          onChange={(e) => {
            setPer(Number(e.target.value));
            setPage(0);
          }}
          className={`${inputClass.replace("w-full", "w-auto").replace("py-2", "py-0")} h-8`}
        >
          {PER_PAGE_OPTIONS.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
      </label>
    </div>
  );
}

// ---- formatting helpers (mirroring the platform's rules) -------------------

export function fmtNum(n) {
  return n === null || n === undefined ? DASH : Number(n).toLocaleString();
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return DASH;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

export function fmtRelative(iso) {
  if (!iso) return DASH;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Math.round((Date.now() - then) / 1000);
  const units = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.35, "week"],
    [12, "month"],
  ];
  let value = Math.abs(diff);
  let unit = "second";
  for (const [step, name] of units) {
    unit = name;
    if (value < step) break;
    value = value / step;
  }
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  return rtf.format(diff > 0 ? -Math.round(value) : Math.round(value), unit);
}

// UIFPE-722: the prototype's short form, "Mon 29 Sep · 09:12", in the viewer's time
export function fmtDayTime(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // fixed short names: ICU spells September "Sept" in some locales
  const day = `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()]} ${d.getDate()} ${
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()]
  }`;
  const pad = (n) => String(n).padStart(2, "0");
  return `${day} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtAbsolute(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
