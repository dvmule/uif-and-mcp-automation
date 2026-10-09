---
name: pearson-brand-standards
description: >-
  Applies Pearson corporate brand standards to any deliverable — HTML pages/reports, dashboards
  (Tableau, Power BI), PowerPoint decks, Word docs, Excel/data exports, email signatures, or
  written copy. Use this skill whenever creating or reviewing anything that will be seen by others
  and should look on-brand, covering color choices, fonts, logo placement, chart/data-viz colors,
  layout, or tone of voice. Trigger on requests like "make this on-brand," "add our branding,"
  "build an HTML report," "make a dashboard," "check this deck against brand guidelines," or any
  mention of Pearson colors, logo, fonts, or style guide — even if the user doesn't say "brand"
  explicitly. Always run the final QA checklist in references/qa-checklist.md before calling a
  branded deliverable done.
---

# Pearson Brand Standards

This skill is the single source of truth for making any output — code or document — comply with Pearson's corporate brand: logo, typography, full color system, photography, tone of voice, layout, and accessibility. It applies to everything, everywhere, regardless of team.

Read the reference file that matches the task. Don't load every reference for every task — pick what's relevant.

| Task involves... | Read this reference |
|---|---|
| Any color decision (UI, charts, backgrounds, text) | `references/colors.md` |
| Fonts, headings, body text, CSS | `references/typography.md` |
| Placing a logo | `references/logo-usage.md` |
| Writing copy, headlines, captions, emails | `references/voice-and-tone.md` |
| Page layout, grids, chart type selection, data viz | `references/layout-and-data-viz.md` |
| Finishing any deliverable | `references/qa-checklist.md` (always) |

Ready-to-use assets are in `assets/`: the Pearson logo (light/dark variants), a CSS custom-properties file with every brand color as a variable, and the Pearson Power BI theme file.

## Quick-reference cheat sheet

If you only remember one table, remember this one:

| Token | Value |
|---|---|
| Font | **Plus Jakarta Sans** only (fallback: Helvetica, Verdana, sans-serif). Disable ligatures in CSS: `font-variant-ligatures: none;` |
| Anchor color | Pearson Purple `#0D004D` |
| Primary supporting | White `#FFFFFF`, Light Purple `#C1BFFF`, Mist Purple `#EDECF6` |
| Secondary | Amethyst `#512EAB` |
| Accents (data viz, CTAs, icons) | Turquoise `#56E2E1`, Amber `#FFCE00`, Fuchsia `#DF41CF` |
| Min contrast | 4.5:1 normal text, 3:1 large text |
| Logo placement | Top left primary, bottom left secondary; never on the right; keep clear space = height of the "n" in the wordmark |

For anything beyond this cheat sheet, go read the relevant reference file — don't guess at hex codes or font weights from memory.

## Applying this across formats

The same brand rules apply whether the output is code or a document — only the mechanism changes:

- **HTML / CSS / web / dashboards:** Use the CSS variables in `assets/pearson-tokens.css` directly. Load Plus Jakarta Sans from Google Fonts or a self-hosted file; never substitute a different typeface. See `references/typography.md` for the exact `@font-face`/ligature setup.
- **PowerPoint / Word / Google Slides / Docs:** Apply the same hex values and Plus Jakarta Sans manually through the app's theme/style settings. Use the logo files in `assets/logos/pearson/`.
- **Power BI:** Apply `assets/Pearson_PowerBI_Theme.json` directly (View → Themes → Browse for themes…) — this sets colors, fonts, and chart defaults automatically.
- **Tableau / other BI tools:** Manually set the workbook's color palette to the data-viz scales in `references/colors.md` and match fonts/backgrounds as closely as the tool allows.
- **Plain written content (emails, reports, Slack posts):** Follow `references/voice-and-tone.md` — this has no visual component but is just as much a brand requirement.

## Before calling any deliverable done

Always run through `references/qa-checklist.md` and report a PASS/FAIL status block. Don't call something "on-brand" without actually checking it against this list.
