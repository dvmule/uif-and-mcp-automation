import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Upload, X, Check, CircleAlert } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { useServiceList } from '@/features/services';
import { detectSchemaType, JSON_BASED, SCHEMA_TYPES } from '../schema-type';
import type { DirectSchemaRegisterRequest } from '../types/publish.types';
import { SUBJECT_SHAPE, subjectWarningFor } from '../subject-shape';

const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted'] as const;

type Classification = (typeof CLASSIFICATIONS)[number];

/** Card geometry, repeated on all six cards across the two columns. */
const CARD = 'bg-paper-card border border-paper-line';
const CARD_STYLE = { borderRadius: 16, padding: 20 } as const;

/** Resting and focused input treatment, per the design's focus token. */
const FIELD =
  'h-10 w-full bg-paper-card px-3 font-mono text-[13px] text-ink border border-paper-line ' +
  'focus:outline-none focus:border-accent focus:ring focus:ring-accent/[.18] focus:ring-[3px]';

function joinWithAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}

/** Every value the form holds, so an edit can be started from a schema that
  * already exists rather than from an empty form. */
export interface PublishFormValues {
  artifactId: string;
  groupId: string;
  content: string;
  serviceUrn: string;
  typeOverride: string;
  classification: Classification | '';
  lifecycle: 'active' | 'deprecated';
  piiFields: string[];
}

interface Props {
  onSubmit: (args: { groupId: string; artifactId: string; body: DirectSchemaRegisterRequest }) => void;
  isSubmitting: boolean;
  /**
   * `create` registers a subject that does not exist yet. `new-version` adds a
   * version to one that does: the subject and the group are what identify the
   * artifact, so they are fixed, and the checklist asks for a change instead of
   * for a subject.
   *
   * Both modes post to the same endpoint — Apicurio's `FIND_OR_CREATE_VERSION`
   * is what decides which it is — so this changes what the form asks for and
   * says, not where it sends it.
   */
  mode?: 'create' | 'new-version';
  /** Prefill. Anything omitted starts empty, as it does on a fresh publish. */
  initial?: Partial<PublishFormValues>;
  /** In `new-version` mode, the version the prefill was read from. */
  baseVersion?: string | null;
}

