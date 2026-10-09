import { useMemo, useState } from 'react';
import { DiffEditor } from '@monaco-editor/react';
import { useSchemaDetail } from '../hooks/use-schema-detail';
import { compareVersionAge, resolveCurrentVersion } from '../utils';
import { getMonacoLanguage } from '../utils';
import type { SchemaVersionRead } from '../types/schema.types';

interface VersionTimelineProps {
  groupId: string;
  artifactId: string;
  versions: SchemaVersionRead[];
  currentVersion: string;
  /**
   * Content already fetched for `currentVersion` by the caller (the subject page
   * always has this — it's `detail.content`). When supplied, the diff's right side
   * uses it directly instead of re-fetching a version the page already has in
   * memory. Omit it and the timeline fetches `currentVersion` itself, so it still
   * works when mounted standalone.
   */
  currentContent?: string;
}

/** UTC calendar date, or null for a missing or unparseable `created_on`. */
function formatCreatedOn(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10);
}

export function VersionTimeline({
  groupId,
  artifactId,
  versions,
  currentVersion,
  currentContent,
}: VersionTimelineProps) {
  const [compareTo, setCompareTo] = useState<string>('');
  const comparing = Boolean(compareTo);

  // The BFF echoes `content_version: "latest"` — a sentinel, not a version —
  // when the detail is fetched without an explicit version, which is how the
  // subject page fetches it. Taken literally that rendered "Compare vlatest
  // against" and, because `olderVersions` filters on this value, left the
  // current version in its own compare list: a diff of a version against
  // itself. Resolve it to the newest real version.
  //
  // By created_on, not by position: Apicurio returns versions oldest-first
  // here but the order is not part of the contract, so neither end of the
  // array is reliably the newest.
  const resolvedVersion = useMemo(
    () => resolveCurrentVersion(versions, currentVersion),
    [versions, currentVersion],
  );

  // The page's own version selector and `compareTo` are independent state. If the
  // caller moves `currentVersion` onto whatever the user had picked to compare
  // against, clear the selection — otherwise the <select> would hold a value no
  // longer among its own (current-version-excluding) options, and the diff would
  // silently compare the current version against itself.
  // During render rather than in an effect, so the <select> never paints once
  // holding a value that is no longer among its own options.
  const [prevResolved, setPrevResolved] = useState(resolvedVersion);
  if (prevResolved !== resolvedVersion) {
    setPrevResolved(resolvedVersion);
    setCompareTo('');
  }

  // `useSchemaDetail` is the one hook for this resource. It used to be wrapped a
  // second time here under a `['schemas', …, 'content', version]` key, which
  // fetched the same version twice and — worse — left half the cache invisible
  // to `use-register-schema`'s `['schemas']` invalidation.
  const left = useSchemaDetail(groupId, artifactId, compareTo || undefined, {
    enabled: comparing,
  });

  // Only fetch the current version's content when the caller hasn't already
  // supplied it — see the `currentContent` doc comment above.
  const needsRightFetch = !currentContent;
  const right = useSchemaDetail(groupId, artifactId, resolvedVersion, {
    enabled: needsRightFetch && comparing,
  });
  const rightContent = currentContent ?? right.data?.content;
  const rightPending = needsRightFetch && right.isLoading;
  const rightFailed = needsRightFetch && right.isError;

  // Every version except the one on screen. It was called `olderVersions`,
  // which is only true while the newest version is the one being viewed: with
  // `?version=1` pinned, this list is v2 and v3 — both NEWER.
  const otherVersions = versions.filter((v) => v.version !== resolvedVersion);

  /**
   * The two sides of the diff, oldest on the left.
   *
   * They used to be fixed: the chosen version was always `original` and the
   * version on screen always `modified`. With a non-latest version pinned
   * that is backwards — choosing v3 while viewing v1 put v3 on the left, so a
   * field ADDED in v3 rendered as a deletion and the diff read as a rollback
   * of work that had actually happened.
   */
  const chosen = versions.find((v) => v.version === compareTo);
  const onScreen = versions.find((v) => v.version === resolvedVersion);
  const chosenIsOlder =
    chosen && onScreen ? compareVersionAge(chosen, onScreen) <= 0 : true;
  const diffPending = comparing && (left.isLoading || rightPending);
  const diffFailed = comparing && (left.isError || rightFailed);

  return (
    <div className="space-y-4">
      <ol className="divide-y divide-paper-line" data-testid="version-timeline-list">
        {versions.map((v) => {
          const createdOn = formatCreatedOn(v.created_on);
          return (
            <li key={v.version} className="flex items-baseline gap-3 py-2.5">
              <span className="w-8 shrink-0 font-mono text-xs font-semibold text-accent">v{v.version}</span>
              <div className="min-w-0 flex-1">
                <p className="text-xs text-ink">
                  {v.created_by ?? 'unknown'}
                  {createdOn && <span className="text-ink-mute"> · {createdOn}</span>}
                </p>
                <p className="text-[11px] text-ink-mute">{v.state}</p>
              </div>
              {v.version === resolvedVersion && (
                // The one uppercase string in the product, and the only place
                // besides the header product name with non-zero letter-spacing.
                <span
                  className="shrink-0 rounded bg-paper-line px-1.5 py-0.5 text-[10px] font-semibold uppercase text-ink-soft"
                  style={{ letterSpacing: '.05em' }}
                >
                  Current
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {/* A select with nothing in it but its own placeholder is a dead
        * control that reads as "comparison is broken". Say what is actually
        * true instead. */}
      {otherVersions.length === 0 && (
        <p className="text-xs text-ink-mute">
          One version so far, so there is nothing to compare against yet.
        </p>
      )}

      {otherVersions.length > 0 && (
        <div>
          <label htmlFor="version-compare" className="block text-xs font-medium text-ink">
            Compare v{resolvedVersion} against
          </label>
          <select
            id="version-compare"
            value={compareTo}
            onChange={(e) => setCompareTo(e.target.value)}
            className="mt-1 h-8 rounded border border-paper-line px-2 text-xs"
          >
            <option value="">Choose a version…</option>
            {otherVersions.map((v) => (
              <option key={v.version} value={v.version}>v{v.version}</option>
            ))}
          </select>
        </div>
      )}

      {diffPending && <p className="text-xs text-ink-mute">Loading comparison…</p>}

      {diffFailed && (
        <p className="text-xs text-signal-fail">
          Could not load the comparison. {left.isError ? `v${compareTo}` : `v${resolvedVersion}`} failed to load.
        </p>
      )}

      {comparing && left.data && rightContent && (
        <div className="h-80 overflow-hidden rounded border border-paper-line">
          <DiffEditor
            // Oldest on the left, whichever side it came from, so additions
            // read as additions.
            original={chosenIsOlder ? left.data.content : rightContent}
            modified={chosenIsOlder ? rightContent : left.data.content}
            language={getMonacoLanguage(left.data.type ?? '', left.data.content)}
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
            }}
          />
        </div>
      )}
    </div>
  );
}
