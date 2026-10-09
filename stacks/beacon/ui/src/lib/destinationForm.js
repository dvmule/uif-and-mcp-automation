// destinationForm.js (UIFPE-599)
// The pure half of "add a destination": the empty form, the request body the
// API expects for each channel, and what has to happen before a new
// destination may be used. Shared by the Destinations screen and the wizard's
// add-destination dialog so the two cannot drift — before this the body was
// assembled inline in one screen and the wizard sent authors to that screen,
// losing the export they were half-way through.

import { selectableWhileUntested } from "./destinationPicker.js";

export const EMPTY_FORM = {
  name: "",
  channel: "s3",
  destination_s3_prefix: "",
  host: "",
  remote_dir: "",
  username: "",
  password: "",
  recipients: "",
  visibility: "private",
};

/** "a@x.com, b@y.com" → ["a@x.com", "b@y.com"]; blanks dropped. */
export function splitRecipients(text) {
  return String(text || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** The POST /destinations body for a filled-in form. Credentials ride in the
 *  request only — the API hands them to Secrets Manager and keeps a
 *  `secret_ref` (D16); nothing here is stored client-side. */
export function createBody(form) {
  const body = {
    name: form.name,
    channel: form.channel,
    domain: "product",
    visibility: form.visibility,
  };
  if (form.channel === "s3") {
    body.destination_s3_prefix = form.destination_s3_prefix;
  } else if (form.channel === "email") {
    body.recipients = splitRecipients(form.recipients);
  } else {
    body.host = form.host;
    body.remote_dir = form.remote_dir;
    body.credential = { host: form.host, username: form.username, password: form.password };
  }
  return body;
}

/** Enough filled in to try creating it — the API validates for real. */
export function canCreate(form) {
  if (!String(form.name || "").trim()) return false;
  if (form.channel === "s3") return !!String(form.destination_s3_prefix || "").trim();
  if (form.channel === "email") return splitRecipients(form.recipients).length > 0;
  return !!String(form.host || "").trim() && !!String(form.remote_dir || "").trim();
}

/** Whether a freshly created destination must pass its connection test before
 *  an export may select it. The same rule the picker applies (UIFPE-595): an
 *  unproven email address is the author's own recipient and may be chosen at
 *  once; an unproven SFTP or S3 target means a run that dies at delivery, so
 *  it is tested first. */
export function needsTestBeforeUse(channel) {
  return !selectableWhileUntested({ channel });
}

// ---- the add flow's per-channel decisions (UIFPE-633) ----------------------
// Adding a destination happens in two places: the Destinations screen and the
// wizard's add-destination dialog. They drifted — the dialog tested SFTP/S3
// straight after creating, the screen never did while its button said "Test &
// save". Every "what does this channel do on create" decision lives here, and
// both places ask these functions. `context` is "screen" (the Destinations
// screen) or "export" (the dialog, which also chooses the destination for the
// export being edited).

/** The create button's label. */
export function createActionLabel(channel, context = "screen") {
  if (needsTestBeforeUse(channel)) return "Create & test";
  return context === "export" ? "Create & use" : "Create";
}

/** The create button's label while it works: creating, then testing. */
export function createBusyLabel(phase) {
  return phase === "testing" ? "Testing connection…" : "Creating…";
}

/** What follows a successful create: "test" — run the connection test now;
 *  "policy" — an S3 destination on a partner's own bucket (the API returned
 *  a partner_policy, UIFPE-440): its test cannot pass until the partner has
 *  applied Beacon's bucket policy, so the next step is sending them that,
 *  not a test that must fail (UIFPE-635); "ready" — usable as it is. */
export function nextStepAfterCreate(dest) {
  if (!needsTestBeforeUse(dest?.channel)) return "ready";
  if (dest?.channel === "s3" && dest?.partner_policy) return "policy";
  return "test";
}

/** The one line beside the create button. */
export function createHint(channel, context = "screen") {
  if (context === "export") {
    return needsTestBeforeUse(channel)
      ? "The connection test runs right after saving — the destination is chosen for this export once it passes."
      : "Chosen for this export as soon as it is saved; send it a test email from the Destinations screen whenever you like.";
  }
  // UIFPE-636: true for the chosen channel — only SFTP has a credential, and
  // only SFTP and S3 must pass a test before an export may pick them
  if (channel === "sftp")
    return "The password goes to Secrets Manager and is never kept here. The connection test runs right after creating; exports can use it once it passes.";
  if (channel === "s3")
    return "No credential needed — Beacon's delivery role writes to the bucket. The connection test runs right after creating; a partner's own bucket needs their bucket policy first.";
  return "No credential needed. Exports can use it at once; send it a test email from its row whenever you like.";
}

/** The message after creating a destination that is usable as it is
 *  (nextStepAfterCreate "ready" — email today). UIFPE-636: it used to say
 *  "pending its connection test. Credentials went to Secrets Manager" for
 *  every channel, both false for email. */
export function readyMessage(dest) {
  return `${dest?.name || "Destination"} created — exports can use it now. Send it a test email from its row whenever you like.`;
}
