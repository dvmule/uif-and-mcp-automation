# Final Brand QA Checklist

Run this before calling any branded deliverable finished. Mark each applicable line PASS or FAIL — do not report a deliverable as done while any applicable line is FAIL. Sections marked N/A only apply if that element type is present in the deliverable (e.g., skip "Photography" if there are no images).

### Logo
- [ ] Correct logo type (color variant + file type) for the background it sits on
- [ ] Placement correct (top-left primary, bottom-left secondary; never right side)
- [ ] Sufficient clear space around the logo (≥ height of the wordmark's "n")
- [ ] Legible — not over people, low-contrast areas, or busy backgrounds

### Colors
- [ ] All colors are from the approved palette (no invented hexes)
- [ ] Color hierarchy and preferred combinations followed
- [ ] Accent colors at full strength (tints only for differentiation)
- [ ] No gradients used as a default background surface
- [ ] Correct color profile (sRGB HEX digital; Pantone/CMYK print)

### Typography
- [ ] All text in Plus Jakarta Sans only
- [ ] Text colors follow the preferred-combinations guidance
- [ ] Headline/subhead/body hierarchy followed (weight, case, leading)
- [ ] All-caps limited to CTAs/section labels
- [ ] Italics avoided except approved special cases
- [ ] Legible and accessible, especially over photography
- [ ] Ligatures disabled (CSS `font-variant-ligatures: none;` / Adobe apps) where the setting is supported

### Layout
- [ ] On-brand, 12-column grid
- [ ] Dynamic, balanced, not cluttered
- [ ] Whitespace used generously and intentionally

### Graphic waves (N/A if none used)
- [ ] Crest sits within one half of the layout
- [ ] Direction left-to-right or bottom-to-top only
- [ ] Kept separate from photography and pictograms/icons
- [ ] Only one graphic wave per layout

### Line waves (N/A if none used)
- [ ] Placement/cropping follows guidance
- [ ] Direction left-to-right or bottom-to-top
- [ ] Stroke weight follows the sizing formula
- [ ] Only approved colors (Pearson Purple, Amethyst, Light Purple, White)
- [ ] Only one line wave per composition

### Pictograms (N/A if none used)
- [ ] At most one accent-color pictogram per layout
- [ ] Correct light/dark mode assets
- [ ] Not mixed with icons in the same set

### Icons (N/A if none used)
- [ ] One icon color across the layout (unless intentionally highlighting one)
- [ ] Small accompaniments to copy, not large illustrations
- [ ] Preferred color combinations followed
- [ ] Not mixed with pictograms

### Photography (N/A if none used)
- [ ] Brand or on-brand photography
- [ ] Represents a wide, diverse audience
- [ ] Wave-effect images follow usage rules (no overlaid text, no tint/alteration)
- [ ] Any gradient treatment uses an approved colorway

### Brand messaging
- [ ] Reflects Pearson's purpose ("Helping people realize the life they imagine through learning")
- [ ] Follows tone-of-voice guidelines (empathetic, clear, direct, motivating, human)
- [ ] American English
- [ ] No passive voice, jargon, or vague claims

### Data visualization (N/A if no charts)
- [ ] Chart type matches the data relationship (sequential/diverging/qualitative)
- [ ] No color-only meaning — labels/patterns supplement color
- [ ] 1px white separators between similar-hued adjacent segments

---

## Status block

Copy and complete before signing off:

```
FINAL BRAND QA STATUS
- Pearson Logo:                PASS / FAIL
- Colors:                      PASS / FAIL
- Typography:                  PASS / FAIL
- Layout:                      PASS / FAIL
- Graphic Waves:               PASS / FAIL / N/A
- Line Waves:                  PASS / FAIL / N/A
- Pictograms:                  PASS / FAIL / N/A
- Icons:                       PASS / FAIL / N/A
- Photography:                 PASS / FAIL / N/A
- Data Visualization:          PASS / FAIL / N/A
- Brand Messaging:             PASS / FAIL
- Overall:                     PASS (only if every applicable line above is PASS)
```

If anything fails, fix it (or tell the user exactly what's failing and why) before calling the deliverable complete.
