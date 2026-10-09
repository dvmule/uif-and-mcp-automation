// runSelection.js (UIFPE-604)
// Picking days on the export detail's Run history chart to filter the Recent
// runs table: click one bar for one day, or drag across bars for a range, the
// way CloudWatch lets you drag-select a time window.
//
// A selection is { from, to } — two "YYYY-MM-DD" days, from <= to, both
// inclusive; null means "no selection" and the table shows the latest runs
// as before. Days are compared as strings, which orders ISO dates correctly.
// Which day a run belongs to is runHealth.runDay — the SAME rule the bars are
// built with, so the rows a bar selects are exactly the runs it counts (plus
// any stopped / skipped / in-flight runs of that day, which the chart does not
// draw but the table still lists).
import { isInFlight, runDay, scoredRuns } from "./runHealth.js";

/** How many runs the export detail page asks for — the API's maximum. The
 *  default (50) left the 90-day chart short on any export with more runs, and
 *  a date selection over older days would have quietly listed too few. */
export const RUN_FETCH_LIMIT = 200;

/** Rows the filtered table shows before "Show more". */
export const SELECTION_PAGE = 25;

/** The range between two picked days, in order; null if either is missing. */
export function selectDays(a, b) {
  if (!a || !b) return null;
  return a <= b ? { from: a, to: b } : { from: b, to: a };
}

/** A click on one bar: selects that day, or clears when it is already the
 *  whole selection (click again to undo). */
export function clickDay(current, day) {
  if (!day) return current;
  if (current && current.from === day && current.to === day) return null;
  return { from: day, to: day };
}

/** The end of a press-drag-release on the chart: the same bar = a click. */
export function finishDrag(current, start, end) {
  if (!start) return current;
  if (!end || start === end) return clickDay(current, start);
  return selectDays(start, end);
}

export function inSelection(day, selection) {
  return Boolean(selection && day && day >= selection.from && day <= selection.to);
}

/** The runs a selection keeps, newest first as given; every run when none. */
export function runsInSelection(runs, selection) {
  const all = runs || [];
  return selection ? all.filter((r) => inSelection(runDay(r), selection)) : all;
}

/** "09-15" for one day, "09-13 – 09-17" for a range — the chart's own labels. */
export function selectionLabel(selection) {
  if (!selection) return "";
  const from = selection.from.slice(5);
  const to = selection.to.slice(5);
  return from === to ? from : `${from} – ${to}`;
}

/** What the chip above the filtered table says: how many runs are listed,
 *  and — when some of them are not drawn on the chart — why the count is
 *  higher than the bars. */
export function selectionSummary(selectedRuns) {
  const total = (selectedRuns || []).length;
  const plotted = scoredRuns(selectedRuns).length;
  return {
    total,
    plotted,
    unplotted: total - plotted,
    text: `${total} run${total === 1 ? "" : "s"}`,
  };
}

/** True when the API answered with the full cap of recorded runs, so older
 *  runs exist that this page did not load. In-flight rows are merged in on
 *  top of the cap (UIFPE-561) and do not count toward it. */
export function historyCapped(runs, limit = RUN_FETCH_LIMIT) {
  return (runs || []).filter((r) => !isInFlight(r)).length >= limit;
}
