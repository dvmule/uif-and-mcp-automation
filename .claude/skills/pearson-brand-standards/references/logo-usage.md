# Logo Usage

The Pearson corporate logo. Files are in `../assets/logos/pearson/`.

### Components

Two inseparable parts — never separate them except for the approved shorthand mark:

| Part | Description |
|---|---|
| Design accent ("ripple") | The expansive, ripple-like impact of learning |
| Wordmark | Clean, rounded letterforms — approachable, confident |

### Variants

| Variant | When to use |
|---|---|
| Primary logo (ripple + wordmark) | Default for all uses |
| Combination lockup | When a specific product/service/legal entity must be associated with Pearson |
| Shorthand mark (ripple only) | Limited to small placements only (social avatars, favicons) in already broadly Pearson-branded contexts |

### Color by background

| Background | Logo |
|---|---|
| Light | Pearson Purple logo (`assets/logos/pearson/PEARSON_LOGO_PURPLE_RGB.svg`) |
| Dark | White logo |
| Print, no color reproduction | Black — print only, never digital/web/mobile |

Never create a new logo colorway.

### Raster fallback

The logo files above are SVG. Some formats cannot embed SVG at all — notably Excel via
`openpyxl` (`XLImage`) and PowerPoint — so `assets/logos/pearson/PEARSON_LOGO_RASTER.png` is
provided for those. It is the wordmark reversed to white on a rounded Pearson Purple field —
271×66 px, RGBA, so the corners outside the rounded field are transparent.

Two things to know about it:

- Because it carries its own purple field, it sits correctly on a light background even though
  the table above would otherwise call for the purple-on-transparent logo there. Do not read the
  field as a frame added to the mark — it is how this asset is supplied.
- **Prefer the SVG whenever the format accepts it.** This PNG is small; scaling it up past its
  native width will soften the ripple. At the 100 px digital minimum it is fine.

Only use it where SVG genuinely will not go.

### Clear space & minimum size

- Clear space = height of the letter "n" in the wordmark (or the bowl height of the shorthand symbol). Nothing — no type, no graphics — inside that space. More space is always fine; less is not.
- Minimum size: print 1 in / 25 mm (shorthand 0.25 in / 6 mm); digital 100 px (shorthand 16 px, e.g. favicon).

### Placement

- Primary: top left. Secondary: bottom left. Avoid the right side entirely.
- Exception: when the logo is the sole hero element on a page, center it.

| Format | Preferred logo width |
|---|---|
| A4 | 50 mm |
| A3 | 70 mm |
| Letter | 1.25 in |
| 1920×1080 digital | 250 px |
| 1200×630 digital | 200 px |

### Never

Use the ripple alone without the wordmark (except approved lockups) · add a tagline to the logo · outline it · use alternate typefaces in combination lockups · alter the ripple in any way · place it over people/low-contrast areas/busy backgrounds · stretch, skew, or recolor it · box or frame it.
