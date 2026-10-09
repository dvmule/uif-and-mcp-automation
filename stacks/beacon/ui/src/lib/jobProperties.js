// jobProperties.js (UIFPE-702)
// The wizard's Job properties step, as pure functions: the delivered file
// name (said, never chosen — the publisher sets it) and the Deliver-to
// multi-select's rule that the managed partner-drop stands alone.

/** The managed partner-drop's key in the Deliver-to list. */
export const MANAGED_KEY = "__managed";

/** The delivered file's name, as the publisher builds it (publisher.py,
 *  UIFPE-196): `<id>_<name slug>_<yyyyMMdd>_<batch>.<ext>`, `.zip` added
 *  when zipped. A new export has no id yet — `<id>` stands in for it. */
export function deliveredFileName({ jobId = "", name = "", format = "", zip = false } = {}) {
  const id = String(jobId || "<id>");
  const slug = String(name || jobId || "")
    .replace(/[^A-Za-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  const stem = slug && slug !== id ? `${id}_${slug}` : id;
  const ext = String(format || "csv").toLowerCase();
  return `${stem}_{yyyyMMdd}_{batch}.${ext}${zip ? ".zip" : ""}`;
}

/** The Deliver-to multi-select's keys for today's destination ids — the
 *  managed drop is on when nothing else is. */
export function deliverToKeys(ids) {
  return ids && ids.length ? [...ids] : [MANAGED_KEY];
}

/** New destination ids from the multi-select's keys. Picking the managed drop
 *  clears the rest (it stands alone); picking any destination drops it. */
export function deliverToIds(prevIds, keys) {
  const next = keys || [];
  const hadManaged = !prevIds || prevIds.length === 0;
  if (next.includes(MANAGED_KEY) && !hadManaged) return [];
  return next.filter((k) => k !== MANAGED_KEY);
}
