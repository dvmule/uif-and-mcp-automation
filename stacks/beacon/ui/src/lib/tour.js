// tour.js (UIFPE-445)
// The guided tour's data and pure logic. The steps, the "seen it" memory,
// and the card-placement math live here so vitest can hold them without a
// DOM; Tour.jsx is only the overlay that renders what these functions say.

export const TOUR_SEEN_KEY = "beacon.tourSeen";

// One plain-English sentence or two per stop — written for someone who has
// never seen the panel and is not an engineer (the UIFPE-425 info-card rule).
// `target` names a data-tour attribute on the real element; a step with no
// target renders as a centered card.
export const TOUR_STEPS = [
  {
    id: "home-kpis",
    route: "/m/beacon",
    target: "home-kpis",
    title: "Welcome to Beacon",
    body: "Beacon sends your data to the people who need it, on a schedule or on demand. This is Home — these figures answer \"is everything healthy?\" the moment you arrive.",
  },
  {
    id: "home-runs",
    route: "/m/beacon",
    target: "home-runs",
    title: "Recent activity",
    body: "Every delivery shows up here as a run. Green means the files reached their destination; red means something needs a look. Click any export name to see its full story.",
  },
  {
    id: "new-export",
    route: "/m/beacon/exports",
    target: "new-export",
    title: "Creating an export",
    body: "This button starts the wizard. An export is a recipe: which data to take, which columns to include, and where to send the files.",
  },
  {
    id: "exports-list",
    route: "/m/beacon/exports",
    target: "exports-list",
    title: "Your exports",
    body: "Everything published lives here, with its schedule and last result. Use the search and the dropdowns above the table to find one quickly.",
  },
  {
    id: "wizard-steps",
    route: "/m/beacon/exports/new",
    target: "wizard-steps",
    title: "The wizard, step by step",
    body: "Start by choosing how — the guided steps, or your own SQL read into them. Then pick a file format, pick the data, choose and transform columns (the fx button), filter rows, choose where files go, set a schedule, and review and publish. You can move back and forth freely — nothing happens until you publish.",
  },
  {
    id: "destinations",
    route: "/m/beacon/destinations",
    target: "destinations-list",
    title: "Destinations",
    body: "A destination is a place files can be delivered — a partner's SFTP server or an AWS S3 bucket. Register it once, test the connection, and every export can use it.",
  },
  {
    id: "runs",
    route: "/m/beacon/runs",
    target: "runs-list",
    title: "Insights",
    body: "How every export is running. Overview sums up the last day, Jobs shows each export's latest runs with Open insight for the detail, and Scheduled jobs lists what will start next.",
  },
  {
    id: "finish",
    route: "/m/beacon",
    target: null,
    title: "You're all set",
    body: "That's the whole panel. Restart this tour any time from the Tour button in the top bar. A good first step: open Exports and look at one that already works.",
  },
];

function defaultStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// True when the offer should NOT appear. A broken/blocked storage reads as
// "seen": never nag someone we cannot remember answering.
export function seenTour(storage = defaultStorage()) {
  try {
    return storage ? storage.getItem(TOUR_SEEN_KEY) === "1" : true;
  } catch {
    return true;
  }
}

export function markTourSeen(storage = defaultStorage()) {
  try {
    storage?.setItem(TOUR_SEEN_KEY, "1");
  } catch {
    /* per-viewer convenience only — losing it is fine */
  }
}

// Where the card goes relative to the highlighted element: below when it
// fits, then above, then beside; always clamped inside the viewport with a
// margin. Pure — Tour.jsx feeds it live rects.
export function placeCard(target, viewport, card, gap = 12, margin = 12) {
  if (!target) {
    return {
      top: Math.max(margin, (viewport.height - card.height) / 2),
      left: Math.max(margin, (viewport.width - card.width) / 2),
    };
  }
  const clampLeft = (left) =>
    Math.min(Math.max(left, margin), Math.max(margin, viewport.width - card.width - margin));
  const clampTop = (top) =>
    Math.min(Math.max(top, margin), Math.max(margin, viewport.height - card.height - margin));

  const centered = clampLeft(target.left + target.width / 2 - card.width / 2);
  if (target.top + target.height + gap + card.height + margin <= viewport.height) {
    return { top: target.top + target.height + gap, left: centered };
  }
  if (target.top - gap - card.height >= margin) {
    return { top: target.top - gap - card.height, left: centered };
  }
  const beside = clampTop(target.top + target.height / 2 - card.height / 2);
  if (target.left + target.width + gap + card.width + margin <= viewport.width) {
    return { top: beside, left: target.left + target.width + gap };
  }
  if (target.left - gap - card.width >= margin) {
    return { top: beside, left: target.left - gap - card.width };
  }
  return { top: clampTop(viewport.height - card.height - margin), left: centered };
}