export function SchemaPublishForm({
  onSubmit,
  isSubmitting,
  mode = 'create',
  initial,
  baseVersion,
}: Props) {
  const isNewVersion = mode === 'new-version';
  const { data: services, isLoading: servicesLoading, isError: servicesError } = useServiceList();

  // The CMDB returns the same service more than once — 16 entries, 14 distinct
  // URNs, with content-svc and pricing-svc each appearing twice. A URN is an
  // identity, so offering it twice both duplicated React keys (whose
  // reconciliation is explicitly unsupported) and showed the user the same
  // service in the picker twice. De-duplicate on the way in; the first entry
  // wins, since the duplicates carry identical fields.
  const uniqueServices = useMemo(() => {
    const seen = new Map<string, NonNullable<typeof services>[number]>();
    for (const s of services ?? []) if (!seen.has(s.urn)) seen.set(s.urn, s);
    return [...seen.values()];
  }, [services]);
  const fileRef = useRef<HTMLInputElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  // Initial values only — the props seed the state once, and the form owns it
  // from then on. Re-syncing on a prop change would throw away a half-written
  // edit every time the schema query refetched in the background.
  const [artifactId, setArtifactId] = useState(initial?.artifactId ?? '');
  const [groupId, setGroupId] = useState(initial?.groupId ?? 'default');
  const [content, setContent] = useState(initial?.content ?? '');
  const [serviceUrn, setServiceUrn] = useState(initial?.serviceUrn ?? '');
  const [typeOverride, setTypeOverride] = useState(initial?.typeOverride ?? '');
  const [classification, setClassification] = useState<Classification | ''>(
    initial?.classification ?? '',
  );
  const [lifecycle, setLifecycle] = useState<'active' | 'deprecated'>(
    initial?.lifecycle ?? 'active',
  );
  const [piiFields, setPiiFields] = useState<string[]>(initial?.piiFields ?? []);
  // The half-typed field name, held in state rather than left in the DOM.
  // Uncontrolled, it was invisible to the submit handler and to the metadata
  // preview: a name typed and not confirmed with Enter was still sitting in
  // the box while the schema registered with `piiFields: []`.
  const [piiDraft, setPiiDraft] = useState('');

  const selectedService = uniqueServices.find((s) => s.urn === serviceUrn);

  const detectedType = content.trim() ? detectSchemaType(content) : null;

  // PROTOBUF, GRAPHQL and XSD bodies are not JSON and the BFF's
  // `detect_schema_type` accepts all three, so the parse check applies only when
  // the effective type is JSON-based: the chosen override if there is one, and
  // otherwise whatever the body itself looks like.
  //
  // "What the body looks like" has to mean the DETECTOR's answer, not whether
  // the body starts with a brace. The detector falls back to JSON for anything
  // it does not recognise — YAML, prose, a pasted error message — so a YAML
  // AsyncAPI document registered as type JSON while this gate was skipped
  // entirely: the badge read "JSON detected", the checklist ticked "a schema
  // body that parses as JSON", and Publish enabled. Server-side
  // `inject_metadata` then json.loads it, fails, and returns the body
  // UNCHANGED — so the artifact registered with no metadata block at all,
  // which is the exact non-compliance this page exists to prevent.
  const effectiveType = typeOverride || detectedType;
  const jsonGateApplies = effectiveType ? JSON_BASED.has(effectiveType) : false;

  const jsonError = useMemo(() => {
    if (!content.trim() || !jsonGateApplies) return null;
    try {
      JSON.parse(content);
      return null;
    } catch {
      // Not "will reject it", which is what this said and is not what
      // happens: the registry accepts the body and silently writes no
      // metadata block into it.
      return 'Schema body is not valid JSON. It would register with no metadata block, which is what makes a schema non-compliant.';
    }
  }, [content, jsonGateApplies]);

  const nonJsonWarning =
    typeOverride && !JSON_BASED.has(typeOverride)
      ? `${typeOverride} bodies get no metadata block written, so the schema will be non-compliant the moment it registers. Register it, then fix compliance manually.`
      : null;

  const subjectWarning = subjectWarningFor(artifactId);
  /**
   * Three states, not two.
   *
   * The positive confirmation used to render whenever there was no warning,
   * which was fine when the warning fired on everything unconventional. Now
   * that it stays silent about subjects governed by OTHER standards, "no
   * warning" no longer means "conforms" — it would have confirmed
   * `product-created-data` as matching this convention, which it does not.
   *
   * So: conforms, claims-but-malformed, or governed elsewhere and left alone.
   */
  const subjectConforms = !!artifactId && SUBJECT_SHAPE.test(artifactId);

  /**
   * The PII list as the form currently READS: the confirmed chips plus
   * whatever is still in the box.
   *
   * Submit, the metadata preview and the change check below all go through
   * this, so the block that gets written cannot differ from the one on screen.
   * Enter and comma are still how you add a second name — they are not how the
   * first one is saved.
   */
  const piiWithDraft = ((): string[] => {
    const raw = piiDraft.trim();
    if (!raw || piiFields.includes(raw)) return piiFields;
    return [...piiFields, raw];
  })();

  /**
   * Whether anything would actually change, in `new-version` mode.
   *
   * This is a gate and not a nicety. The endpoint posts with Apicurio's
   * `ifExists=FIND_OR_CREATE_VERSION`, which on a body it already holds FINDS
   * the existing version rather than adding one — and answers
   * `registered: true` either way. So submitting an untouched form reports
   * success, navigates back, and has published nothing. Refusing it is the
   * only way the page can tell the truth about what it did.
   *
   * Governance counts as a change because the registry writes those five
   * values into the body: a new classification is a different body, and
   * therefore a real new version.
   */
  const hasChanges =
    content !== (initial?.content ?? '') ||
    serviceUrn !== (initial?.serviceUrn ?? '') ||
    typeOverride !== (initial?.typeOverride ?? '') ||
    classification !== (initial?.classification ?? '') ||
    lifecycle !== (initial?.lifecycle ?? 'active') ||
    piiWithDraft.length !== (initial?.piiFields ?? []).length ||
    piiWithDraft.some((f, i) => f !== (initial?.piiFields ?? [])[i]);

  /**
   * The gates, as one list.
   *
   * These used to be two hand-maintained lists — `canSubmit`'s boolean chain
   * and `missingForSubmit`'s strings — with a comment warning that changing
   * one meant changing the other. The design puts the same checks on
   * screen as a third view of them, so they are derived from one array now:
   * `label` is what the checklist shows, `missing` is the fragment the
   * disabled-button sentence uses. They cannot drift out of step.
   *
   * The first gate differs by mode. A subject cannot be missing on a new
   * version — it is fixed, and it is what says which artifact this is — so a
   * checklist line for it could never fail and could never be acted on. What
   * can fail there is having changed nothing.
   */
  const gates = [
    isNewVersion
      ? {
          met: hasChanges,
          label: 'A change to the body or to governance',
          missing: 'a change to the body or to governance',
        }
      : { met: Boolean(artifactId.trim()), label: 'A subject', missing: 'a subject' },
    {
      met: Boolean(content.trim()) && !jsonError,
      label: 'A schema body that parses as JSON',
      missing: content.trim() ? 'a valid JSON schema body' : 'a schema body',
    },
    {
      met: Boolean(serviceUrn),
      label: 'An owning service the CMDB can resolve',
      missing: 'an owning service',
    },
    { met: Boolean(classification), label: 'A data classification', missing: 'a data classification' },
  ];

  const missingForSubmit = gates.filter((g) => !g.met).map((g) => g.missing);
  const canSubmit = missingForSubmit.length === 0 && !isSubmitting;

  const disabledReason =
    !isSubmitting && missingForSubmit.length > 0
      ? isNewVersion
        ? `This needs ${joinWithAnd(missingForSubmit)}.`
        : `Choose ${joinWithAnd(missingForSubmit)} to publish.`
      : null;

  const lines = content ? content.split('\n').length : 0;

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    // Straight into the body field, so it goes through exactly the same JSON
    // gate, type detection and metadata preview as anything typed in.
    reader.onload = (ev) => setContent((ev.target?.result as string) ?? '');
    reader.readAsText(file);
    e.target.value = '';
  }

  function commitPii(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    setPiiFields(piiWithDraft);
    setPiiDraft('');
  }

  return (
    <form
      className="grid items-start"
      style={{ gridTemplateColumns: 'minmax(0,1fr) 352px', gap: 20 }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        // The classification gate already required this, so narrow it once,
        // here, rather than re-checking at runtime.
        const dataClassification = classification as (typeof CLASSIFICATIONS)[number];
        onSubmit({
          groupId: groupId.trim() || 'default',
          artifactId: artifactId.trim(),
          body: {
            service_urn: serviceUrn,
            content,
            type_override: typeOverride || null,
            data_classification: dataClassification,
            pii_fields: piiWithDraft,
            lifecycle,
          },
        });
      }}
    >
      {/* ═══════════════════════════════════════════════════ left column */}
      <div className="min-w-0 flex flex-col" style={{ gap: 16 }}>
        {/* ---------------------------------------------------- subject */}
        <section className={CARD} style={CARD_STYLE}>
          <h2 className="text-base font-semibold text-ink">Subject</h2>
          <p className="mt-1 text-[13px] text-ink-mute">
            {isNewVersion ? (
              <>
                The subject and the group are what identify the artifact, so a new
                version cannot change them. Publish under a different subject to
                register a separate schema.
              </>
            ) : (
              <>
                Subjects in this registry look like{' '}
                <code className="font-mono">domain.entity.event-value</code>.
              </>
            )}
          </p>

          {/* Read-only text, not a disabled input: a greyed-out box invites the
            * click it will refuse, and there is nothing here to edit. */}
          {isNewVersion ? (
            <div className="mt-4 flex items-baseline gap-2 flex-wrap">
              <span className="font-mono text-[15px] font-semibold text-ink break-all">
                {artifactId}
              </span>
              <Badge tone="neutral">{groupId}</Badge>
              {baseVersion && <Badge tone="neutral">based on v{baseVersion}</Badge>}
            </div>
          ) : (
          <div className="mt-4 grid" style={{ gridTemplateColumns: 'minmax(0,1fr) 180px', gap: 16 }}>
            <div className="min-w-0">
              <label htmlFor="pub-subject" className="sr-only">
                Subject
              </label>
              <input
                id="pub-subject"
                value={artifactId}
                onChange={(e) => setArtifactId(e.target.value)}
                placeholder="product.product.gold"
                className={FIELD}
                style={{ borderRadius: 4 }}
              />
              {/* Warns, never blocks. Silent for a subject another standard governs. */}
              {artifactId && subjectWarning && (
                <p className="mt-1.5 flex items-start gap-1.5 text-xs text-signal-warn">
                  <CircleAlert size={13} className="shrink-0 mt-px" aria-hidden />
                  {subjectWarning}
                </p>
              )}
              {subjectConforms && (
                <p className="mt-1.5 flex items-baseline gap-1.5 text-xs text-ink">
                  <Check size={13} className="shrink-0 translate-y-0.5" aria-hidden />
                  {/* One flex child, so the gap does not open up before the
                    * full stop or around the inline code. */}
                  <span>
                    Matches the convention. Domain reads as{' '}
                    <code className="font-mono">{artifactId.split('.')[0]}</code>, type as{' '}
                    <code className="font-mono">{artifactId.split('.')[2]}</code>.
                  </span>
                </p>
              )}
            </div>

            <div className="min-w-0">
              <label htmlFor="pub-group" className="sr-only">
                Group
              </label>
              <input
                id="pub-group"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                className={FIELD}
                style={{ borderRadius: 4 }}
              />
              <p className="mt-1.5 text-xs text-ink-mute">Rarely changed.</p>
            </div>
          </div>
          )}
        </section>

        {/* ------------------------------------------------ schema body */}
        <section className={CARD} style={CARD_STYLE}>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-semibold text-ink">
              <label htmlFor="pub-body">Schema body</label>
            </h2>
            {detectedType && <Badge tone="neutral">{detectedType} detected</Badge>}
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-1.5 shrink-0 border border-light text-[13px] font-semibold text-accent"
              style={{ height: 32, borderRadius: 999, padding: '0 14px' }}
            >
              <Upload size={14} strokeWidth={2} />
              Upload a file
            </button>
            <input ref={fileRef} type="file" onChange={handleFile} className="hidden" />
          </div>

          <div
            className="mt-3 overflow-hidden border border-paper-line"
            style={{ borderRadius: 8 }}
          >
            <div className="flex" style={{ height: 300 }}>
              {/* A 40px gutter beside the textarea, scrolled in step with it.
                * A textarea cannot carry line numbers itself, and the numbers
                * are load-bearing here: the footer counts lines, and a body
                * pasted from a file is read by line when it fails to parse. */}
              <div
                ref={gutterRef}
                aria-hidden
                className="shrink-0 overflow-hidden bg-paper-raised text-right font-mono text-xs leading-5 text-ink-mute select-none"
                style={{ width: 40, paddingTop: 8, paddingBottom: 8, paddingRight: 8 }}
              >
                {Array.from({ length: Math.max(lines, 1) }, (_, i) => (
                  <div key={i}>{i + 1}</div>
                ))}
              </div>
              <textarea
                id="pub-body"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onScroll={(e) => {
                  if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop;
                }}
                spellCheck={false}
                placeholder={'{\n  "type": "record",\n  "name": "OrderPlaced",\n  "fields": []\n}'}
                className="flex-1 min-w-0 resize-none bg-paper-card px-3 py-2 font-mono text-xs leading-5 text-ink placeholder:text-ink-mute focus:outline-none"
              />
            </div>

            <div
              className="flex items-center gap-2 flex-wrap border-t border-paper-line bg-paper-raised text-xs"
              style={{ padding: '7px 12px' }}
            >
              {jsonError ? (
                <span className="font-medium text-signal-fail">{jsonError}</span>
              ) : jsonGateApplies && content.trim() ? (
                <span className="flex items-center gap-1.5 text-ink">
                  <Check size={13} aria-hidden />
                  Valid JSON
                </span>
              ) : null}
              {/* No "Valid JSON" claim about a body the JSON gate does not
                * apply to — a Protobuf body is not valid JSON and saying so
                * either way would be wrong. */}
              {content.trim() && !jsonError && jsonGateApplies && (
                <span className="text-ink-mute">·</span>
              )}
              {lines > 0 && (
                <span className="text-ink-mute">
                  {lines} line{lines === 1 ? '' : 's'}
                </span>
              )}
              <div className="flex-1" />
              <span className="text-ink-mute">Type detection can be overridden below.</span>
            </div>
          </div>
        </section>

        {/* ------------------------------------------------- governance */}
        <section className={CARD} style={CARD_STYLE}>
          <h2 className="text-base font-semibold text-ink">Governance</h2>
          <p className="mt-1 text-[13px] text-ink-mute">
            These four values are what the registry writes into the metadata block.
            Compliance is read back from them.
          </p>

          <div className="mt-4 flex flex-col" style={{ gap: 16 }}>
            <Field label="Owning service" htmlFor="pub-service">
              <select
                id="pub-service"
                value={serviceUrn}
                onChange={(e) => setServiceUrn(e.target.value)}
                className={cn(FIELD, 'font-sans')}
                style={{ borderRadius: 4 }}
              >
                <option value="">
                  {servicesLoading
                    ? 'Loading services…'
                    : servicesError
                      ? 'Service list unavailable'
                      : 'Choose a service…'}
                </option>
                {uniqueServices.map((s) => (
                  <option key={s.urn} value={s.urn}>
                    {s.name} — {s.domain}
                  </option>
                ))}
              </select>
              {servicesError ? (
                <p className="mt-1.5 text-xs font-medium text-signal-fail">
                  Couldn't load the service list — publishing needs it, so nothing can be
                  published until this endpoint recovers.
                </p>
              ) : servicesLoading ? (
                <p className="mt-1.5 text-xs text-ink-mute">Loading services…</p>
              ) : (
                <p className="mt-1.5 text-xs text-ink-mute">
                  Required, and never free text — registration fails outright on a URN the
                  CMDB cannot resolve.
                </p>
              )}
            </Field>

            <div className="grid" style={{ gridTemplateColumns: 'minmax(0,1fr) 180px', gap: 16 }}>
              <Field label="Schema type" htmlFor="pub-type">
                <select
                  id="pub-type"
                  value={typeOverride}
                  onChange={(e) => setTypeOverride(e.target.value)}
                  className={cn(FIELD, 'font-sans')}
                  style={{ borderRadius: 4 }}
                >
                  <option value="">Detect automatically</option>
                  {SCHEMA_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                {nonJsonWarning ? (
                  <p className="mt-1.5 text-xs font-medium text-signal-warn">{nonJsonWarning}</p>
                ) : (
                  <p className="mt-1.5 text-xs text-signal-warn">
                    Override only for PROTOBUF, GRAPHQL or XSD — those get no metadata block
                    and land non-compliant.
                  </p>
                )}
              </Field>

              <Field label="Lifecycle" htmlFor="pub-lifecycle">
                <select
                  id="pub-lifecycle"
                  value={lifecycle}
                  onChange={(e) => setLifecycle(e.target.value as 'active' | 'deprecated')}
                  className={FIELD}
                  style={{ borderRadius: 4 }}
                >
                  <option value="active">active</option>
                  <option value="deprecated">deprecated</option>
                </select>
              </Field>
            </div>

            <fieldset>
              <div className="flex items-center gap-2">
                <legend className="text-xs font-medium text-ink">Data classification</legend>
                {/* The contract has no default, so an empty value is a real
                  * state that has to be shown rather than assumed. */}
                {!classification && <Badge tone="warn">not set</Badge>}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {CLASSIFICATIONS.map((c) => (
                  <label
                    key={c}
                    className={cn(
                      'flex items-center gap-2.5 cursor-pointer border',
                      classification === c ? 'border-accent bg-accent-soft' : 'border-light',
                    )}
                    style={{ height: 40, borderRadius: 999, padding: '0 16px 0 13px' }}
                  >
                    <input
                      id={`pub-class-${c}`}
                      type="radio"
                      name="classification"
                      value={c}
                      checked={classification === c}
                      onChange={() => setClassification(c)}
                      className="sr-only"
                    />
                    <span
                      aria-hidden
                      className="shrink-0 flex items-center justify-center"
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: 999,
                        border: '1.5px solid #512EAB',
                      }}
                    >
                      {classification === c && (
                        <span
                          style={{ width: 8, height: 8, borderRadius: 999, background: '#512EAB' }}
                        />
                      )}
                    </span>
                    <span className="font-mono text-[13px] text-ink">{c}</span>
                  </label>
                ))}
              </div>
              <p className="mt-2 text-xs text-ink-mute">
                The contract carries no default, so the form has to ask.
              </p>
            </fieldset>

            <Field label="PII fields" htmlFor="pub-pii">
              <div
                className="flex items-center gap-1.5 flex-wrap w-full bg-paper-card border border-paper-line focus-within:border-accent"
                style={{ minHeight: 40, borderRadius: 4, padding: '0 10px' }}
              >
                {piiFields.map((f) => (
                  <span
                    key={f}
                    className="flex items-center gap-1 shrink-0 bg-paper-line font-mono text-xs text-ink-soft"
                    style={{ borderRadius: 4, padding: '2px 6px' }}
                  >
                    {f}
                    <button
                      type="button"
                      aria-label={`Remove ${f}`}
                      onClick={() => setPiiFields(piiFields.filter((x) => x !== f))}
                    >
                      <X size={11} strokeWidth={2.5} />
                    </button>
                  </span>
                ))}
                <input
                  id="pub-pii"
                  value={piiDraft}
                  onChange={(e) => setPiiDraft(e.target.value)}
                  onKeyDown={commitPii}
                  // Tabbing away confirms it too, so the chip appears where
                  // the reader expects it rather than only at submit.
                  onBlur={() => {
                    setPiiFields(piiWithDraft);
                    setPiiDraft('');
                  }}
                  placeholder="Field name, then Enter"
                  className="flex-1 min-w-[140px] bg-transparent font-mono text-[13px] text-ink placeholder:font-sans placeholder:text-ink-mute focus:outline-none"
                  style={{ height: 38 }}
                />
              </div>
            </Field>
          </div>
        </section>
      </div>

      {/* ══════════════════════════════════════════════════ right column */}
      <div className="flex flex-col" style={{ gap: 16 }}>
        <MetadataPreview
          ciId={selectedService?.ci_id ?? null}
          domain={selectedService?.domain ?? null}
          classification={classification || null}
          lifecycle={lifecycle}
          piiFields={piiWithDraft}
        />

        <section className={CARD} style={CARD_STYLE}>
          <h2 className="text-[15px] font-semibold text-ink">
            {isNewVersion ? 'Before you publish a new version' : 'Before you publish'}
          </h2>
          <ul className="mt-3 flex flex-col" style={{ gap: 10 }}>
            {gates.map((gate) => (
              <li key={gate.label} className="flex items-start gap-2.5">
                {gate.met ? (
                  <Check size={16} className="shrink-0 mt-px text-ink" aria-hidden />
                ) : (
                  <span
                    aria-hidden
                    className="shrink-0 mt-0.5"
                    style={{
                      width: 16,
                      height: 16,
                      borderRadius: 999,
                      border: '1.5px solid #BB4D00',
                    }}
                  />
                )}
                <span
                  className={cn('text-[13px]', gate.met ? 'text-ink-soft' : 'text-signal-warn')}
                >
                  {gate.label}
                </span>
              </li>
            ))}
          </ul>

          <div className="my-4 h-px" style={{ background: '#EDECF6' }} />

          <button
            type="submit"
            disabled={!canSubmit}
            aria-describedby={disabledReason ? 'pub-submit-hint' : undefined}
            className={cn(
              'w-full text-sm font-semibold',
              // Not opacity: a 40%-opacity purple pill still reads as the
              // primary action and invites the click it will refuse.
              canSubmit ? 'bg-ink text-white' : 'bg-paper text-ink-mute cursor-not-allowed',
            )}
            style={{ height: 48, borderRadius: 999 }}
          >
            {isSubmitting
              ? 'Publishing…'
              : isNewVersion
                ? 'Publish new version'
                : 'Publish schema'}
          </button>
          {disabledReason && (
            <p id="pub-submit-hint" className="mt-2 text-xs text-ink-mute">
              {disabledReason}
            </p>
          )}
        </section>

        <section className={cn(CARD, 'border-light')} style={CARD_STYLE}>
          <h2 className="text-[15px] font-semibold text-ink">A 200 is not a success</h2>
          <p className="mt-1.5 text-[13px] leading-[1.45] text-ink-soft">
            Registration can fail softly. The result panel reads{' '}
            <code className="font-mono">registered</code>, not the status code, and leads with
            the registry's own error when it is false.
          </p>
        </section>
      </div>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-ink">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

