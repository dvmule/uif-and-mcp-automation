// DestinationFields (UIFPE-599): the destination form's fields, shared by the
// Destinations screen (add and edit) and the wizard's add-destination dialog.
// One component, so the two places cannot drift — the dialog exists because
// sending the author to the Destinations screen threw away the export they
// were half-way through.
//
// `form` / `set` are the caller's: `set(key)` returns a change handler.
// `editing` is the destination being edited, or null when adding — the
// channel is immutable on an edit (a different channel is a different
// destination), the name is editable since UIFPE-606, and a blank password
// then means "keep the stored one".
//
// UIFPE-699 (approved design): the channel is picked from three cards (AWS S3 ·
// SFTP · Email), who can use it is a two-way switch, and the fields sit in
// pairs. `onShowPolicy`, when given, puts "Show bucket policy" beside the S3
// prefix; `policyNote` says why it is unavailable when it is not given.
import React from "react";
import { Cloud, Mail, Server } from "lucide-react";
import { Field, inputClass } from "./ui.jsx";
import { CHANNEL_ORDER, channelLabel } from "./lib/destinationPicker.js";

const CHANNEL_ICON = { s3: Cloud, sftp: Server, email: Mail };
const CAPTION = "mb-1.5 block text-[14px] font-bold text-ink"; // UIFPE-722: the prototype's label
// UIFPE-722: the popup's roomier fields (12 x 14 px, 15 px text)
const BIG = `${inputClass} px-3.5! py-3! text-[15px]!`;

// a button group drives the same `set(key)` handler an <input> would
const pick = (set, key, value) => () => set(key)({ target: { value } });

export default function DestinationFields({ form, set, channels, editing = null, onShowPolicy = null, policyNote = "" }) {
  // the cards follow the fixed channel order; only channels the platform
  // makes available get one (a planned channel could never be picked), plus
  // an edit's own channel whatever its status
  const ids = [...new Set([...CHANNEL_ORDER, ...channels.map((c) => c.id), ...(editing ? [editing.channel] : [])])].filter(
    (id) => channels.some((c) => c.id === id && c.status === "available") || editing?.channel === id
  );
  const statusOf = (id) => channels.find((c) => c.id === id)?.status;

  return (
    <div className="space-y-4">
      <div role="group" aria-label="Channel">
        <span className={CAPTION}>
          Channel
          <span className="ml-1.5 font-medium normal-case tracking-normal">
            {editing ? "cannot change — a different channel is a different destination" : ""}
          </span>
        </span>
        <div className="grid gap-2.5 sm:grid-cols-3">
          {ids.map((id) => {
            const Icon = CHANNEL_ICON[id] || Cloud;
            const on = form.channel === id;
            const status = statusOf(id);
            const off = editing ? id !== editing.channel : status !== "available";
            return (
              <button
                key={id}
                type="button"
                aria-pressed={on}
                disabled={off}
                onClick={pick(set, "channel", id)}
                title={!editing && status && status !== "available" ? `${channelLabel(id)} is ${status}` : undefined}
                className={`relative flex items-center gap-3 rounded-[14px] border-[1.5px] px-4 py-3.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                  on ? "border-accent bg-accent-soft ring-2 ring-pearson-light-purple/60" : "border-line bg-paper-card hover:border-pearson-light-purple"
                }`}
              >
                {/* UIFPE-722: the prototype's card — a rounded-square icon, a ✓ once chosen */}
                <span className={`grid h-[38px] w-[38px] place-items-center rounded-[10px] ${on ? "bg-accent text-white" : "bg-accent-soft text-accent"}`}>
                  <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
                </span>
                <span className="text-[17px] font-bold text-ink">{channelLabel(id)}</span>
                {on && (
                  <span className="absolute right-2.5 top-2.5 grid h-5 w-5 place-items-center rounded-full bg-accent text-[11px] font-extrabold text-white" aria-hidden="true">
                    ✓
                  </span>
                )}
                {!editing && status && status !== "available" && <span className="ml-auto text-meta text-ink-mute">{status}</span>}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* UIFPE-606: editable on edit too — a label; must be unique */}
        <Field label="Name" hint="must be unique">
          <input className={BIG} value={form.name} onChange={set("name")} placeholder="partner-x-drop" spellCheck={false} />
        </Field>
        {/* UIFPE-546: the author decides who sees it; changeable later */}
        <div role="group" aria-label="Who can use it">
          <span className={CAPTION}>Who can use it</span>
          <div className="inline-flex rounded-full border border-line bg-paper-card p-0.5">
            {[
              ["private", "Private — just me"],
              ["public", "Public — everyone"],
            ].map(([v, l]) => (
              <button
                key={v}
                type="button"
                aria-pressed={form.visibility === v}
                onClick={pick(set, "visibility", v)}
                className={`rounded-full px-3.5 py-1 text-body font-semibold transition-colors ${
                  form.visibility === v ? "bg-pearson-purple text-white" : "text-ink-soft hover:text-ink"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
          <span className="mt-1 block text-meta text-ink-mute">
            {form.visibility === "private" ? "Only you see it in lists and the wizard." : "Everyone can pick it; only you can change it."}
          </span>
        </div>
      </div>

      {form.channel === "s3" && (
        <Field label="Destination prefix" hint={policyNote || "s3://bucket/prefix"}>
          <span className="flex items-center gap-3">
            <input
              className={BIG}
              value={form.destination_s3_prefix}
              onChange={set("destination_s3_prefix")}
              placeholder="s3://partner-bucket/inbound"
              spellCheck={false}
            />
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                onShowPolicy?.();
              }}
              disabled={!onShowPolicy}
              title="The bucket-policy statement the partner adds so Beacon can write to their bucket"
              className="h-8 shrink-0 rounded-full border border-paper-line bg-paper-card px-4 text-body font-semibold text-ink transition-colors hover:border-accent/50 hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-45"
            >
              Show bucket policy
            </button>
          </span>
        </Field>
      )}
      {form.channel === "email" && (
        <Field label="Recipients" hint="comma separated">
          <input className={BIG} value={form.recipients} onChange={set("recipients")} placeholder="partner@example.com, ops@example.com" spellCheck={false} />
        </Field>
      )}
      {form.channel === "sftp" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Host">
            <input className={BIG} value={form.host} onChange={set("host")} placeholder="sftp.partner.example" spellCheck={false} />
          </Field>
          <Field label="Remote directory">
            <input className={BIG} value={form.remote_dir} onChange={set("remote_dir")} placeholder="/inbound" spellCheck={false} />
          </Field>
          <Field label="Username">
            <input className={BIG} value={form.username} onChange={set("username")} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Password" hint={editing ? "leave blank to keep the stored credential" : "stored in Secrets Manager, never here"}>
            {/* UIFPE-631 (owner-approved): masked while typed; new-password keeps the
                browser from saving it or filling in the author's own password */}
            <input
              type="password"
              autoComplete="new-password"
              className={BIG}
              value={form.password}
              onChange={set("password")}
            />
          </Field>
        </div>
      )}
    </div>
  );
}
