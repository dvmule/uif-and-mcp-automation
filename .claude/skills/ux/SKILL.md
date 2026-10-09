---
name: ux
description: Use this skill whenever the user (a UX Lead) wants to check accessibility (WCAG) compliance, verify brand/design-token compliance (Plus Jakarta Sans font, Pearson color palette), or detect drift between a Figma design component and its implemented code in a UIF domain's ui/ folder. Trigger on "check accessibility", "does this match our brand", "design system compliance", "Figma drift", "is this using the right colors/font", or a request to review a UI implementation against its UX spec.
---

# ux — mechanical design-compliance checks for UIF UX Leads

Journey mapping, spec authoring, and the final sign-off that an implementation matches spec are UX judgment calls this skill never makes. What it automates is the part that's actually mechanical: does the code use the right font and colors, does it pass an accessibility scan, and has the implemented component drifted from its Figma source.

## Brand reference (from `CLAUDE.md` — the only permitted values for visual artifacts)

- Font: **Plus Jakarta Sans**.
- Colors (HEX only): Pearson Purple `#0D004D` (primary), Light Purple `#C1BFFF`, Mist Purple `#EDECF6`, Amethyst `#512EAB` (secondary); accents Fuchsia `#DF41CF`, Amber `#FFCE00`, Turquoise `#56E2E1` (used sparingly).

## Workflow

1. **Brand/design-token lint.** Search the domain's `ui/` (and any diagrams/artifacts produced) for hex colors or font-family declarations. Flag anything outside the list above as an exact-match check, not a taste judgment — a color one shade off the palette still fails.
2. **Accessibility scan.** If the domain's UI is a running/buildable app, run an automated accessibility checker (axe-core or Lighthouse CI) against it and report WCAG violations by severity. If no build/runtime is available, state that clearly rather than guessing at compliance.
3. **Figma Code Connect drift check.** If Figma MCP tools are available in the session, use `get_code_connect_map` / `get_code_connect_suggestions` to compare the domain's implemented components against their Figma source, and report components with no mapping or a stale mapping.
4. **Report as a checklist**, not a verdict — list pass/fail per check with evidence (file:line, color found, violation description). Never mark something "approved."

## What this skill does NOT do (stays with the UX Lead)

- **User journey mapping and UX spec authoring** — always human.
- **The sign-off that an implementation matches its spec** — the skill can pre-flag visual/accessibility diffs; the "yes, this is right" call is the UX Lead's.
- **Accessibility judgment beyond the automated scan** — a scanner catches contrast ratios and missing alt text, not whether a flow is genuinely usable.