/**
 * The five fields `metadata_validator.py` reads compliance from, exactly as
 * `metadata_injector.py` will write them.
 *
 * `domain` is the OWNING SERVICE's domain, from its CMDB record — not the
 * subject's first segment. Those two can differ (a `commerce.*` subject owned
 * by a finance service), and previewing the subject's would show a value the
 * registry is not going to write. `owner.team` and `owner.email` are resolved
 * server-side from the URN, so the preview does not claim them.
 */
function MetadataPreview({
  ciId,
  domain,
  classification,
  lifecycle,
  piiFields,
}: {
  ciId: string | null;
  domain: string | null;
  classification: string | null;
  lifecycle: string;
  piiFields: string[];
}) {
  const unset = [
    ciId === null && 'owner.service',
    domain === null && 'domain',
    classification === null && 'dataClassification',
  ].filter((v): v is string => Boolean(v));

  return (
    <section className="bg-paper-card border border-paper-line" style={CARD_STYLE}>
      <h2 className="text-[15px] font-semibold text-ink">What gets written</h2>
      <p className="mt-1 text-[12.5px] leading-[1.4] text-ink-mute">
        Injected into the body and mirrored onto the artifact's properties on publish. Team
        and email are resolved from the URN.
      </p>

      <pre
        className="mt-3 overflow-x-auto bg-paper-raised border border-paper-line font-mono"
        style={{ borderRadius: 8, padding: '12px 14px', fontSize: 11.5, lineHeight: '19px' }}
      >
        <Struct>{'"metadata": {'}</Struct>
        {'\n  '}
        <Struct>{'"owner": {'}</Struct>
        {'\n    '}
        <Struct>{'"service": '}</Struct>
        <Val value={ciId} />
        {'\n  '}
        <Struct>{'},'}</Struct>
        {'\n  '}
        <Struct>{'"domain": '}</Struct>
        <Val value={domain} />
        <Struct>{','}</Struct>
        {'\n  '}
        <Struct>{'"dataClassification": '}</Struct>
        <Val value={classification} />
        <Struct>{','}</Struct>
        {'\n  '}
        <Struct>{'"lifecycle": '}</Struct>
        <Val value={lifecycle} />
        <Struct>{','}</Struct>
        {'\n  '}
        <Struct>{'"piiFields": ['}</Struct>
        <span style={{ color: '#333333' }}>
          {piiFields.map((f) => `"${f}"`).join(', ')}
        </span>
        <Struct>{']'}</Struct>
        {'\n'}
        <Struct>{'}'}</Struct>
      </pre>

      {/* An unset required value renders as `null` above rather than being
        * omitted, and is named here — omitting it would read as "this schema
        * has no such field" instead of "nothing is in it yet". */}
      {unset.map((field) => (
        <p
          key={field}
          className="mt-2 inline-flex bg-signal-warn-fill font-mono text-xs text-signal-warn"
          style={{ borderRadius: 4, padding: '2px 6px' }}
        >
          {field} is still null
        </p>
      ))}
    </section>
  );
}

function Struct({ children }: { children: ReactNode }) {
  return <span style={{ color: '#0D004D' }}>{children}</span>;
}

/** A set value in UX Black; an unset one as `null` in warn. */
function Val({ value }: { value: string | null }) {
  if (value === null) return <span style={{ color: '#BB4D00' }}>null</span>;
  return <span style={{ color: '#333333' }}>{`"${value}"`}</span>;
}
