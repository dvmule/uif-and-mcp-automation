# Typography

## Brand font: Plus Jakarta Sans

**Plus Jakarta Sans** is the sole approved typeface, company-wide. It's a geometric sans-serif with clean lines, open forms, generous spacing — readable across platforms. Weights: Light, Regular, Medium, Semibold, Bold. Open-source, free on Google Fonts.

- Supports Latin, Cyrillic, Vietnamese natively.
- For Arabic, Chinese, Japanese, Korean, etc.: use the alternative fonts from Pearson's Alternative Fonts Guide (Brand Hub) — do not substitute an arbitrary font.
- **Web/digital fallback stack:** `"Plus Jakarta Sans", Helvetica, Verdana, sans-serif`

## Hierarchy

| Level | Weight | Case | Leading | Tracking |
|---|---|---|---|---|
| Headlines / Titles | Bold, Semibold, or Medium | Sentence or Title case | 110% (1.1×) | 0 pt |
| Subheads | Semibold | Caps, Sentence, or Title case | 130% (1.3×) | 0 pt |
| Body copy | Regular or Light | Sentence or Title case | 130% (1.3×) | 0 pt |
| CTA / section titles | Regular | Caps or Sentence case | 130% (1.3×) | 0 pt |

## Rules

- Only Plus Jakarta Sans — no exceptions, no "just this once" substitutions.
- Set body text flush left. Kerning: Optical.
- Use a small, consistent set of point sizes in any one layout — don't let sizes proliferate.
- Limit all-caps to CTAs and section labels only.
- Italics: only for titles of works, vehicle/ship names, scientific/technical terms, or foreign words/phrases. Never as a general expressive/emphasis tool.
- Never expand, stretch, or distort type.
- Never use more than one color within a single headline.
- Never use a text/background color pairing that fails contrast standards (see `colors.md`).

## Ligatures — always disable

Plus Jakarta Sans web fonts ship with ligatures on by default. Turn them off everywhere except Microsoft Office apps (which don't support the setting):

```css
font-family: "Plus Jakarta Sans", Helvetica, Verdana, sans-serif;
font-variant-ligatures: none;
```

| Tool | How |
|---|---|
| CSS | `font-variant-ligatures: none;` |
| Illustrator / Photoshop | Select all text → Character panel → turn off Standard Ligatures |
| InDesign | Find/Change → Find Format (Ligatures on) → Change Format (Ligatures off) → Change All |
| After Effects | Select text clips → Text Properties → turn off Ligatures |
| Premiere Pro | Select text boxes → Type/OpenType → turn off Standard Ligatures |
| PowerPoint / Word | N/A — setting doesn't apply |

## HTML/CSS setup

Load from Google Fonts (or self-host) and apply as the base font-family. A ready `@font-face`-free Google Fonts snippet:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
```

```css
body {
  font-family: "Plus Jakarta Sans", Helvetica, Verdana, sans-serif;
  font-variant-ligatures: none;
}
```

## Power BI note

Power BI does not support custom web fonts natively. For Plus Jakarta Sans to render, it must be installed on the machine running Power BI Desktop **and** on each Power BI Service viewer's machine (or embedded via org fonts). The Pearson Power BI theme (`../assets/Pearson_PowerBI_Theme.json`) already sets it as the default font — install the font locally to see it render correctly.
