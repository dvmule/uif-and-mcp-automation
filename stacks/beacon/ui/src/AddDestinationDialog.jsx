// AddDestinationDialog (UIFPE-599): register a destination WITHOUT leaving the
// wizard. "New destination" used to navigate to the Destinations screen; the
// wizard unmounted and every step the author had filled in was gone. This
// dialog collects the same fields on the Output & delivery step, creates the
// destination, and then does what the channel needs before it may be used:
//   email     — usable at once (UIFPE-595: the author's own recipient), so it
//               is selected and the dialog closes
//   sftp / s3 — the connection test runs here, straight after creation; it is
//               selected only when the test passes. A failed test shows the
//               API's reason and leaves the destination created but unchosen,
//               so the author can retry or come back to it later.
import React, { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "./api.js";
import { Button, InfoTip, Note } from "./ui.jsx";
import DestinationFields from "./DestinationFields.jsx";
import {
  EMPTY_FORM,
  canCreate,
  createActionLabel,
  createBody,
  createBusyLabel,
  createHint,
  nextStepAfterCreate,
} from "./lib/destinationForm.js";

export default function AddDestinationDialog({ onClose, onCreated }) {
  const [channels, setChannels] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  // form → creating → (testing → failed)*; a success path closes the dialog
  const [phase, setPhase] = useState("form");
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.channels().then(setChannels).catch(() => {});
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const finish = (dest, select) => {
    onCreated(dest, { select });
    onClose();
  };

  const runTest = async (dest) => {
    setPhase("testing");
    setError(null);
    try {
      const r = await api.testDestination(dest.id);
      if (r.ok) {
        finish({ ...dest, status: "verified", host_key: r.host_key || dest.host_key }, true);
        return;
      }
      setError(r.error || "the connection test failed");
    } catch (e) {
      setError(e.message);
    }
    setPhase("failed");
  };

  const submit = async () => {
    setError(null);
    setPhase("creating");
    try {
      const dest = await api.addDestination(createBody(form));
      setCreated(dest);
      // UIFPE-633: the shared rule decides what follows a create
      const next = nextStepAfterCreate(dest);
      if (next === "test") await runTest(dest);
      // UIFPE-635: a partner's own bucket — its test cannot pass yet, so the
      // policy comes first; kept, not chosen for the export
      else if (next === "policy") setPhase("policy");
      else finish(dest, true);
    } catch (e) {
      setError(e.message);
      setPhase("form");
    }
  };

  const busy = phase === "creating" || phase === "testing";
  const [copied, setCopied] = useState(null); // UIFPE-635: "ok" | "fail"

  const copyPolicy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(created.partner_policy.statement, null, 2));
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
  };

  // UIFPE-631 (owner-approved): Escape closes the dialog under the same rule
  // as a click outside — never while it is creating or testing
  useEffect(() => {
    if (busy) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return (
    // UIFPE-719: the Destinations screen's popup — 1040px, the heading with its
    // ⓘ, the same fields, a tinted footer with the actions on the right. The
    // wizard's own flow (create, test, select) is unchanged.
    <div
      className="motion-fade-in fixed inset-0 z-50 flex items-center justify-center bg-pearson-purple/45 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Add destination"
      onClick={busy ? undefined : onClose}
    >
      <div
        className="motion-pop-in flex max-h-[90vh] w-full max-w-[1040px] flex-col overflow-hidden rounded-card border border-line bg-paper-card shadow-[0_24px_48px_-20px_rgb(13_0_77_/_0.55)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-6 py-4">
          <div className="flex items-center gap-1.5">
            <h2 className="text-[18px] font-extrabold tracking-tight text-ink">Add destination</h2>
            <InfoTip
              label="About destinations"
              text="A destination you own, registered without leaving this export. An SFTP password goes to Secrets Manager and Beacon keeps only a reference to it. AWS S3 and email need no credential."
            />
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy} aria-label="Close">
            <X size={15} />
          </Button>
        </div>
        <div className="flex min-h-0 flex-col overflow-y-auto">

        {/* UIFPE-635: an external bucket — the partner's policy, then the test */}
        {phase === "policy" && created && (
          <>
            <div className="px-6 py-5">
              <Note tone="warn">
                <b>{created.name}</b> was created. It is on the partner's own bucket, so its
                connection test can only pass after they add Beacon's bucket policy.
              </Note>
              <p className="mt-2 text-body text-ink-soft">
                Send the partner this statement for their bucket policy (S3 → bucket → Permissions →
                Bucket policy). It is not selected for this export yet — test it once they confirm.
              </p>
              <pre className="mt-2 max-h-56 overflow-auto rounded-card bg-pearson-purple px-4 py-3 font-mono text-[11px] leading-relaxed text-[#e6e3ff]">
                {JSON.stringify(created.partner_policy?.statement, null, 2)}
              </pre>
              {created.partner_policy?.note && (
                <p className="mt-2 text-meta leading-relaxed text-ink-soft">{created.partner_policy.note}</p>
              )}
              {copied === "ok" && <Note tone="ok">Copied — send it to the partner.</Note>}
              {copied === "fail" && <Note tone="fail">Could not copy — select the JSON and copy it manually.</Note>}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper-head px-6 py-3">
              <Button onClick={copyPolicy}>Copy policy JSON</Button>
              <Button onClick={() => finish(created, false)}>Keep it, choose later</Button>
              <Button variant="primary" onClick={() => runTest(created)}>Test now</Button>
            </div>
          </>
        )}

        {phase !== "failed" && phase !== "policy" && (
          <>
            <div className="px-6 py-5">
              {/* UIFPE-699: the policy needs the saved destination, so it follows the create */}
              <DestinationFields
                form={form}
                set={set}
                channels={channels}
                policyNote="s3://bucket/prefix — a partner's own bucket gets its policy right after you create it"
              />
              {error && phase === "form" && <Note tone="fail">{error}</Note>}
              {phase === "testing" && (
                <Note tone="mute">
                  Saved. Testing the connection now — for SFTP this connects from the delivery IPs
                  and checks the folder is writable; for AWS S3 it writes and removes a tiny test file.
                </Note>
              )}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper-head px-6 py-3">
              <span className="mr-auto max-w-[44ch] text-meta text-ink-mute">
                {createHint(form.channel, "export")}
              </span>
              <Button onClick={onClose} disabled={busy}>Cancel</Button>
              <Button variant="primary" className="whitespace-nowrap" onClick={submit} busy={busy} disabled={!canCreate(form) || busy}>
                {busy ? createBusyLabel(phase) : createActionLabel(form.channel, "export")}
              </Button>
            </div>
          </>
        )}

        {phase === "failed" && created && (
          <>
            <div className="px-6 py-5">
              <Note tone="fail">
                <b>{created.name}</b> was created, but its connection test failed — {error}
              </Note>
              {created.partner_policy && (
                <Note tone="warn">
                  This is an external bucket: it will keep failing until the partner has applied
                  the bucket policy. Open it on the Destinations screen and press <b>Show bucket policy</b>,
                  send that JSON to the partner, and test again once they confirm.
                </Note>
              )}
              <p className="mt-2 text-body text-ink-soft">
                It is not selected for this export. Fix the target and test again, or keep it and
                pick it later from the Destinations screen once it passes.
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper-head px-6 py-3">
              <Button onClick={() => finish(created, false)}>Keep it, choose later</Button>
              <Button variant="primary" onClick={() => runTest(created)}>Test again</Button>
            </div>
          </>
        )}
        </div>
      </div>
    </div>
  );
}
