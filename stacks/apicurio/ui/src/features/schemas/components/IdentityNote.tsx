import { UserRound } from 'lucide-react';

/**
 * The "Owned by me" coming-soon state, formerly `YourSchemasPanel`.
 *
 * There is no signed-in user anywhere in this app — no `current_user`, no
 * OIDC, no JWT, no Entra. So this cannot be a fourth segment, and it must not
 * be faked as one: a segment that renders an empty table or a zero count
 * asserts that you own nothing, which is a different and false statement from
 * "we do not know who you are".
 *
 * It says what is missing, and points at the two things that do work today —
 * grouping by owner, and the owner facet.
 */
export function IdentityNote() {
  return (
    <section
      className="flex items-start gap-3 border border-light"
      style={{ borderRadius: 12, padding: '12px 16px' }}
    >
      <UserRound size={18} strokeWidth={1.75} className="shrink-0 mt-px text-accent" aria-hidden />
      <p className="text-[13px] leading-[1.45] text-ink-soft">
        <span className="font-semibold text-ink">Owned by me</span> becomes a fourth segment once
        sign-in lands. Until then, group or filter by owner CI.
      </p>
    </section>
  );
}
