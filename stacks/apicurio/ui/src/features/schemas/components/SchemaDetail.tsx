import { useState, useMemo, useEffect, useRef, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  ArrowLeft,
  TableProperties,
  Code,
  Network,
  Search,
  X,
  ChevronUp,
  ChevronDown,
  Copy,
  Check,
  Maximize2,
  CircleAlert,
  Link2,
  Download,
} from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { useSchemaDetail } from '../hooks/use-schema-detail';
import { useSchemaServices } from '../hooks/use-schema-services';
import { useNonCompliant } from '../hooks/use-non-compliant';
import { useSchemaUrlState } from '../hooks/use-schema-url-state';
import {
  DETAIL_TAB_ORDER,
  DETAIL_TAB_LABELS,
  SCHEMA_VIEW_ORDER,
  SCHEMA_VIEW_LABELS,
} from '../hooks/use-schema-url-state';
import type { SchemaViewMode } from '../hooks/use-schema-url-state';
import {
  resolveCurrentVersion,
  splitSubject,
  DOMAIN_COLOURS,
  downloadSchema,
  schemaFile,
} from '../utils';
import {
  parseSchemaBody,
  countFieldViewMatches,
  countGraphMatches,
} from '../schema-body';
import { parseMappingBody } from '../mapping-body';
import { SchemaMappingView } from './SchemaMappingView';
import { SchemaViewer } from './SchemaViewer';
import { findCodeMatches, displayedContent } from '../code-view';
import { SchemaFieldsView } from './SchemaFieldsView';
import { SchemaGraphView } from './SchemaGraphView';
import { VersionTimeline } from './VersionTimeline';
import { ComplianceBadge } from './ComplianceBadge';
import type {
  DataClassification,
  ValidationResult,
  SchemaServicesRead,
  SchemaServiceRef,
} from '../types/schema.types';

const classificationTone: Record<DataClassification, 'neutral' | 'accent' | 'warn'> = {
  public: 'neutral',
  internal: 'accent',
  confidential: 'warn',
  restricted: 'warn',
};

const VIEW_ICONS: Record<SchemaViewMode, typeof Code> = {
  // lucide-react 1.11 has no `AlignJustify`, which the design names for the
  // field view. `TableProperties` is the nearest glyph and the more literal
  // one — this view IS a field table. `Network` is the three-node glyph.
  fields: TableProperties,
  code: Code,
  graph: Network,
};

interface SchemaDetailProps {
  groupId: string;
  artifactId: string;
}

