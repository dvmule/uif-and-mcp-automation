// DestinationPicker (UIFPE-539): the Output & delivery destination chooser
// for teams with MANY destinations. The wizard keeps its original card grid
// up to PICKER_THRESHOLD (see lib/destinationPicker.js); above that it
// renders this instead — a selected-chips strip that is always visible, a
// search/channel/verified toolbar, dense paged rows, and the managed
// partner-drop pinned on top (it is the exclusive default and must never
// sit on page 4). Selection order is the wizard's destinationIds order —
// the publisher builds delivery targets in that order, so this component
// never reorders anything.
import React, { useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Cloud, Database, Mail, Search, Server, X } from "lucide-react";
import { Button, Chip, EmptyState, Pill, inputClass } from "./ui.jsx";
import {
  PICKER_PAGE_SIZE,
  channelLabel,
  channelsOf,
  filterDestinations,
  isVerified,
  pageBounds,
  selectableWhileUntested,
  statusText,
} from "./lib/destinationPicker.js";

const CHANNEL_ICON = { s3: Cloud, sftp: Server, email: Mail };

function ChannelIcon({ channel }) {
  const Icon = CHANNEL_ICON[channel] ?? Database;
  return <Icon size={14} strokeWidth={1.75} className="shrink-0 text-ink-mute" aria-hidden="true" />;
}

