import { useState, useEffect } from 'react';

interface SyncIndicatorProps {
  dataUpdatedAt: number;
  isFetching: boolean;
}

/**
 * How long ago the schema list was last fetched, shown at the right of the
 * app header — the slot Echo gives to a notification bell and a user chip,
 * neither of which this app can honestly render (see `AppShell`).
 *
 * It moved here from the registry health rail, which 2a's layout does not
 * have. `subjectCount` went with it: the toolbar's count pill now reads
 * `n / m subjects`, and two counts of the same thing in one viewport invite
 * the question of which is right.
 *
 * UX Black on Echo's white header. It was Light Purple when the header was
 * Pearson Purple, which on white measures 1.6:1 — effectively invisible.
 */
export function SyncIndicator({ dataUpdatedAt, isFetching }: SyncIndicatorProps) {
  // A lazy initialiser, not `useState(Date.now())`. Reading the clock in the
  // render body is impure — the same render yields a different tree each time
  // — and it also re-read on every render for a value only the interval
  // changes. An initialiser runs once, which is where React allows a read
  // like this, and the first paint gets the real time rather than a value
  // that has to be corrected a frame later.
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (isFetching) {
    return (
      <span className="select-none whitespace-nowrap text-xs text-ink-soft">Syncing…</span>
    );
  }

  // `dataUpdatedAt` is 0 until the first SUCCESSFUL fetch — that is TanStack
  // Query's sentinel for "never", not a timestamp. Read as one it is the
  // epoch, so with the BFF unreachable this rendered `Synced 498000h ago`
  // directly above the panel telling the reader to check that Apicurio is
  // reachable. Nothing at all is the honest answer: there has been no sync to
  // report, and the failure itself is already stated on the page below.
  if (!dataUpdatedAt) return null;

  const secAgo = Math.max(0, Math.floor((now - dataUpdatedAt) / 1000));

  return (
    <span className="select-none whitespace-nowrap text-xs text-ink-soft">
      Synced {formatAgo(secAgo)}
    </span>
  );
}

function formatAgo(sec: number): string {
  if (sec < 60) return `${sec}s ago`;
  const mins = Math.floor(sec / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}