/** 150ms, per the design. Short enough to feel live, long enough that the
  * graph is not relaid out on every keystroke. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function SchemaDetail({ groupId, artifactId }: SchemaDetailProps) {
  const { state, set } = useSchemaUrlState();
  const { pathname, search } = useLocation();
  // The version lives in the URL, so the link in the address bar — and the one
  // the copy button hands you — names the version on screen.
  const { data, isLoading, error } = useSchemaDetail(groupId, artifactId, state.version ?? undefined);

  // The same query key the list page uses, so this is served from cache on a
  // row click rather than re-fetched.
  const {
    data: complianceData,
    isLoading: complianceLoading,
    isError: complianceError,
  } = useNonCompliant();

  const {
    data: servicesData,
    isLoading: servicesLoading,
    isError: servicesError,
  } = useSchemaServices(groupId, artifactId, { enabled: state.detailTab === 'services' });

  const [rawQuery, setRawQuery] = useState('');
  const query = useDebounced(rawQuery, 150);
  const [activeMatch, setActiveMatch] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  /**
   * Whether the reader has picked a tab themselves.
   *
   * Needed because `set({ detailTab: 'schema' })` DELETES the param — `schema`
   * is the URL default — so the URL alone cannot tell "the reader chose
   * Schema" from "the reader chose nothing", and a mapping defaults to the
   * Mapping tab. Without this, clicking Schema on a mapping did nothing and
   * its Code view was unreachable, which design spec §3.2 keeps for one.
   */
  const [tabPicked, setTabPicked] = useState(false);
  const findRef = useRef<HTMLInputElement>(null);

  // Reset during render, not in an effect. React's own guidance for
  // "adjust state when something changes": an effect runs AFTER paint, so for
  // one frame the stepper read "3 of 5" against a query that had just
  // changed. `react-hooks/set-state-in-effect` flags the effect form.
  const matchScope = `${query}\u0000${state.schemaView}`;
  const [prevMatchScope, setPrevMatchScope] = useState(matchScope);
  if (prevMatchScope !== matchScope) {
    setPrevMatchScope(matchScope);
    setActiveMatch(0);
  }

  const complianceResult: ValidationResult | undefined = useMemo(
    () =>
      complianceData?.items.find(
        (r) => r.artifact_id === artifactId && r.group_id === groupId,
      ),
    [complianceData, artifactId, groupId],
  );

  const body = useMemo(() => parseSchemaBody(data?.content ?? ''), [data?.content]);

  // A mapping is a different body from a schema: no fields, no properties, no
  // metadata block. `parseSchemaBody` finds nothing in one, so the Mapping tab
  // is the only reading of it that means anything. Design spec §3.2.
  const mapping = useMemo(() => parseMappingBody(data?.content ?? ''), [data?.content]);

  // Matched against the body as the EDITOR shows it, not as the registry
  // stored it. A minified body is reformatted for display, and matching the
  // raw string put the rail's line numbers, the match count and the stepper
  // on a different document from the one on screen.
  const codeMatches = useMemo(
    () => findCodeMatches(displayedContent(data?.type ?? '', data?.content ?? ''), query),
    [data?.type, data?.content, query],
  );

  if (isLoading) {
    return <p className="text-sm text-ink-mute">Loading schema…</p>;
  }

  if (error || !data) {
    return (
      <div className="space-y-2">
        <p className="text-sm font-medium text-ink">Could not load schema</p>
        <p className="font-mono text-sm text-signal-fail">
          {(error as Error)?.message ?? 'Unknown error'}
        </p>
      </div>
    );
  }

  // `content_version` can be the literal sentinel "latest". Resolve it once so
  // the selector, the viewer and the timeline all name the same version.
  const currentVersion = resolveCurrentVersion(data.versions, data.content_version);

  // The tab is offered ONLY for a mapping. A tab that is empty for twelve of
  // thirteen artifacts is worse than no tab.
  const visibleTabs = DETAIL_TAB_ORDER.filter((t) => t !== 'mapping' || mapping.isMapping);

  // Landing on Mapping is a DEFAULT, not a lock: `?dtab=` still wins, and so
  // does a click. The URL state's default is `schema`, so an untouched URL
  // reads as `schema` here and that is the case being redirected — a
  // deliberate `?dtab=schema` on a mapping is indistinguishable from no param
  // at all, and the design calls the default the more useful of the two.
  const hasExplicitTab = tabPicked || search.includes('dtab=');
  const activeTab =
    mapping.isMapping && !hasExplicitTab ? 'mapping' : state.detailTab;

  // Fields finds no fields or properties in a mapping and Graph draws its
  // columns as anonymous objects, so Code is the only honest reading here.
  const visibleSchemaViews = mapping.isMapping
    ? SCHEMA_VIEW_ORDER.filter((v) => v === 'code')
    : SCHEMA_VIEW_ORDER;
  const suppressedView = mapping.isMapping && state.schemaView !== 'code';
  const { base: subjectBase, suffix: subjectSuffix } = splitSubject(data.artifact_id);
  const canCompare = data.versions.length > 1;

  // The compliance query failing is not compliance. `complianceResult` is
  // undefined both when the schema passed and when nothing came back, so the
  // error flag is what separates them.
  const passed = complianceLoading || complianceError ? undefined : !complianceResult;
  const failureCount = complianceResult?.failures.length ?? 0;

  const matchLabel = (() => {
    if (!query) return null;
    if (state.schemaView === 'code') {
      return codeMatches.length === 0
        ? 'No matches'
        : `${activeMatch + 1} of ${codeMatches.length}`;
    }
    if (state.schemaView === 'fields') {
      const n = countFieldViewMatches(body, query);
      return `${n} match${n === 1 ? '' : 'es'} in fields and metadata`;
    }
    const n = countGraphMatches(body.value, query);
    return `${n} node${n === 1 ? '' : 's'}`;
  })();

  const step = (delta: number) => {
    if (codeMatches.length === 0) return;
    setActiveMatch((i) => (i + delta + codeMatches.length) % codeMatches.length);
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(data.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  /**
   * An absolute link to exactly this page — subject, version, tab and view.
   *
   * Built from the router's location rather than `window.location.href` so it
   * is the same string in a test as in a browser, and so it cannot pick up a
   * hash or a param the router does not know about.
   */
  const deepLink = `${window.location.origin}${pathname}${search}`;

  const handleCopyLink = async () => {
    await navigator.clipboard.writeText(deepLink);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 1500);
  };

  // `currentVersion`, not `state.version`: the file is named for the version on
  // screen, which is a real version number even when the URL pins nothing and
  // the registry answered with the `latest` sentinel.
  const { name: downloadName } = schemaFile(
    data.type,
    data.content,
    data.artifact_id,
    currentVersion,
  );

  const handleDownload = () =>
    downloadSchema(data.type, data.content, data.artifact_id, currentVersion);

  return (
    <div className="flex flex-col" style={{ gap: 16 }}>
      <Link
        to="/schemas"
        className="flex items-center gap-1.5 self-start text-[13px] font-semibold text-accent hover:underline"
      >
        <ArrowLeft size={15} />
        Back to the registry
      </Link>

      {/* ----------------------------------------------------------- title */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="font-mono text-2xl font-semibold text-ink break-all">
              {subjectBase}
            </h1>
            {subjectSuffix && <Badge tone="neutral">{subjectSuffix}</Badge>}
            <Badge tone="neutral">{data.type}</Badge>
            <Badge tone="neutral">{data.state}</Badge>
            <ComplianceBadge
              passed={passed}
              loading={complianceLoading}
              error={complianceError}
            />
          </div>
          <p className="mt-1 text-[13px] text-ink-mute">Group: {data.group_id}</p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <label className="sr-only" htmlFor="version-select">
            Version
          </label>
          <select
            id="version-select"
            value={currentVersion}
            onChange={(e) => set({ version: e.target.value })}
            className="border border-light bg-paper-card font-mono text-[13px] text-ink"
            style={{ height: 36, borderRadius: 4, padding: '0 8px' }}
          >
            {data.versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} — {v.state}
              </option>
            ))}
          </select>

          {/* The version pinned in the URL is carried through, so Update edits
            * the version on screen rather than silently jumping to the latest.
            * Nothing is appended when no version is pinned -- the edit page
            * then reads whatever the registry calls current, which is what the
            * page without a `?version=` is showing. */}
          <Link
            to={`${pathname}/edit${state.version ? `?version=${encodeURIComponent(state.version)}` : ''}`}
            className="flex items-center shrink-0 border border-ink text-[13px] font-semibold text-ink whitespace-nowrap"
            style={{ height: 36, borderRadius: 999, padding: '0 18px' }}
          >
            Update schema
          </Link>

          <button
            type="button"
            disabled={!canCompare}
            onClick={() => set({ detailTab: 'history' })}
            title={
              canCompare
                ? 'Compare this version against an earlier one'
                : 'There is only one version, so there is nothing to compare against'
            }
            className={cn(
              'shrink-0 text-[13px] font-semibold whitespace-nowrap',
              canCompare
                ? 'border border-ink text-ink'
                : 'bg-paper text-ink-mute cursor-not-allowed',
            )}
            style={{ height: 36, borderRadius: 999, padding: '0 18px' }}
          >
            Compare versions
          </button>
        </div>
      </div>

      {/* ----------------------------------------------------- facts band */}
      <div
        className="grid bg-paper-card border border-paper-line"
        style={{
          gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
          gap: '16px 24px',
          borderRadius: 16,
          padding: '16px 20px',
        }}
      >
        <Fact label="Domain">
          {data.domain ? (
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="shrink-0"
                style={{
                  width: 3,
                  height: 12,
                  backgroundColor: DOMAIN_COLOURS[data.domain] ?? 'transparent',
                }}
              />
              {data.domain}
            </span>
          ) : null}
        </Fact>
        <Fact label="Classification">
          {data.classification ? (
            <Badge tone={classificationTone[data.classification]}>{data.classification}</Badge>
          ) : (
            <Badge tone="warn">not set</Badge>
          )}
        </Fact>
        <Fact label="Lifecycle">
          {data.lifecycle ? (
            <Badge tone={data.lifecycle === 'deprecated' ? 'warn' : 'neutral'}>{data.lifecycle}</Badge>
          ) : null}
        </Fact>
        <Fact label="Compatibility">
          {/* 0 of 12 subjects have one. Until this reads something other than
            * `not set`, publishing a breaking change here registers silently. */}
          {data.compatibility ? (
            <Badge tone="neutral">{data.compatibility}</Badge>
          ) : (
            <Badge tone="warn">not set</Badge>
          )}
        </Fact>
        <Fact label="Owner CI">{data.owner.service}</Fact>
        {/* These two come from the artifact's own Apicurio properties, which
          * are unset even when the body's metadata block carries values. The
          * Fields view shows the block; this shows the properties. They are
          * allowed to disagree, and the disagreement is the finding — do not
          * synthesise these from the body to make them match. */}
        <Fact label="Owner team">{data.owner.team}</Fact>
        <Fact label="PII fields">
          {data.pii_fields.length > 0 ? data.pii_fields.join(', ') : null}
        </Fact>
        <Fact label="Current version">v{currentVersion}</Fact>
      </div>

      {/* ------------------------------------------------------- tab row */}
      <div className="flex items-end gap-4 border-b border-paper-line" role="tablist">
        {visibleTabs.map((tab) => {
          const isActive = tab === activeTab;
          const showBadge = tab === 'compliance' && failureCount > 0;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => {
                setTabPicked(true);
                set({ detailTab: tab });
              }}
              className={cn(
                'flex items-center gap-1.5 text-sm -mb-px border-b-2 whitespace-nowrap',
                isActive
                  ? 'border-ink text-ink font-semibold'
                  : 'border-transparent text-ink-mute font-medium',
              )}
              style={{ padding: '8px 4px 11px' }}
            >
              {DETAIL_TAB_LABELS[tab]}
              {showBadge && <Badge tone="fail">{failureCount}</Badge>}
            </button>
          );
        })}
      </div>

      {activeTab === 'schema' && (
        <div
          className="grid items-start"
          style={{
            // The graph is a canvas — it takes the full width, and the
            // compatibility note moves below it rather than squeezing it.
            gridTemplateColumns: state.schemaView === 'graph' ? 'minmax(0,1fr)' : 'minmax(0,1fr) 296px',
            gap: 20,
          }}
        >
          <div className="min-w-0 flex flex-col" style={{ gap: 12 }}>
            {/* view toggle · search · toolbar */}
            <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
              <div
                role="tablist"
                aria-label="How to read the schema"
                className="flex shrink-0 bg-paper"
                style={{ borderRadius: 999, padding: 3 }}
              >
                {visibleSchemaViews.map((mode) => {
                  const Icon = VIEW_ICONS[mode];
                  const isActive = mode === state.schemaView;
                  return (
                    <button
                      key={mode}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      onClick={() => set({ schemaView: mode })}
                      className={cn(
                        'flex items-center gap-1.5 text-[13px] whitespace-nowrap',
                        isActive
                          ? 'bg-accent text-white font-semibold'
                          : 'bg-transparent text-ink-soft font-medium',
                      )}
                      style={{ borderRadius: 999, padding: '7px 15px' }}
                    >
                      <Icon size={14} />
                      {SCHEMA_VIEW_LABELS[mode]}
                    </button>
                  );
                })}
              </div>

              <FindField
                inputRef={findRef}
                value={rawQuery}
                onChange={setRawQuery}
                matchLabel={matchLabel}
                showStepper={state.schemaView === 'code' && codeMatches.length > 0}
                onStep={step}
              />

              <button
                type="button"
                onClick={handleCopy}
                aria-label="Copy the schema body"
                title="Copy the schema body"
                className="flex items-center justify-center shrink-0 border border-light text-accent"
                style={{ width: 34, height: 34, borderRadius: 4 }}
              >
                {copied ? <Check size={15} className="text-signal-ok-bold" /> : <Copy size={15} />}
              </button>
              <button
                type="button"
                onClick={handleDownload}
                aria-label="Download this version"
                title={`Download ${downloadName}`}
                className="flex items-center justify-center shrink-0 border border-light text-accent"
                style={{ width: 34, height: 34, borderRadius: 4 }}
              >
                <Download size={15} />
              </button>
              <button
                type="button"
                onClick={handleCopyLink}
                aria-label="Copy a link to this version"
                title="Copy a link to this version"
                className="flex items-center justify-center shrink-0 border border-light text-accent"
                style={{ width: 34, height: 34, borderRadius: 4 }}
              >
                {linkCopied ? <Check size={15} className="text-signal-ok-bold" /> : <Link2 size={15} />}
              </button>
              {/* Code and Fields render a fullscreen overlay; the graph has
                * its own toolbar and does not. A button that does nothing at
                * all is worse than no button. */}
              {state.schemaView !== 'graph' && (
                <button
                  type="button"
                  onClick={() => setIsFullscreen(true)}
                  aria-label="Expand to fullscreen"
                  title="Expand to fullscreen"
                  className="flex items-center justify-center shrink-0 border border-light text-accent"
                  style={{ width: 34, height: 34, borderRadius: 4 }}
                >
                  <Maximize2 size={15} />
                </button>
              )}
            </div>

            {!suppressedView && state.schemaView === 'fields' && (
              <div className="bg-paper-card border border-paper-line overflow-hidden" style={{ borderRadius: 16 }}>
                <SchemaFieldsView
                  body={body}
                  query={query}
                  isFullscreen={isFullscreen}
                  onFullscreenChange={setIsFullscreen}
                />
              </div>
            )}

            {state.schemaView === 'code' && (
              <div
                className="grid items-start"
                style={{ gridTemplateColumns: 'minmax(0,1fr) 180px', gap: 12 }}
              >
                <div
                  className="min-w-0 bg-paper-card border border-paper-line overflow-hidden"
                  style={{ borderRadius: 8, height: 570 }}
                >
                  <SchemaViewer
                    schemaType={data.type}
                    content={data.content}
                    query={query}
                    activeMatch={activeMatch}
                    onRequestFind={() => findRef.current?.focus()}
                    compact
                    isFullscreen={isFullscreen}
                    onFullscreenChange={setIsFullscreen}
                  />
                </div>
                <MatchRail
                  matches={codeMatches}
                  active={activeMatch}
                  query={query}
                  onSelect={setActiveMatch}
                />
              </div>
            )}

            {!suppressedView && state.schemaView === 'graph' && (
              <SchemaGraphView
                body={body}
                content={data.content}
                subject={data.artifact_id}
                version={currentVersion}
                query={query}
              />
            )}

            {/* A stale link, answered with the reason. An empty Fields table
              * would assert this artifact has no columns, which is the one
              * thing it certainly does have. */}
            {suppressedView && (
              <div className="bg-paper-card border border-paper-line p-5"
                   style={{ borderRadius: 16 }}>
                <p className="text-sm text-ink-mute">
                  This artifact is a gold attribute mapping, not a schema — it declares{' '}
                  <code className="font-mono">columns</code>, not{' '}
                  <code className="font-mono">fields</code> or{' '}
                  <code className="font-mono">properties</code>, so there is nothing for this
                  view to tabulate or draw.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setTabPicked(true);
                    set({ detailTab: 'mapping' });
                  }}
                  className="mt-3 text-sm font-medium text-accent underline"
                >
                  Open the Mapping tab
                </button>
              </div>
            )}
          </div>

          {state.schemaView !== 'graph' && <CompatibilityRail />}
        </div>
      )}

      {activeTab === 'mapping' && mapping.isMapping && (
        <SchemaMappingView
          mapping={mapping}
          kind={state.mappingKind}
          onKindChange={(kind) => set({ mappingKind: kind })}
          subject={data.artifact_id}
          version={currentVersion}
        />
      )}

      {!suppressedView && state.schemaView === 'graph' && activeTab === 'schema' && (
        <div style={{ maxWidth: 640 }}>
          <CompatibilityRail />
        </div>
      )}

      {activeTab === 'compliance' && (
        <ComplianceTab result={complianceResult} isError={complianceError} isLoading={complianceLoading} />
      )}

      {activeTab === 'services' && (
        <ServicesTab
          data={servicesData}
          isLoading={servicesLoading}
          isError={servicesError}
        />
      )}

      {activeTab === 'history' && (
        <HistoryTab
          groupId={groupId}
          artifactId={artifactId}
          versions={data.versions}
          currentVersion={currentVersion}
          currentContent={data.content}
        />
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  // An em-dash, not a blank. A blank cell in a facts band reads as "nothing to
  // say here"; the dash says the field exists and is unset.
  const empty = children === null || children === undefined || children === '';
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-mute">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] text-ink break-all">
        {empty ? <span className="text-ink-mute">—</span> : children}
      </dd>
    </div>
  );
}