export default function DestinationPicker({ destinations, selectedIds, onToggle, targetFor, currentUser = null }) {
  const [query, setQuery] = useState("");
  const [channel, setChannel] = useState("");
  const [verifiedOnly, setVerifiedOnly] = useState(true);
  const [page, setPage] = useState(0);

  const chosen = selectedIds
    .map((id) => destinations.find((d) => d.id === id))
    .filter(Boolean);
  const channels = useMemo(() => channelsOf(destinations), [destinations]);
  const filtered = useMemo(
    () => filterDestinations(destinations, { query, channel, verifiedOnly }, targetFor),
    [destinations, query, channel, verifiedOnly, targetFor]
  );
  const { page: p, start, end } = pageBounds(filtered.length, page);
  const managedActive = selectedIds.length === 0;

  const retune = (fn) => (v) => {
    fn(v);
    setPage(0);
  };

  return (
    <div>
      {/* the selection, always in sight — filters and paging never hide it */}
      <div className="mb-3 flex min-h-[40px] flex-wrap items-center gap-1.5 rounded-card border border-line bg-paper-head px-3 py-2">
        {chosen.length === 0 ? (
          <span className="text-meta text-ink-mute">
            Nothing picked — the managed partner-drop below is used on its own.
          </span>
        ) : (
          <>
            <span className="text-meta text-ink-soft">
              Delivering to <b className="text-ink">{chosen.length}</b>:
            </span>
            {chosen.map((d) => (
              <span
                key={d.id}
                className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-2.5 py-0.5 text-meta font-medium text-accent"
              >
                <ChannelIcon channel={d.channel} />
                {d.name}
                <button
                  type="button"
                  aria-label={`Remove ${d.name}`}
                  onClick={() => onToggle(d.id)}
                  className="rounded-full hover:bg-accent/15"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onToggle("")}>
              Clear
            </Button>
          </>
        )}
      </div>

      {/* pinned: the managed drop is the exclusive default, never paged away.
          UIFPE-632: a single choice, so it wears the wizard's choice-card disc */}
      <button
        type="button"
        onClick={() => onToggle("")}
        className={`mb-3 flex w-full items-center gap-3 rounded-card border px-3 py-2.5 text-left transition-colors ${
          managedActive
            ? "border-accent bg-paper-card shadow-[inset_3px_0_0_var(--color-accent)]"
            : "border-line bg-paper-card hover:border-paper-line hover:bg-paper-head"
        }`}
      >
        <span
          aria-hidden="true"
          className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-2 ${
            managedActive ? "border-accent bg-accent text-white" : "border-paper-line bg-paper-card"
          }`}
        >
          {managedActive && <Check size={11} strokeWidth={3} />}
        </span>
        <span className="text-body font-semibold text-ink">Managed S3 partner-drop</span>
        <Pill tone="mute">default</Pill>
        <span className="ml-auto text-meta text-ink-mute">used when nothing else is picked</span>
      </button>

      {/* UIFPE-632: toolbar and pager inside the list's frame, like the Exports list */}
      <div className="overflow-hidden rounded-card border border-line bg-paper-card shadow-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
          <div className="relative min-w-[180px] flex-1">
            <Search
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute"
              aria-hidden="true"
            />
            <input
              value={query}
              onChange={(e) => retune(setQuery)(e.target.value)}
              placeholder="Search by name, host, bucket or address"
              aria-label="Search destinations"
              className={`${inputClass} h-8 pl-8`}
            />
          </div>
          <select
            value={channel}
            onChange={(e) => retune(setChannel)(e.target.value)}
            aria-label="Filter by channel"
            className={`${inputClass.replace("w-full", "w-auto")} h-8`}
          >
            <option value="">All channels</option>
            {channels.map((c) => (
              <option key={c} value={c}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
          <label className="flex shrink-0 items-center gap-1.5 text-meta text-ink-soft">
            <input
              type="checkbox"
              checked={verifiedOnly}
              onChange={(e) => retune(setVerifiedOnly)(e.target.checked)}
            />
            Verified only
          </label>
          <span className="ml-auto text-meta text-ink-mute tnum">
            {filtered.length
              ? `${start + 1}–${end} of ${filtered.length}`
              : `0 of ${destinations.length}`}
          </span>
          <Button size="sm" className="w-7 px-0" disabled={p === 0} onClick={() => setPage(p - 1)} aria-label="Previous page">
            <ChevronLeft size={14} />
          </Button>
          <Button
            size="sm"
            className="w-7 px-0"
            disabled={end >= filtered.length}
            onClick={() => setPage(p + 1)}
            aria-label="Next page"
          >
            <ChevronRight size={14} />
          </Button>
        </div>
        {filtered.length === 0 ? (
          <div className="p-4 text-center">
            <EmptyState>
              No destinations match — clear the search, or register a new destination.
            </EmptyState>
          </div>
        ) : (
          filtered.slice(start, end).map((d) => {
            const active = selectedIds.includes(d.id);
            const verified = isVerified(d);
            // an unverified destination cannot be newly selected (it cannot
            // deliver), but one already on an edited version stays removable.
            // UIFPE-595: email is the exception — an unproven address is the
            // author's own recipient, and the failure lands in front of them
            // rather than in a run that dies at delivery.
            const locked = !verified && !active && !selectableWhileUntested(d);
            return (
              <button
                key={d.id}
                type="button"
                onClick={() => !locked && onToggle(d.id)}
                aria-pressed={active}
                disabled={locked}
                title={
                  locked
                    ? "Pass its connection test on the Destinations screen first"
                    : verified
                      ? ""
                      : statusText(d) // UIFPE-595: selectable but unproven
                }
                className={`flex w-full items-center gap-2.5 border-b border-line px-3 py-2 text-left transition-colors last:border-b-0 ${
                  active
                    ? "bg-accent-soft"
                    : locked
                      ? "cursor-not-allowed opacity-50"
                      : "hover:bg-paper-head"
                }`}
              >
                <input type="checkbox" checked={active} readOnly tabIndex={-1} className="pointer-events-none" />
                <ChannelIcon channel={d.channel} />
                <span className={`max-w-[220px] truncate text-body text-ink ${active ? "font-semibold" : ""}`}>
                  {d.name}
                </span>
                <Chip>{channelLabel(d.channel)}</Chip>
                {/* UIFPE-546: a public destination someone else registered */}
                {d.owner && currentUser && d.owner !== currentUser && (
                  <Pill tone="mute" title={`Registered by ${d.owner} — usable by everyone`}>shared</Pill>
                )}
                <span className="min-w-0 flex-1 truncate font-mono text-label text-ink-mute">
                  {targetFor(d)}
                </span>
                {/* UIFPE-595: an unverified row says what it is waiting for,
                    not just that it is unverified */}
                <Pill tone={verified ? "ok" : "warn"} title={verified ? "" : statusText(d)}>
                  {verified ? "verified" : "not tested"}
                </Pill>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
