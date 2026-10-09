import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, CircleAlert } from 'lucide-react';
import {
  SchemaPublishForm,
  PublishResult,
  useRegisterSchema,
  detectSchemaType,
  SCHEMA_TYPES,
} from '@/features/publish';
import type { PublishFormValues } from '@/features/publish';
import { useSchemaDetail, useSchemaServices, resolveCurrentVersion } from '@/features/schemas';
import { useServiceList } from '@/features/services';

/**
 * Editing an existing schema and publishing the result as a new version.
 *
 * There is no separate update endpoint and there does not need to be: the
 * registry posts with Apicurio's `ifExists=FIND_OR_CREATE_VERSION`, so the same
 * `POST /schemas/{group}/{subject}` that registers a new subject adds a version
 * to one that already exists. What this page contributes is the prefill — the
 * body and the four governance values as the registry currently holds them —
 * because the metadata block is rewritten from the form on every publish. A
 * blank form would silently reset the classification, the lifecycle and the PII
 * list of whatever it was pointed at.
 */
export function SchemaEditPage() {
  const { groupId, id } = useParams<{ groupId: string; id: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();

  // `?version=` is carried over from the detail page when a version is pinned
  // there, so "update" edits the version that was on screen rather than
  // silently jumping to the latest. Publishing still adds a version at the
  // head — this is a starting point, not a place to write back to.
  const version = params.get('version');

  const detail = useSchemaDetail(groupId!, id!, version ?? undefined);
  const services = useServiceList();
  // Producers carry the URN; the artifact's own metadata carries only the CI
  // id. Either can name the owning service, and this is the more direct of the
  // two — see `initialServiceUrn`.
  const schemaServices = useSchemaServices(groupId!, id!);

  const register = useRegisterSchema();
  const result = register.data;
  const navigatedFor = useRef<string | null>(null);

  const detailPath = `/schemas/${encodeURIComponent(groupId!)}/${encodeURIComponent(id!)}`;

  /**
   * A published version goes back to the schema, which is where the version
   * list, the body and the compliance verdict it just changed are shown.
   *
   * Guarded by `navigatedFor` for the same reason the publish page's effect is:
   * an effect that navigates will re-run on re-render, and without the guard a
   * re-render after the navigation navigates again.
   */
  useEffect(() => {
    if (!result?.registered) return;
    if (navigatedFor.current === detailPath) return;
    navigatedFor.current = detailPath;
    navigate(detailPath);
  }, [result, detailPath, navigate]);

  /**
   * The owning service, as a URN the CMDB can resolve — which is what the
   * register endpoint takes, and it fails outright on one it cannot.
   *
   * Preferred order is the produces edge first, then the CI id in the body's
   * metadata block matched against the service list. The second is a fallback
   * because a CI id is not a URN: the mapping only exists in the service list,
   * and a schema whose metadata names a CI the CMDB no longer returns has no
   * URN to offer. That resolves to `''`, which leaves the picker unset and the
   * checklist blocking on it — the honest outcome, since the alternative is
   * submitting a URN that will be rejected.
   */
  const initialServiceUrn = useMemo(() => {
    const producer = schemaServices.data?.producers[0];
    if (producer?.urn) return producer.urn;
    const ciId = detail.data?.owner.service;
    if (!ciId) return '';
    return services.data?.find((s) => s.ci_id === ciId)?.urn ?? '';
  }, [schemaServices.data, detail.data?.owner.service, services.data]);

  const initial: PublishFormValues | null = useMemo(() => {
    const data = detail.data;
    if (!data) return null;
    // Pinned only when detection would disagree with the type the registry
    // actually holds. Left on "Detect automatically" the badge would claim a
    // type this artifact does not have — a JSON artifact whose body happens to
    // look like an Avro record reads as AVRO — and the JSON gate would be
    // decided from the wrong one.
    const stored = (SCHEMA_TYPES as readonly string[]).includes(data.type) ? data.type : '';
    const typeOverride = stored && detectSchemaType(data.content) !== stored ? stored : '';
    return {
      artifactId: data.artifact_id,
      groupId: data.group_id,
      content: data.content,
      serviceUrn: initialServiceUrn,
      typeOverride,
      classification: data.classification ?? '',
      lifecycle: data.lifecycle ?? 'active',
      piiFields: data.pii_fields,
    };
  }, [detail.data, initialServiceUrn]);

  const currentVersion = detail.data
    ? resolveCurrentVersion(detail.data.versions, detail.data.content_version)
    : null;

  /**
   * Whether the prefill is final, and so whether the form may mount.
   *
   * This is `isPending` on all three queries and NOT `isLoading`, and the
   * difference is a bug that cost an edit. `isLoading` is
   * `isPending && isFetching`, and a query that is enabled but has not yet
   * started its fetch — the state every query is in on the render where it
   * first mounts enabled — is pending and idle, so `isLoading` is false. On a
   * cold load that never shows, because nothing has resolved yet either. But
   * arriving from the schema page, the schema itself is already in the cache
   * while the produces edge is not: the gate read "nothing is loading", the
   * form mounted and seeded itself from an owning service of `''`, and then
   * the edge landed, the gate flipped to loading, and the form was UNMOUNTED
   * and rebuilt — discarding whatever had been typed into it.
   *
   * `isPending` is false only once a query has actually settled, and it stays
   * false through later background refetches, so this cannot flip back.
   */
  const prefillReady = !detail.isPending && !services.isPending && !schemaServices.isPending;

  /**
   * Latched, so that even a state these three queries can reach that
   * `prefillReady` has not been reasoned about cannot pull a mounted form out
   * from under someone mid-edit. Set during render rather than in an effect,
   * which is React's own guidance for adjusting state when something changes:
   * an effect runs after paint, so the form would unmount for one frame first
   * — the exact thing this is here to prevent.
   */
  const [formMounted, setFormMounted] = useState(false);
  if (prefillReady && !formMounted) setFormMounted(true);

  return (
    <div className="flex flex-col" style={{ gap: 20 }}>
      <div>
        <Link
          to={detailPath}
          className="flex items-center gap-1.5 self-start text-[13px] font-semibold text-accent hover:underline"
        >
          <ArrowLeft size={15} />
          Back to the schema
        </Link>
        <h1 className="mt-3 text-3xl font-semibold text-ink" style={{ lineHeight: 1.1 }}>
          Publish a new version
        </h1>
        <p
          className="mt-2 text-[15px] text-ink-soft"
          style={{ lineHeight: 1.45, maxWidth: '74ch' }}
        >
          The body and the governance values below are what the registry holds now. Both are
          rewritten on publish — the metadata block is built from this form, not merged into
          what is already there — so leave anything you do not mean to change alone.
        </p>
      </div>

      {/* Only on this page, and only when there is no rule.
        *
        * A first registration cannot break a consumer, because there is no
        * earlier version to be incompatible with. A new version can, and
        * Apicurio only refuses one when the subject carries a compatibility
        * rule — no subject in this registry does. So the thing that would stop
        * a field being removed out from under a consumer is not running, and
        * the publish will be accepted with no complaint. Saying so here is the
        * only warning there is.
        *
        * The subject page states the same fact as `Compatibility · not set`.
        * This says what follows from it, on the page where it is about to
        * matter.
        *
        * `signal-warn-fill` is the amber tint and there is no warn border
        * token; the fill as a border would be invisible against itself, so the
        * hue at low opacity gives the band an edge without inventing one. */}
      {formMounted && detail.data && !detail.data.compatibility && (
        <div
          className="flex items-start gap-2.5 bg-signal-warn-fill border border-signal-warn/25"
          style={{ borderRadius: 16, padding: '14px 18px' }}
        >
          <CircleAlert size={16} className="shrink-0 text-signal-warn" style={{ marginTop: 2 }} aria-hidden />
          <p className="text-[13px] text-signal-warn" style={{ lineHeight: 1.45, maxWidth: '74ch' }}>
            This subject carries no compatibility rule, so the registry will not check this
            version against v{currentVersion}. A removed or renamed field registers without
            complaint and breaks its consumers at runtime — check the Services tab for who
            reads this schema before you publish.
          </p>
        </div>
      )}

      {result && <PublishResult result={result} />}

      {register.isError && (
        <div
          role="alert"
          className="bg-signal-fail-row border border-signal-fail-fill"
          style={{ borderRadius: 16, padding: '16px 20px' }}
        >
          <p className="text-sm font-semibold text-signal-fail">The request itself failed</p>
          <p className="mt-1 font-mono text-xs text-signal-fail">{register.error.message}</p>
        </div>
      )}

      {/* The form seeds its state from `initial` exactly once, so it must not
        * mount before every source of that prefill has settled. See
        * `prefillReady`. */}
      {!formMounted ? (
        <p className="text-sm text-ink-mute">Loading schema…</p>
      ) : detail.error || !initial ? (
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">Could not load schema</p>
          <p className="font-mono text-sm text-signal-fail">
            {(detail.error as Error)?.message ?? 'Unknown error'}
          </p>
        </div>
      ) : (
        <SchemaPublishForm
          mode="new-version"
          initial={initial}
          baseVersion={currentVersion}
          onSubmit={(args) => register.mutate(args)}
          isSubmitting={register.isPending}
        />
      )}
    </div>
  );
}