function FindField({
  inputRef,
  value,
  onChange,
  matchLabel,
  showStepper,
  onStep,
}: {
  inputRef: React.RefObject<HTMLInputElement>;
  value: string;
  onChange: (v: string) => void;
  matchLabel: string | null;
  showStepper: boolean;
  onStep: (delta: number) => void;
}) {
  return (
    <div
      className="flex items-center gap-2 flex-1 min-w-[240px] bg-paper-card border border-light focus-within:border-accent"
      style={{ height: 34, borderRadius: 4, padding: '0 10px' }}
    >
      <Search size={14} className="shrink-0 text-ink-mute" aria-hidden />
      <input
        ref={inputRef}
        type="text"
        aria-label="Find in this schema"
        placeholder="Find a field, key or value…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onStep(e.shiftKey ? -1 : 1);
          }
          if (e.key === 'Escape') {
            e.preventDefault();
            onChange('');
          }
        }}
        // `min-w-[72px]`, not `min-w-0`. The match label beside it was
        // `shrink-0`, so once a query produced a long one — "2 matches in
        // fields and metadata" — the input was the only thing left that could
        // give, and it was squeezed to zero width: the find field disappeared
        // while you were typing in it. The field you are using wins, and the
        // label truncates with its full text on hover.
        className="flex-1 basis-0 min-w-[72px] bg-transparent font-mono text-[13px] text-ink placeholder:text-ink-mute placeholder:font-sans focus:outline-none"
      />
      {matchLabel && (
        <span title={matchLabel} className="min-w-0 truncate text-xs text-ink-mute">
          {matchLabel}
        </span>
      )}
      {showStepper && (
        <>
          <button
            type="button"
            onClick={() => onStep(-1)}
            aria-label="Previous match"
            className="shrink-0 text-accent"
          >
            <ChevronUp size={14} />
          </button>
          <button
            type="button"
            onClick={() => onStep(1)}
            aria-label="Next match"
            className="shrink-0 text-accent"
          >
            <ChevronDown size={14} />
          </button>
        </>
      )}
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear the find query"
          className="shrink-0 text-ink-mute"
        >
          <X size={13} />
        </button>
      )}
      {/* Marks this field as the replacement for Monaco's find widget, which
        * is suppressed so the key reaches here and works in all three views. */}
      <kbd
        aria-hidden
        className="shrink-0 font-mono text-[10px] leading-none text-ink-mute select-none border border-paper-line"
        style={{ borderRadius: 4, padding: '3px 5px' }}
      >
        ⌘F
      </kbd>
    </div>
  );
}

