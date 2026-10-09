// MultiSelect (UIFPE-693): the shared multi-select dropdown of the redesign —
// Domains in the wizard, destination Type / Deliver to, the list filters.
// The closed box names one pick, counts several ("2 domains selected of 4") or
// says "All domains (4)"; the menu has a search box, a Select all / Clear all
// row and a scrolling list, so twenty-plus options stay tidy. The wording and
// toggle rules are the pure functions in lib/multiSelect.js (vitest).
//
// items     [{ key, label, meta?, icon? }]   meta = quiet text on the right
// selected  array of keys; onChange(nextKeys)
// size      "md" (forms) or "sm" (32px, for list toolbars — UIFPE-695)
//
// UIFPE-762: two more faces for the list toolbars' filter chips (FilterChips.jsx):
// variant   "box" (default) · "chip" (a pill reading "chipName · picks ▾" with a
//           ✕ that calls onRemove) · "add" (a dashed "+ Filter" pill)
// single    one pick at a time — onChange(key), no search or Select all, closes on pick
// defaultOpen  starts open (a chip just added from "+ Filter")
import React, { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { allSelected, filterItems, selectionLabel, toggleAll, toggleOne } from "./lib/multiSelect.js";
import { chipValueText } from "./lib/filterChips.js";

function Tick({ on }) {
  return (
    <span
      aria-hidden="true"
      className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[4px] border-[1.5px] ${
        on ? "border-accent bg-accent text-white" : "border-ink-mute bg-paper-card"
      }`}
    >
      {on && <Check size={12} strokeWidth={3} />}
    </span>
  );
}

export default function MultiSelect({
  items,
  selected,
  onChange,
  noun = "items",
  placeholder = "Choose…",
  label,
  className = "",
  emptyText = "Nothing to choose from yet.",
  size = "md",
  variant = "box",
  chipName,
  onRemove,
  single = false,
  defaultOpen = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [query, setQuery] = useState("");
  const box = useRef(null);
  const search = useRef(null);
  const listId = useId();
  const chip = variant === "chip";
  const add = variant === "add";
  // the chip menus skip the search box for a handful of options; "add" and single lists never search
  const searchable = !single && !add && (!chip || items.length > 5);

  // close on a click outside or Escape; the search box takes focus on open
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    const t = setTimeout(() => search.current?.focus(), 20);
    return () => {
      document.removeEventListener("mousedown", onDown);
      clearTimeout(t);
    };
  }, [open]);

  // an empty list says so plainly instead of reading like a filter that hides everything
  const summary = items.length
    ? selectionLabel(items, selected, noun, placeholder)
    : { text: `No ${noun} yet`, empty: true };
  const shown = filterItems(items, query);
  const every = allSelected(items, selected);
  const pick = (key) => {
    if (single) {
      onChange(key);
      setOpen(false);
    } else onChange(toggleOne(selected, key));
  };

  const wrap = chip
    ? `inline-flex items-center rounded-full border bg-accent-soft ${open ? "border-accent ring-2 ring-accent/20" : "border-pearson-light-purple"}`
    : add
      ? "inline-block"
      : "inline-block min-w-[220px] max-w-full";
  const button = chip
    ? "flex items-center gap-1.5 rounded-l-full py-1.5 pl-3 pr-1.5 text-meta text-accent hover:bg-pearson-light-purple/50"
    : add
      ? `inline-flex items-center gap-1.5 rounded-full border border-dashed px-3 py-1.5 text-meta font-bold text-accent hover:bg-accent-soft ${open ? "border-accent bg-accent-soft" : "border-pearson-light-purple bg-paper-card"}`
      : `flex w-full items-center justify-between gap-2.5 border bg-paper-card text-left text-body transition-colors ${
          size === "sm" ? "h-[38px] rounded-[10px] px-3" : "rounded-[10px] px-3 py-2"
        } ${open ? "border-accent ring-2 ring-accent/20" : "border-line hover:border-pearson-light-purple"}`;

  return (
    <div
      ref={box}
      className={`relative ${wrap} ${className}`}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={label}
        onClick={() => {
          setQuery("");
          setOpen((o) => !o);
        }}
        className={button}
      >
        {chip && (
          <>
            <span className="font-medium text-ink-soft">{chipName}</span>
            <span className="font-bold">
              {chipValueText(items.filter((it) => selected.includes(it.key)).map((it) => it.label))}
            </span>
            <ChevronDown size={12} aria-hidden="true" className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
          </>
        )}
        {add && (
          <>
            <span aria-hidden="true">+</span> Filter
          </>
        )}
        {!chip && !add && (
          <>
            <span className={`min-w-0 truncate ${summary.empty ? "text-ink-mute" : "font-semibold text-ink"}`}>
              {summary.text}
              {summary.of && <span className="ml-1 font-medium text-ink-mute">of {summary.of}</span>}
            </span>
            <ChevronDown
              size={16}
              aria-hidden="true"
              className={`shrink-0 text-ink-mute transition-transform ${open ? "rotate-180" : ""}`}
            />
          </>
        )}
      </button>
      {chip && onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove the ${chipName} filter`}
          title="Remove this filter"
          className="rounded-r-full py-1.5 pl-1 pr-2.5 text-[11px] leading-none text-ink-soft hover:text-dot-fail"
        >
          ✕
        </button>
      )}

      {open && (
        <div
          className={`motion-menu-in absolute left-0 top-full z-30 mt-1.5 rounded-[12px] border border-line bg-paper-card p-1.5 shadow-lg ${
            chip || add ? "min-w-[210px]" : "min-w-full"
          }`}
        >
          {searchable && (
            <div className="relative mb-1.5 mt-0.5">
              <Search size={14} aria-hidden="true" className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
              <input
                ref={search}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${noun}`}
                aria-label={`Search ${noun}`}
                className="w-full rounded-[8px] border border-line bg-paper-card py-1.5 pl-8 pr-2.5 text-body text-ink outline-none focus:border-accent"
              />
            </div>
          )}
          {!single && !add && (
            <button
              type="button"
              onClick={() => onChange(toggleAll(items, selected))}
              disabled={!items.length}
              className="mb-1 flex w-full items-center gap-2.5 rounded-t-[8px] border-b border-line px-2.5 py-2 text-left text-body font-semibold text-ink hover:bg-paper-head"
            >
              <Tick on={every} />
              <span>{every ? "Clear all" : "Select all"}</span>
              <span className="ml-auto text-meta font-medium text-ink-mute">
                {selected.filter((k) => items.some((it) => it.key === k)).length} / {items.length}
              </span>
            </button>
          )}
          <div id={listId} role="listbox" aria-multiselectable={!single} className="max-h-[264px] overflow-y-auto">
            {!items.length && <div className="px-2.5 py-2 text-meta text-ink-mute">{emptyText}</div>}
            {items.length > 0 && !shown.length && (
              <div className="px-2.5 py-2 text-meta text-ink-mute">No match for “{query.trim()}”.</div>
            )}
            {shown.map((it) => {
              const on = selected.includes(it.key);
              const Icon = it.icon;
              return (
                <button
                  key={it.key}
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => pick(it.key)}
                  className="flex w-full items-center gap-2.5 whitespace-nowrap rounded-[8px] px-2.5 py-2 text-left text-body text-ink hover:bg-paper-head"
                >
                  <Tick on={on} />
                  {Icon && <Icon size={15} strokeWidth={1.75} aria-hidden="true" className="shrink-0 text-accent" />}
                  <span className="min-w-0 truncate">{it.label}</span>
                  {it.meta != null && <span className="ml-auto pl-4 text-meta text-ink-mute">{it.meta}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
