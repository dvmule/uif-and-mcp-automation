import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { SchemaPublishForm, PublishResult, useRegisterSchema } from '@/features/publish';

export function PublishPage() {
  const register = useRegisterSchema();
  const navigate = useNavigate();
  // The result carries only `topic`, so remember the group we submitted under.
  const [submittedGroup, setSubmittedGroup] = useState('default');
  const navigatedFor = useRef<string | null>(null);

  const result = register.data;

  /**
   * A successful registration leaves the form and goes to the schema, which is
   * where compliance is shown — the thing this page exists to produce. The
   * group comes from what was actually submitted, not a hardcoded 'default':
   * publishing into a non-default group used to land on a URL for a subject
   * that does not exist there.
   *
   * `navigatedFor` guards the effect against a re-render re-navigating for a
   * result it has already acted on.
   */
  useEffect(() => {
    if (!result?.registered) return;
    const target = `/schemas/${encodeURIComponent(submittedGroup)}/${encodeURIComponent(result.topic)}`;
    if (navigatedFor.current === target) return;
    navigatedFor.current = target;
    navigate(target);
  }, [result, submittedGroup, navigate]);

  return (
    <div className="flex flex-col" style={{ gap: 20 }}>
      <div>
        <Link
          to="/schemas"
          className="flex items-center gap-1.5 self-start text-[13px] font-semibold text-accent hover:underline"
        >
          <ArrowLeft size={15} />
          Back to the registry
        </Link>
        <h1 className="mt-3 text-3xl font-semibold text-ink" style={{ lineHeight: 1.1 }}>
          Publish a schema
        </h1>
        <p
          className="mt-2 text-[15px] text-ink-soft"
          style={{ lineHeight: 1.45, maxWidth: '74ch' }}
        >
          Registering here is what makes a schema compliant: the registry writes the Pearson
          metadata block into the body and mirrors it onto the artifact's properties. A schema
          pushed straight to Apicurio skips both and lands non-compliant.
        </p>
      </div>

      {/* A 200 with `registered: false` is a failure and renders here.
        * A 200 with `registered: true` navigates instead — see the effect. */}
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

      <SchemaPublishForm
        onSubmit={(args) => {
          setSubmittedGroup(args.groupId);
          register.mutate(args);
        }}
        isSubmitting={register.isPending}
      />
    </div>
  );
}