function MatchRail({
  matches,
  active,
  query,
  onSelect,
}: {
  matches: { line: number; text: string }[];
  active: number;
  query: string;
  onSelect: (i: number) => void;
}) {
  if (!query) {
    return (
      <p className="text-xs text-ink-mute" style={{ padding: '4px 0' }}>
        Matches appear here as you type.
      </p>
    );
  }
  if (matches.length === 0) {
    return (
      <p className="text-xs text-ink-mute" style={{ padding: '4px 0' }}>
        Nothing in this body matches “{query}”.
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-1 overflow-y-auto" style={{ maxHeight: 570 }}>
      {matches.map((m, i) => (
        <li key={`${m.line}-${i}`}>
          <button
            type="button"
            onClick={() => onSelect(i)}
            className={cn(
              'flex w-full items-baseline gap-2 text-left',
              i === active ? 'bg-amber-row' : 'bg-transparent hover:bg-paper-raised',
            )}
            style={{ borderRadius: 4, padding: '4px 6px' }}
          >
            <span className="shrink-0 font-mono text-[11px] text-signal-warn">L{m.line}</span>
            <span className="min-w-0 truncate font-mono text-[11px] text-ink-soft">{m.text}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

/**
 * The right rail leads with the compatibility gap, because it is the most
 * consequential thing about any schema on this page: nothing is checked
 * against the previous version at publish time.
 *
 * `Set a rule` is disabled. The BFF exposes no endpoint for writing a
 * compatibility rule — Apicurio has one, the governance layer in front of it
 * does not — and a button that silently does nothing is exactly the kind of
 * surface the redesign exists to remove. It follows the precedent already in
 * this app: the bulk bar's `Raise Jira` is disabled and says why.
 */
function CompatibilityRail() {
  return (
    <aside className="flex flex-col" style={{ gap: 12 }}>
      <div
        className="bg-paper-card border border-light"
        style={{ borderRadius: 16, padding: '16px 18px' }}
      >
        <div className="flex items-start gap-2.5">
          <CircleAlert size={18} className="shrink-0 mt-px text-signal-warn" aria-hidden />
          <div>
            <h2 className="text-[15px] font-semibold text-ink">No compatibility rule</h2>
            <p className="mt-1.5 text-[13px] leading-[1.45] text-ink-soft">
              Nothing here is checked against the current version when the next version is
              published, so a breaking change would register silently.
            </p>
          </div>
        </div>
        <button
          type="button"
          disabled
          title="Not implemented — the registry API exposes no endpoint for writing a compatibility rule yet"
          className="mt-3 w-full bg-paper text-[13px] font-semibold text-ink-mute cursor-not-allowed"
          style={{ height: 36, borderRadius: 999 }}
        >
          Set a rule
        </button>
        <p className="mt-1.5 text-xs text-ink-mute">
          Not wired up yet: rules are set in Apicurio directly.
        </p>
      </div>
    </aside>
  );
}

function ComplianceTab({
  result,
  isError,
  isLoading,
}: {
  result: ValidationResult | undefined;
  isError: boolean;
  isLoading: boolean;
}) {
  if (isLoading) return <p className="text-sm text-ink-mute">Checking compliance…</p>;

  // A failed query is not a pass. Without this branch an empty result reads
  // identically to "this schema passed every rule".
  if (isError) {
    return (
      <div className="flex items-center gap-2">
        <Badge tone="neutral">unknown</Badge>
        <span className="text-sm text-ink-soft">
          The compliance check could not be loaded, so nothing is known about this schema&rsquo;s
          metadata block right now.
        </span>
      </div>
    );
  }

  if (!result || result.passed) {
    return (
      <div className="flex items-center gap-2">
        <Badge tone="ok">compliant</Badge>
        <span className="text-sm text-ink-mute">All compliance checks passed.</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge tone="fail">non-compliant</Badge>
        <span className="text-sm text-ink-mute">
          {result.failures.length} rule{result.failures.length === 1 ? '' : 's'} failing
        </span>
      </div>
      <ul className="space-y-3">
        {result.failures.map((f) => (
          <li
            key={f.rule}
            className="border border-signal-fail-fill bg-signal-fail-row p-3 space-y-1"
            style={{ borderRadius: 12 }}
          >
            <p className="font-mono text-xs font-semibold text-signal-fail">{f.rule}</p>
            <p className="text-xs text-ink-soft">{f.message}</p>
          </li>
        ))}
      </ul>
      <p className="text-xs text-ink-mute">
        Validated: {new Date(result.validated_at).toLocaleString()}
      </p>
    </div>
  );
}

function ServicesTab({
  data,
  isLoading,
  isError,
}: {
  data: SchemaServicesRead | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  if (isLoading) return <p className="text-sm text-ink-mute">Loading…</p>;

  // A failed lookup is not an empty one. `data` is undefined either way, and
  // `?? []` turned that into "No services produce this schema." — which reads
  // as "nothing breaks if you change this schema", the single most dangerous
  // thing this page could get wrong. The compliance tab and the registry
  // table both carry this branch; this tab had none.
  if (isError) {
    return (
      <div className="space-y-1">
        <p className="text-sm font-medium text-ink">Could not load services</p>
        <p className="text-sm text-ink-mute">
          Who produces and consumes this schema is unknown, not none. Nothing here says it is
          safe to change.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <ServiceSection
        title="Producers"
        services={data?.producers ?? []}
        emptyLabel="No services produce this schema."
      />
      <ServiceSection
        title="Consumers"
        services={data?.consumers ?? []}
        emptyLabel="No services consume this schema."
      />
    </div>
  );
}

function ServiceSection({
  title,
  services,
  emptyLabel,
}: {
  title: string;
  services: SchemaServiceRef[];
  emptyLabel: string;
}) {
  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-ink">
        {title}
        {services.length > 0 && (
          <span className="ml-1.5 font-normal text-ink-mute">({services.length})</span>
        )}
      </h2>
      {services.length === 0 ? (
        <span className="text-xs italic text-ink-mute">{emptyLabel}</span>
      ) : (
        <ul className="space-y-2">
          {services.map((svc) => (
            <li key={svc.ci_id}>
              <Link
                to={`/services/${encodeURIComponent(svc.urn)}`}
                className="flex items-center gap-2 border border-paper-line px-3 py-2 text-sm hover:bg-paper-raised transition-colors group"
                style={{ borderRadius: 12 }}
              >
                <span className="flex-1 font-medium text-ink group-hover:text-accent truncate">
                  {svc.name}
                </span>
                <Badge tone="accent">{svc.domain}</Badge>
                <span className="font-mono text-xs text-ink-mute">{svc.ci_id}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function HistoryTab({
  groupId,
  artifactId,
  versions,
  currentVersion,
  currentContent,
}: {
  groupId: string;
  artifactId: string;
  versions: { version: string; state: string; created_on: string | null; created_by: string | null }[];
  currentVersion: string;
  currentContent: string;
}) {
  return (
    <div style={{ maxWidth: 720 }}>
      <VersionTimeline
        groupId={groupId}
        artifactId={artifactId}
        versions={versions}
        currentVersion={currentVersion}
        currentContent={currentContent}
      />
    </div>
  );
}
