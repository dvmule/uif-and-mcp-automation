// RangePicker (UIFPE-610): CloudWatch-style time range — quick picks and a
// Custom from/to. Controlled: `value` is { kind: "preset", id } or
// { kind: "custom", from, to } ("YYYY-MM-DD" local days); `onChange` gets the
// next value. The window rules live in lib/overviewRange.js.
import React, { useEffect, useRef, useState } from "react";
import { CalendarDays } from "lucide-react";
import { Button, SlidingPill, inputClass, useSlidingPill } from "./ui.jsx";
import { PRESETS, localDay, rangeWindow } from "./lib/overviewRange.js";

export default function RangePicker({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const today = localDay(new Date());
  const [from, setFrom] = useState(value?.kind === "custom" ? value.from : today);
  const [to, setTo] = useState(value?.kind === "custom" ? value.to : today);
  const boxRef = useRef(null);
  const draft = rangeWindow({ kind: "custom", from, to });

  // close the custom panel on Escape or a click outside it
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    const onDown = (e) => boxRef.current && !boxRef.current.contains(e.target) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  // UIFPE-625: segmented control — the active window filled in Pearson purple
  const pill = (active) =>
    `rounded-md px-2.5 py-1 text-meta font-semibold transition-colors ${
      active ? "bg-pearson-purple text-white shadow-card" : "text-ink-mute hover:bg-paper-head hover:text-ink"
    }`;
  const custom = value?.kind === "custom";
  // UIFPE-769: one filled pill slides between the buttons
  const pillPos = useSlidingPill(boxRef, [value?.kind, value?.id, value?.from, value?.to]);

  return (
    <div ref={boxRef} className="motion-seg relative inline-flex items-center gap-0.5 rounded-lg border border-line bg-paper-card p-1 shadow-card">
      <SlidingPill pos={pillPos} />
      {PRESETS.map((p) => (
        <button
          key={p.id}
          type="button"
          className={pill(!custom && value?.id === p.id)}
          aria-pressed={!custom && value?.id === p.id}
          onClick={() => {
            setOpen(false);
            onChange({ kind: "preset", id: p.id });
          }}
        >
          {/* UIFPE-722: the prototype's words — "24 hours", not "24h" */}
          {p.label}
        </button>
      ))}
      <button
        type="button"
        className={`${pill(custom)} inline-flex items-center gap-1`}
        aria-pressed={custom}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <CalendarDays size={12} /> {custom ? rangeWindow(value).label : "Custom"}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-30 mt-1.5 w-[290px] rounded-card border border-line bg-paper-card p-3 text-ink shadow-lg">
          <div className="grid grid-cols-2 gap-2">
            <label className="text-label font-semibold uppercase tracking-wide text-ink-mute">
              From
              <input type="date" className={`${inputClass} mt-1`} value={from} max={today} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="text-label font-semibold uppercase tracking-wide text-ink-mute">
              To
              <input type="date" className={`${inputClass} mt-1`} value={to} max={today} onChange={(e) => setTo(e.target.value)} />
            </label>
          </div>
          {draft.error && <p className="mt-2 text-meta text-signal-fail">{draft.error}</p>}
          <div className="mt-3 flex justify-end gap-2">
            <Button size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!!draft.error}
              onClick={() => {
                setOpen(false);
                onChange({ kind: "custom", from, to });
              }}
            >
              Apply
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
