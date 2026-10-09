import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'fail';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

/**
 * Opaque fill/text pairs, not foreground-at-10%-alpha.
 *
 * These five ratios were measured at 11–12px against the fills named here.
 * `bg-signal-warn/10` composited the foreground over whatever sat behind the
 * badge, which is a different colour from the fill that was checked — and on a
 * tinted table row it was a third colour again. Keep each pair together.
 *
 * `ok` is reserved for compliance, and is the only tone that carries green.
 * Lifecycle `active` used to use it, which put two green badges on every
 * ordinary row and made the normal case the loudest thing in the table —
 * `active` is `neutral` now. Colour is spent on exceptions: a subject that is
 * live and passing its rules should read as plain text, and the eye should be
 * drawn only to `deprecated` and `non-compliant`.
 *
 * The three status pairs are Echo's, except that `ok` is Echo's green one step
 * darker: Echo uses #009966 on 32px figures and 24px icons, where AA asks
 * 3:1, and this badge is 11px text, where it asks 4.5:1. See the note in
 * tailwind.config.ts. `text-signal-ok-bold` is the token for Echo's exact
 * value, and it must not be used here.
 */
const toneClasses: Record<BadgeTone, string> = {
  neutral: 'bg-paper-line text-ink-soft',            // #333333 on #E2E0F0 — 9.72:1
  accent: 'bg-accent-soft text-accent',              // #512EAB on #EDECF6 — 7.70:1
  ok: 'bg-signal-ok-fill text-signal-ok',            // #008055 on #ECFDF5 — 4.73:1
  warn: 'bg-signal-warn-fill text-signal-warn',      // #BB4D00 on #FFFBEB — 4.85:1
  fail: 'bg-signal-fail-fill text-signal-fail',      // #C0152A on #FFE8EB — 5.32:1
};

export function Badge({ tone = 'neutral', className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        // `whitespace-nowrap` because these sit in fixed-width table columns:
        // `non-compliant` broke across two lines in the 104px Compliance
        // column and pushed the row out of its 44px height.
        'inline-flex items-center whitespace-nowrap px-1.5 py-0.5 rounded text-xs font-medium font-mono',
        toneClasses[tone],
        className,
      )}
      {...props}
    />
  );
}
