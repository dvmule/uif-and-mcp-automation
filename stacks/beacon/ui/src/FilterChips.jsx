// FilterChips (UIFPE-762): the list toolbars' filters as chips — one removable
// chip per filter that narrows the list, and a "+ Filter" menu to add one.
// A filter at its default (every option on, "Any time", retired hidden) has no
// chip, so an untouched list shows just Find and + Filter. The rules are the
// pure functions in lib/filterChips.js (vitest); each chip's menu is MultiSelect.
// UIFPE-768: a chip scales in when added and, when it leaves, plays its exit
// (a static copy with the last text it showed) before the row closes up.
//
// filters   definitions — see lib/filterChips.js for the three kinds
import React, { useEffect, useRef, useState } from "react";
import MultiSelect from "./MultiSelect.jsx";
import { chipValueText, isActive, offerable, resetValue, startValue } from "./lib/filterChips.js";

const EXIT_MS = 160; // matches theme.css .motion-chip-out

/** What a chip reads, for the exit copy: the name and its picks. */
function chipText(d) {
  if (d.kind === "toggle") return d.onText || "on";
  if (d.kind === "single") return d.items.find((it) => it.key === d.value)?.label ?? "";
  return chipValueText(d.items.filter((it) => d.selected.includes(it.key)).map((it) => it.label));
}

function StaticChip({ name, text, className = "", ...rest }) {
  return (
    <span className={`inline-flex items-center rounded-full border border-pearson-light-purple bg-accent-soft py-1.5 pl-3 pr-2.5 text-meta text-accent ${className}`} {...rest}>
      <span className="font-medium text-ink-soft">{name}</span>
      <span className="ml-1.5 font-bold">{text}</span>
    </span>
  );
}

export default function FilterChips({ filters, className = "" }) {
  // the chip just added from "+ Filter" opens its menu straight away
  const [openKey, setOpenKey] = useState(null);
  const offered = offerable(filters);
  const active = offered.filter(isActive);

  // chips that just became inactive stay for EXIT_MS as a static copy playing the exit
  const [leaving, setLeaving] = useState({}); // key -> { name, text }
  const lastShown = useRef({});
  active.forEach((d) => {
    lastShown.current[d.key] = { name: d.name, text: chipText(d) };
  });
  const activeKeys = active.map((d) => d.key).join("|");
  const prevKeys = useRef(activeKeys);
  useEffect(() => {
    const before = prevKeys.current ? prevKeys.current.split("|") : [];
    const now = activeKeys ? activeKeys.split("|") : [];
    prevKeys.current = activeKeys;
    const gone = before.filter((k) => !now.includes(k));
    if (!gone.length || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return undefined;
    setLeaving((l) => ({ ...l, ...Object.fromEntries(gone.map((k) => [k, lastShown.current[k]])) }));
    const t = setTimeout(
      () =>
        setLeaving((l) => {
          const next = { ...l };
          gone.forEach((k) => delete next[k]);
          return next;
        }),
      EXIT_MS
    );
    return () => clearTimeout(t);
  }, [activeKeys]);

  const toggleFromMenu = (key) => {
    const d = offered.find((f) => f.key === key);
    if (!d) return;
    if (isActive(d)) {
      d.onChange(resetValue(d));
      setOpenKey(null);
    } else {
      d.onChange(startValue(d));
      setOpenKey(key);
    }
  };

  const activeKeySet = new Set(active.map((d) => d.key));

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`} aria-label="Filters">
      {active.map((d) =>
        d.kind === "toggle" ? (
          <span
            key={d.key}
            className="motion-chip-in inline-flex items-center rounded-full border border-pearson-light-purple bg-accent-soft py-1.5 pl-3 text-meta text-accent"
          >
            <span className="font-medium text-ink-soft">{d.name}</span>
            <span className="ml-1.5 font-bold">{d.onText || "on"}</span>
            <button
              type="button"
              onClick={() => d.onChange(false)}
              aria-label={`Remove the ${d.name} filter`}
              title="Remove this filter"
              className="ml-1 py-0.5 pl-1 pr-2.5 text-[11px] leading-none text-ink-soft hover:text-dot-fail"
            >
              ✕
            </button>
          </span>
        ) : (
          <MultiSelect
            key={d.key}
            variant="chip"
            className="motion-chip-in"
            chipName={d.name}
            label={`${d.name} filter`}
            noun={d.noun || d.name.toLowerCase()}
            items={d.items}
            selected={d.kind === "single" ? [d.value] : d.selected}
            single={d.kind === "single"}
            onChange={d.onChange}
            onRemove={() => d.onChange(resetValue(d))}
            defaultOpen={openKey === d.key}
          />
        )
      )}
      {Object.entries(leaving)
        .filter(([k, v]) => v && !activeKeySet.has(k))
        .map(([k, v]) => (
          <StaticChip key={`leaving-${k}`} name={v.name} text={v.text} className="motion-chip-out" aria-hidden="true" />
        ))}
      <MultiSelect
        variant="add"
        single
        label="Add a filter"
        noun="filters"
        items={offered.map((f) => ({ key: f.key, label: f.name }))}
        selected={active.map((f) => f.key)}
        onChange={toggleFromMenu}
      />
    </div>
  );
}
