import type { Config } from 'tailwindcss';

/**
 * Pearson March 2025 palette. Every value here is sourced from a design file,
 * not from memory: the brand and surface tokens from the stylesheet shipped
 * with the Schema Registry design handoff (`_ds/…/colors_and_type.css`), and
 * the `signal` status tones from the Relay `Echo-Designs` Figma library
 * (file h3PKd8lIAgUSYHcZ8DXCEe, published variables), so that the two RELAY
 * surfaces read as one product.
 *
 * The semantic names (`ink`, `paper`, `accent`, `signal`) predate that palette
 * and are kept: ~400 call sites reference them, and renaming them would have
 * turned a palette change into an app-wide rewrite. Only the values moved.
 *
 * The `signal` tones each carry an explicit `-fill`, and the pairs must stay
 * paired: they used to be rendered as `bg-signal-ok/10` — the foreground at
 * 10% alpha over whatever sat behind it — which on a tinted table row was a
 * third colour again, and never the one that was contrast-checked.
 *
 * Status now follows Echo rather than the Schema Registry handoff. Echo's
 * three status hues are its published `Accent Colors` variables, and its
 * `-fill` values are the real chip backgrounds from its Cards component:
 *
 *   ok      Success     #009966   fill #ECFDF5
 *   warn    Dark Amber  #BB4D00   fill #FFFBEB  (its own "Amber Tint")
 *   fail    Error       #C0152A   fill #FFE8EB
 *
 * One value here is NOT Echo's, and the reason matters. Echo only ever puts
 * these hues on a 32px SemiBold figure or a 24px icon, where WCAG AA asks for
 * 3:1 — and #009966 clears that at 3.65:1. This app puts them on 11px badge
 * text, where AA asks 4.5:1, and there #009966 fails at 3.47:1 on its own
 * fill. So `ok` is one step darker on Echo's identical hue and saturation
 * (hsl 160, 100%, 25% rather than 30%) and `ok-bold` carries Echo's exact
 * value for the icon and large-figure uses that Echo itself designed for.
 * Both readings are the same green; only the lightness moves.
 *
 * Measured pairs, at the size each is actually used:
 *
 *   ok      #008055 on #ECFDF5    4.73:1   (11px text, needs 4.5)
 *   ok-bold #009966 on #FFFFFF    3.65:1   (15px icon, needs 3.0)
 *   warn    #BB4D00 on #FFFBEB    4.85:1   (11px text, needs 4.5)
 *   fail    #C0152A on #FFE8EB    5.32:1   (11px text, needs 4.5)
 *   accent  #512EAB on #EDECF6    7.70:1
 *   neutral #333333 on #E2E0F0    9.72:1
 *   match   #0D004D on #FFE67F   11.36:1
 *
 * The previous set was #0D004D/#7F6701/#7B1571 — a hueless "ok", olive and
 * fuchsia. It passed AA too; it was replaced because Relay ships Echo's
 * green/amber/red and two surfaces of one product should not disagree about
 * what red means.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'Helvetica', 'Verdana', 'sans-serif'],
        // Pearson specifies no mono face. This is the system stack the design
        // prototype itself used, and the one the brand token file names as the
        // fallback "if absolutely required". Nothing is fetched for it, and it
        // has a real 600 weight — DM Mono, which this replaced, has none, so
        // every `font-mono font-semibold` was being synthesised.
        mono: ['ui-monospace', 'SFMono-Regular', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        ink: {
          DEFAULT: '#0D004D',   // Pearson Purple — body text, header, primary pill
          soft: '#333333',      // UX Black — secondary body text
          mute: '#5A5377',      // Derived muted — labels, meta, placeholders on light
          // Echo's `Tertiary/Placeholder` hue, darkened — the same correction
          // `signal.ok` needed, for the same reason. Echo's own #90A1B9 on its
          // own #F8FAFC field measures 2.51:1, which is under AA for the 14px
          // placeholder AND under the 3:1 non-text floor for the search glyph
          // beside it. This holds Echo's hue and saturation (hsl 215, 23%) and
          // takes lightness from 64.5% to 46%, giving 4.77:1.
          //
          // Placeholder text is the only visible label this field has, so it
          // is text a sighted user reads, not decoration. Only for placeholders
          // on that field ground — `ink.mute` stays the muted colour for
          // labels and meta on the page.
          placeholder: '#5B7190',
        },
        paper: {
          DEFAULT: '#EDECF6',   // Mist Purple — page background
          card: '#FFFFFF',
          raised: '#F7F6FB',    // Surface tint — table headers, gutters, hover
          line: '#E2E0F0',      // Border subtle — card borders
          divider: '#F1F0F7',   // Table row rules
          group: '#F3F0FF',     // Grouped-table header rows
          // Echo's header chrome, from its Header and SearchBar components:
          // a near-white field ground and a true-neutral hairline. These sit
          // alongside `line` (#E2E0F0) rather than replacing it — `line` is
          // the purple-tinted card border the rest of the app uses, and only
          // the chrome Echo specifies takes the neutral one.
          field: '#F8FAFC',
          edge: '#E0E0E0',
        },
        accent: {
          DEFAULT: '#512EAB',   // Amethyst — links, mono values, icons
          soft: '#EDECF6',      // Mist Purple — active/neutral chip ground
        },
        // Light Purple. Borders on light, and muted text on the purple header,
        // where the light-field muted purple (#5A5377) is only 2.1:1.
        light: '#C1BFFF',
        amber: {
          DEFAULT: '#FFCE00',   // Count pill, graph match stroke
          tint: '#FFE67F',      // Search-match highlight
          row: '#FFF9E0',       // Matched-row tint
        },
        // Echo's Left Nav Panel: Pearson Purple ground, and the one step up it
        // uses for the active item's pill. #201360 is Echo's own value, taken
        // from the component — it is not a derived tint of the ground.
        sidebar: {
          DEFAULT: '#0D004D',
          active: '#201360',
        },
        signal: {
          // Echo's hue at 25% lightness instead of 30%, for 11-12px text.
          ok: '#008055',
          // Echo's exact Success. Icons and figures 24px and up only — it does
          // not clear AA on small text, which is the whole reason `ok` exists.
          'ok-bold': '#009966',
          'ok-fill': '#ECFDF5',
          warn: '#BB4D00',
          'warn-fill': '#FFFBEB',
          fail: '#C0152A',
          'fail-fill': '#FFE8EB',
          // Row tint for a non-compliant row. Derived, not from Echo: Echo has
          // no table-row status tint, so this is its #FFE8EB chip fill taken
          // lighter, far enough that a badge sitting on the row keeps the
          // measured ratio above (#C0152A on it is 5.81:1).
          'fail-row': '#FFF5F6',
        },
      },
      // Tailwind's own radius scale is already 4 / 8 / 12 / 16 at
      // DEFAULT / lg / xl / 2xl, which is exactly the design's scale — inputs
      // and chips, insets and code panes, notices, cards. The previous
      // `DEFAULT: '6px'` override is gone rather than restated, so bare
      // `rounded` now means the 4px the design asks for.
      boxShadow: {
        sm: '0 1px 2px rgba(13,0,77,.06)',
        DEFAULT: '0 2px 6px rgba(13,0,77,.07)',
        lg: '0 8px 24px rgba(13,0,77,.10)',
        xl: '0 16px 40px rgba(13,0,77,.14)',
        // Purple-tinted only, never grey or black. 2xl is the deepest the
        // design uses, and it is the same value as the menu shadow.
        '2xl': '0 16px 40px rgba(13,0,77,.14)',
      },
    },
  },
  plugins: [],
} satisfies Config;
