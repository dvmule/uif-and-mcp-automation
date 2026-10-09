import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  /**
   * What broke, in the reader's terms — "This page", "The registry". It is
   * the difference between "navigate away and carry on" and "reload".
   */
  scope: 'page' | 'app';
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Catches a render-time throw and shows what broke, instead of letting React
 * unmount the tree to a blank white page.
 *
 * There was no boundary anywhere in this app, and it had already cost
 * something twice: a fixture whose `data.map` was not a function blanked the
 * whole shell, and an unguarded `localStorage.getItem` in a `useState`
 * initialiser would have done the same in a private window. In both cases the
 * reader gets nothing at all — no message, no nav, no URL to report — which
 * is the worst possible failure for a governance tool someone is using to
 * decide whether a schema is safe to change.
 *
 * A class, because `componentDidCatch` has no hook equivalent.
 *
 * Deliberately NOT a retry button. A render that threw once will throw again
 * on the same inputs, and a button that appears to fix nothing is worse than
 * no button; the recovery that works is navigating away (page scope) or
 * reloading (app scope), and both are offered as what they are.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The console is the only sink this app has — there is no error reporting
    // service wired up, and inventing one here would be a feature, not a fix.
    // The component stack is what makes the message actionable.
    console.error('Unhandled render error', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const isPage = this.props.scope === 'page';

    return (
      <div className="mx-auto max-w-[640px] py-12" role="alert">
        <h1 className="text-base font-semibold text-ink">
          {isPage ? 'This page stopped working' : 'The registry stopped working'}
        </h1>
        <p className="mt-1.5 text-sm text-ink-mute">
          Nothing was changed. The registry itself is unaffected —{' '}
          {isPage
            ? 'the rest of the app still works, so you can go back to the list.'
            : 'reloading usually clears it.'}
        </p>

        {/* The message verbatim, in mono, the way the schema page shows a
          * failed fetch. A paraphrase would be unreportable. */}
        <p className="mt-4 break-words font-mono text-sm text-signal-fail">{error.message}</p>

        <div className="mt-5 flex items-center gap-3">
          {isPage && (
            <a
              href="/schemas"
              className="text-[13px] font-semibold text-accent hover:underline"
            >
              Back to the registry
            </a>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-[13px] font-semibold text-accent hover:underline"
          >
            Reload the page
          </button>
        </div>
      </div>
    );
  }
}
