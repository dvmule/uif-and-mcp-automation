# Color

Full brand color system. Machine-readable CSS variables for all of this live in `../assets/pearson-tokens.css` — use that file directly for HTML/CSS work instead of retyping hex codes.

## Primary colors

| Name | Hex | RGB | PMS | CMYK |
|---|---|---|---|---|
| **Pearson Purple** *(anchor — use most heavily)* | `#0D004D` | 13, 0, 77 | 2755 C | 95, 100, 0, 65 |
| White | `#FFFFFF` | 255, 255, 255 | N/A | 0, 0, 0, 0 |
| Light Purple | `#C1BFFF` | 193, 191, 255 | 6085 C | 19, 14, 0, 0 |
| Mist Purple | `#EDECF6` | 237, 236, 246 | 663 C | 6, 5, 0, 0 |

## Secondary color

| Name | Hex | RGB | PMS | CMYK |
|---|---|---|---|---|
| Amethyst | `#512EAB` | 81, 46, 171 | 2098 C | 88, 100, 0, 0 |

Use to complement the primary palette and emphasize content shifts or key messaging. Can be a background in text boxes, infographics, or web pages — never full-bleed.

## Accent colors

| Name | Hex | RGB | PMS | CMYK |
|---|---|---|---|---|
| Turquoise | `#56E2E1` | 86, 226, 225 | 3105 C | 60, 0, 15, 0 |
| Amber | `#FFCE00` | 255, 206, 0 | 116 C | 0, 15, 100, 0 |
| Fuchsia | `#DF41CF` | 223, 65, 207 | 2385 C | 10, 90, 0, 0 |

Tints (use only when extra differentiation is needed — never as a default replacement for full-strength): Turquoise Tint `#B3F2F1`, Amber Tint `#FFE67F`, Fuchsia Tint `#EFA0E7`.

## Tertiary / special use

| Name | Hex | Use |
|---|---|---|
| UX Black | `#333333` | Body copy fallback in limited digital/UX contexts |
| Black | `#000000` | Print only when color reproduction isn't feasible — never digital |

## Color hierarchy (use in this order of visual weight)

1. **Pearson Purple** — anchor; backgrounds, overlays, primary graphic elements.
2. **Primary colors** (White, Light Purple, Mist Purple) — supporting backgrounds and layout foundations.
3. **Amethyst** — secondary; text-box/infographic/web-page backgrounds (not full-bleed).
4. **Accent colors** (Turquoise, Amber, Fuchsia) — text accents, icons, CTAs, data visualization. Full-strength by default.

## Preferred combinations (ADA compliant)

**Preferred / most widely used:** White on Pearson Purple · White on Amethyst · Light Purple on Pearson Purple · Pearson Purple on Light Purple · Pearson Purple on White · Pearson Purple on Mist Purple · Amethyst on Mist Purple · Amethyst on White

**Accent combos (minor treatments / icons only):** Amber on Pearson Purple · Amber on Amethyst · Turquoise on Pearson Purple · Turquoise on Amethyst · Pearson Purple on Turquoise · Fuchsia on Pearson Purple · Pearson Purple on Fuchsia

**Limited use (CTAs and small accents only):** Pearson Purple on Amber

## Background schemes

| Scheme | Background | Primary type color | Supporting |
|---|---|---|---|
| Dark | Pearson Purple (or wave image) | White | Amethyst, White, one secondary accent |
| Light | Light Purple (solid or tint) | Pearson Purple | White, Pearson Purple, one secondary accent |

## Accessibility

- Minimum contrast: **4.5:1** normal text, **3:1** large text.
- Never rely on color alone to convey meaning — pair with labels, patterns, or shapes.
- Never use a color combination that fails contrast standards, even if it's in the palette.

## Color profile rules

- **Digital/UX/static assets:** sRGB, HEX values only. Never CMYK/Pantone in digital work. Never Adobe RGB / ProPhoto RGB / Display P3.
- **Video/motion:** sRGB throughout (After Effects defaults to this — don't change it).
- **Print:** Pantone for target color, CMYK for 4-color process, physical proofs before full runs.

## Do not

- Use gradient backgrounds as a default surface.
- Use tints as a replacement for full-strength colors (tints = differentiation only).
- Invent new brand colors, even close variants.
- Use CMYK or Pantone values in digital work.

---

## Data visualization palette

Priority order for chart color use: **Turquoise → Amber → Fuchsia → Amethyst**. Each has a 10-stop scale (core, 2 dark, 2 mid, 5 light) for sequential charts — see `../assets/pearson-tokens.css` for every stop as a CSS variable.

| Family | Core |
|---|---|
| Turquoise | `#56E2E1` |
| Amber | `#FFCE00` |
| Fuchsia | `#DF41CF` |
| Amethyst | `#512EAB` |

**Chart type guidance:**

| Type | Use when | Approach |
|---|---|---|
| Sequential | Clear order/progression (temperature, income, time) | Light → dark for the value range |
| Diverging | Critical midpoint, extremes matter (profit/loss, sentiment) | Diverge from center; light center, dark extremes |
| Qualitative | Distinct categories, no inherent order | Different hues per category |

**Best practices:** simplest chart that tells the story · direct labels over legend keys where possible · never color alone to convey meaning · add a 1px white stroke between same-hued bars/segments that would otherwise merge · use **patterns** (crosshatch, diagonal lines), not invented colors, for extra categories · minimize non-data ink · sRGB HEX always.
