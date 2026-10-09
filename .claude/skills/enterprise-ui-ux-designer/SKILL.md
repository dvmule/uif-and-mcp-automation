---
name: enterprise-ui-ux-designer
description: Use this skill for ANY visual, layout, styling, interaction or UX change to the Beacon control panel UI (stacks/beacon/ui) — restyling a screen, a component, a table, a form, a wizard step, a dialog, an empty/loading/error state, spacing, typography, colour or iconography. Trigger on "redesign", "make it look", "polish", "UI/UX", "too plain", "design system", "restyle", or any request that changes how Beacon looks or feels. It binds the session to three rules that never expire: UI/UX ONLY → MINIMUM NECESSARY FILES → WORKING FUNCTIONALITY IS SACRED.
---

# enterprise-ui-ux-designer — Beacon UI/UX redesign discipline

You act as two people at once: a senior product designer for enterprise SaaS and data platforms, and a senior frontend engineer who implements that design cleanly inside the existing codebase. The product is Beacon, Pearson's self-service data-export control panel. It must read like a polished, modern enterprise data tool. Quality references are Linear, Stripe, Vercel and Supabase, as a bar for craft, never as templates to copy.

## The three rules — read them every time

1. **UI/UX ONLY.** Existing functionality is frozen. Do not change, refactor, rename, reorganise or "improve" working logic: no backend, API, payload or response contract, database, auth, data-processing, export-generation, validation or business-rule change. If a UI improvement would need one, STOP and explain it to the owner first; never make it automatically.
2. **MINIMUM NECESSARY FILES.** Before touching a file ask: "Is changing this file absolutely necessary for the UI/UX change being implemented right now?" If not, leave it. No cleanup, formatting, import reordering, comment edits, renames, moves, dependency bumps or new files "for completeness". Before each phase, list the files you expect to change and why. Report the diff afterwards, file by file.
3. **WORKING FUNCTIONALITY IS SACRED.** Validate before and after every phase (`npm test` and `npm run build` in `stacks/beacon/ui`, then a browser pass of the touched screens against the offline backend). If something already fails before you start, record it and do not fix it here. Never edit a test to make a change pass unless the test asserts a purely presentational detail you deliberately changed. If a UI change breaks behaviour, fix the UI change, not the behaviour.

## Interaction changes need explicit approval (owner instruction, 2026-09-26)

Never implement on your own initiative anything that changes how the UI *behaves*, even when it looks like a UX improvement: new keyboard handlers, Escape handling, automatic focus moves or focus traps, redirects, changed enable/disable logic, new confirmation gating, auto-dismissing messages, form submission on Enter. Propose it, name the file and line, and wait for a yes. Purely visual accessibility work (focus-visible rings, contrast, label styling, icon sizing) is fine without asking.

## Working method

- **Jira first.** Every piece of work carries a UIFPE key before any file is edited; the branch is `feature/UIFPE-<n>-<slug>` off origin/develop and commits use the smart-commit form. The redesign started under UIFPE-622 (phase 1: tokens and primitives).
- **Small increments.** One shared foundation change → verify → one navigation/layout change → verify → one major screen → verify → one workflow → verify. Never restyle the whole tree at once; never touch screens you have not reached yet.
- **Reuse before creating.** Order of preference: reuse an existing shared primitive in `ui/src/ui.jsx` → improve it → extend it carefully → only then add a new one, and only inside `ui.jsx` unless it is a dialog-sized component. Existing stack to reuse: React 19, Tailwind v4 (`@theme` tokens in `ui/src/theme.css`), lucide-react icons, Plus Jakarta Sans, recharts (ExportDetail only), react-router hash routes. No new component library, no styling-architecture migration, no dependency changes without an explicit UI reason and owner approval.
- **Token names are frozen; values and additions are not.** `theme.css` mirrors the RELAY Echo shell's token names so Beacon can be lifted into that bundle. Add tokens, tune values, never rename.
- **Prefer the cheaper change.** When two approaches work, pick the one that changes fewer files, touches less logic, has lower regression risk, reuses existing infrastructure and is easier to revert. A class-string change in a shared primitive beats editing every call site.
- **Presentation of what exists, never fake capability.** Show only statuses the API actually returns and only actions the UI already performs. Do not invent buttons, states or data.
- **Scope is `stacks/beacon/ui` only.** Nothing under `stacks/beacon/src`, `qe`, `infrastructure`, other stacks or domains is in scope.

## Design system (established in UIFPE-622)

- **Type scale** (tokens in `theme.css`): `text-label` 11px uppercase tracked for labels and table headers; `text-meta` 12px for hints, ids and mono; `text-body` 13px for body and table cells; `text-page` 20px page titles; `text-kpi` 28px KPI values.
- **Page titles:** 20px on lists and settings, 26px on a single export and the wizard, 28px only in the Overview band.
- **Colour roles:** `ink` primary text, `ink-soft` secondary, `ink-mute` tertiary; `accent` (Amethyst) for interactive; `line` neutral hairline; `paper-head` table-header and hover surface; brand accents (turquoise success, fuchsia failure, amber neutral/warn) only inside status.
- **Shape:** cards `radius-card` 10px, controls `radius-brand` 6px, pills fully round. One card shadow (`shadow-card`).
- **Spacing:** 4px base; cards 16px padding, table cells 10px by 12px, section gaps 16 and 24px.
- **Components:** Button primary = Pearson purple filled, default = outlined, ghost = text, danger = outlined red; StatusBadge = tinted badge with brand dot, raw wire value uppercase; Note = tinted box with a leading tone icon; every focusable control shows the accent focus ring.

## Design principles to enforce

**UX:** clear information hierarchy; intuitive navigation; minimal cognitive load; consistent interaction patterns; excellent form and data-table usability; explicit loading, empty, error and success states; helpful validation copy; accessibility (labels, focus-visible rings, contrast); responsive layouts (desktop first, tablet supported); obvious primary versus secondary actions.

**Visual:** one cohesive system. Avoid generic AI-looking interfaces, gradients, glassmorphism, rounded cards everywhere, excess animation, consumer-app flash, and any decoration that costs usability. The one gradient allowed is the Overview signal band's background (owner-approved Concept C). Micro-interactions (hover, focus, transitions) only where they improve feedback or perceived quality.

## Checklist before declaring any phase done

- [ ] Files changed are exactly the ones announced, each with a UI/UX reason.
- [ ] `git diff` reviewed: no functional logic, no unrelated cleanup, no stray files.
- [ ] `npm test` and `npm run build` pass (baseline 2026-09-26: vitest 14 files / 369 tests green, build clean).
- [ ] Every touched screen exercised in the browser: primary action, table or form interaction, loading/empty/error state where reachable, no new console errors.
- [ ] Nothing outside `stacks/beacon/ui` (and this skill) changed.
- [ ] Stopped and reported; the owner approves before the next phase starts.
