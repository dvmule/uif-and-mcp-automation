import { XCircle } from 'lucide-react';
import type { SchemaRegistrationResult } from '../types/publish.types';

/**
 * The soft-failure panel, and only that.
 *
 * `POST /schemas/{group}/{artifact}` answers HTTP 200 with
 * `registered: false` and an error string when registration fails, so the
 * status code says nothing. This branches on `registered` and leads with the
 * registry's own error rather than a generic message — the error text names
 * the URN the CMDB could not resolve, which is the only thing that tells the
 * user what to change.
 *
 * There is no success branch any more: `PublishPage` navigates to the new
 * subject's detail page when `registered` is true, which is where the schema's
 * compliance — the thing the publish page exists to produce — is actually
 * shown. A success panel here would have been a second, weaker confirmation
 * that nobody would stay on to read.
 */
export function PublishResult({ result }: { result: SchemaRegistrationResult }) {
  if (result.registered) return null;

  return (
    <div
      role="alert"
      className="bg-signal-fail-row border border-signal-fail-fill"
      style={{ borderRadius: 16, padding: '16px 20px' }}
    >
      <p className="flex items-center gap-2 text-sm font-semibold text-signal-fail">
        <XCircle size={16} strokeWidth={2} />
        {result.topic} was not registered
      </p>
      <p className="mt-1.5 font-mono text-xs text-signal-fail">
        {result.error ?? 'The registry gave no reason.'}
      </p>
      <p className="mt-2 text-xs text-ink-soft">
        The request returned HTTP 200. Registration still failed, so nothing was written
        and nothing is registered under this subject.
      </p>
    </div>
  );
}
