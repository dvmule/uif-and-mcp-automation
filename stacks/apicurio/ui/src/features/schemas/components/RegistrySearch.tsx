import { useEffect, useRef } from 'react';
import { Search } from 'lucide-react';
import { useSchemaUrlState } from '../hooks/use-schema-url-state';

/**
 * The registry's search, which lives in the app header rather than in the
 * toolbar below it.
 *
 * Styled as Echo's SearchBar component: a #F8FAFC field on a 12px radius with
 * a neutral #E0E0E0 edge and an 18px leading glyph. It was a translucent white
 * pill because the header used to be Pearson Purple; on Echo's white header
 * that pill was invisible.
 *
 * The placeholder is `ink-placeholder`, which is Echo's placeholder hue taken
 * darker — Echo's own value measures 2.51:1 on this field. See the token.
 *
 * The `/` hint is kept, which Echo's component does not have. The shortcut is
 * real behaviour with a test behind it, and nothing else on the screen
 * advertises it.
 *
 * It reads and writes `?search=` through `useSchemaUrlState` like every other
 * list control, so it needs no props and no plumbing through `AppShell` — it
 * just has to be rendered inside the router, on the route that has a table to
 * filter. `AppShell` mounts it on `/schemas` only.
 */
export function RegistrySearch() {
  const { state, set } = useSchemaUrlState();
  const inputRef = useRef<HTMLInputElement>(null);

  // `/` focuses the field from anywhere on the page, which is why it also has
  // to be ignored while a field already has focus — otherwise typing a slash
  // into the search box, or into any input on the publish form, is swallowed.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const typing =
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el?.isContentEditable;

      if (e.key === '/' && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <div className="flex w-full max-w-[445px] items-center gap-2.5 rounded-xl border border-paper-edge bg-paper-field px-3 py-2">
      <Search size={18} strokeWidth={2} className="shrink-0 text-ink-placeholder" aria-hidden />
      <input
        ref={inputRef}
        type="search"
        role="searchbox"
        aria-label="Search schemas"
        className="min-w-0 flex-1 bg-transparent text-sm leading-5 text-ink placeholder:text-ink-placeholder focus:outline-none [&::-webkit-search-cancel-button]:hidden"
        placeholder="Search subject, domain, owner…"
        value={state.search}
        onChange={(e) => set({ search: e.target.value })}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault();
            set({ search: '' });
          }
        }}
      />
      <kbd
        aria-hidden
        className="shrink-0 select-none rounded border border-paper-edge px-[5px] py-[3px] font-mono text-[10px] leading-none text-ink-placeholder"
      >
        /
      </kbd>
    </div>
  );
}
