// destinationPicker.js (UIFPE-539)
// Pure logic for the wizard's scalable destination picker — filtering,
// paging and the card-grid/picker threshold live here so vitest can hold
// them without a DOM; DestinationPicker.jsx only renders what these say.

// Up to this many author-owned destinations the wizard keeps the original
// card grid; above it, the searchable picker takes over. 10 since UIFPE-600
// (owner decision 2026-09-25): a team with nine or ten keeps the one-glance
// grid rather than a paged list.
export const PICKER_THRESHOLD = 10;

// Dense rows, so a page is one screenful without nested scrolling.
export const PICKER_PAGE_SIZE = 8;

// The API's status vocabulary: anything pending-* has not passed its
// connection test yet (same reading as the Destinations screen).
export function isVerified(d) {
  return !(d.status || "").startsWith("pending");
}

// UIFPE-595: what an untested destination is actually waiting for, per
// channel. The API's single `pending_connection_test` status is right for a
// server you connect to and wrong for email, where there is nothing to
// connect to — only a mail to send. Reported as "pointless bureaucracy",
// which is what an accurate label fixes. The status values themselves are
// unchanged; this is only how they are said.
const PENDING_TEXT = {
  email: "Not tested yet — send a test email",
  sftp: "Not tested yet — connection not proven",
  s3: "Not tested yet — write access not proven",
};

/** What this destination's status means, in words. Verified destinations and
 *  channels we have no phrasing for fall back to the raw status, so a new
 *  channel or a status we do not know still shows something true. */
export function statusText(d) {
  if (!d) return "";
  if (isVerified(d)) return "verified";
  return PENDING_TEXT[d.channel] || (d.status || "").replace(/_/g, " ");
}

// UIFPE-699: channels as people name them — "AWS S3", never a bare "S3" or
// the id in capitals. The order is the channel cards' order.
export const CHANNEL_ORDER = ["s3", "sftp", "email"];
const CHANNEL_LABEL = { s3: "AWS S3", sftp: "SFTP", email: "Email" };

/** "s3" → "AWS S3"; an unknown channel id in capitals. */
export function channelLabel(channel) {
  return CHANNEL_LABEL[channel] || String(channel || "").toUpperCase();
}

/** Where a destination delivers, as its row shows it: the S3 prefix, the
 *  recipients, or host:directory. */
export function destinationAddress(d) {
  if (!d) return "";
  if (d.destination_s3_prefix) return d.destination_s3_prefix;
  if (d.recipients) return d.recipients.join(", ");
  return d.host ? `${d.host}:${d.remote_dir || ""}` : "";
}

/** The Destinations screen's list: channels switched OFF are hidden, and the
 *  find box matches the name or the address. */
export function filterDestinationRows(rows, { query = "", channelsOff = [] } = {}) {
  const q = query.trim().toLowerCase();
  return (rows || []).filter(
    (d) =>
      !channelsOff.includes(d.channel) &&
      (!q || (d.name || "").toLowerCase().includes(q) || destinationAddress(d).toLowerCase().includes(q))
  );
}

/** The action that would verify this destination, for the button's label. */
export function testActionLabel(channel) {
  return channel === "email" ? "Send test email" : "Test";
}

// UIFPE-595: an untested EMAIL destination may still be selected. An
// unproven SFTP or S3 target means a run that dies at delivery, so those
// stay locked; an unproven email address is the author's own recipient and
// the failure lands in front of them. Never auto-verified either way —
// "verified" has to keep meaning that a real send was accepted, or a typo
// like `pearson.con` would look fine and fail silently.
export function selectableWhileUntested(d) {
  return (d?.channel || "") === "email";
}

// One destination against the toolbar. `target` is the rendered delivery
// target (host / bucket / recipients) — search must match what the author
// SEES, because with many partners people remember "the Ingram SFTP one",
// not the record's exact name.
export function destinationMatches(d, { query = "", channel = "", verifiedOnly = false }, target = "") {
  if (channel && d.channel !== channel) return false;
  if (verifiedOnly && !isVerified(d)) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    (d.name || "").toLowerCase().includes(q) ||
    String(target).toLowerCase().includes(q)
  );
}

export function filterDestinations(list, filters, targetFor = () => "") {
  return (list || []).filter((d) => destinationMatches(d, filters, targetFor(d)));
}

// Clamp the page against the FILTERED total (narrowing the list must never
// strand the author on an empty page) and hand back the slice bounds.
export function pageBounds(total, page, size = PICKER_PAGE_SIZE) {
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(Math.max(0, page), pages - 1);
  const start = p * size;
  return { page: p, pages, start, end: Math.min(start + size, total) };
}

// The distinct channels actually present, sorted — the filter dropdown
// only offers what exists rather than a hardcoded list.
export function channelsOf(list) {
  return [...new Set((list || []).map((d) => d.channel).filter(Boolean))].sort();
}
