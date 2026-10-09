// UIFPE-425: the guided transformation builder's catalog and compiler.
//
// Each entry turns a handful of small form values into ONE mapping row the
// wizard already knows how to emit — a `derived` expression, or a `literal`.
// The engine, publisher, and validation are untouched: everything compiled
// here uses only functions on the engine's expression allowlist
// (dynamic_mapping/config_validator.py ALLOWED_FUNCTIONS), and the result
// still goes through the same publish-time validation as a typed expression.
//
// Pure functions only — no React, no fetch — so vitest covers every entry.

// Single quotes double inside SQL string literals ('' is the escape).
export function sqlString(s) {
  return `'${String(s).replace(/'/g, "''")}'`;
}

const need = (v) => v !== undefined && v !== null && String(v).trim() !== "";

// Input kinds the wizard renders:
//   column   one source column (alias.col picker)
//   columns  two or more source columns (multi picker)
//   text     free text
//   number   integer
//   select   fixed options [{value, label}]
//   pairs    list of {from, to} rows (map values)
export const TRANSFORMS = [
  {
    id: "if_null",
    label: "If null / not null",
    group: "Logic",
    info: {
      what: "Checks whether the column has a value. Writes one text when it does, another when it is empty. Use it to turn a technical column into a simple flag your partner can read.",
      example: [
        ["A101703313003", "Available"],
        ["(empty)", "Not available"],
      ],
    },
    hint: "Write one text when the column has a value, another when it is null — leave a box empty to keep the column's own value on that side.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      {
        key: "whenPresent",
        kind: "text",
        label: "Text when not null (empty = keep value)",
        placeholder: "Available",
        optional: true,
      },
      {
        key: "whenMissing",
        kind: "text",
        label: "Text when null (empty = stay null)",
        placeholder: "No",
        optional: true,
      },
    ],
    validate: (v) =>
      need(v.whenPresent) || need(v.whenMissing)
        ? null
        : "fill at least one of the two texts — both empty changes nothing",
    build: (v) => {
      const thenArm = need(v.whenPresent) ? sqlString(v.whenPresent) : v.column;
      const elseArm = need(v.whenMissing) ? sqlString(v.whenMissing) : "NULL";
      return `CASE WHEN ${v.column} IS NOT NULL THEN ${thenArm} ELSE ${elseArm} END`;
    },
  },
  {
    id: "map_values",
    label: "Map values",
    group: "Logic",
    info: {
      what: "Translates stored codes into friendly labels, one pair at a time. A value with no pair keeps its original value — or gets your fallback text if you set one.",
      example: [
        ["P", "Print"],
        ["D", "Digital"],
      ],
    },
    hint: "Translate stored codes into partner labels (P → Print).",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "pairs", kind: "pairs", label: "Value → label" },
      {
        key: "fallback",
        kind: "text",
        label: "When nothing matches",
        placeholder: "leave empty to keep the original value",
        optional: true,
      },
    ],
    validate: (v) =>
      Array.isArray(v.pairs) && v.pairs.some((p) => need(p.from))
        ? null
        : "add at least one value → label pair",
    build: (v) => {
      const whens = v.pairs
        .filter((p) => need(p.from))
        .map((p) => `WHEN ${v.column} = ${sqlString(p.from)} THEN ${sqlString(p.to ?? "")}`)
        .join(" ");
      const elseArm = need(v.fallback) ? sqlString(v.fallback) : v.column;
      return `CASE ${whens} ELSE ${elseArm} END`;
    },
  },
  {
    id: "default_when_empty",
    label: "Default when empty",
    group: "Logic",
    info: {
      what: "Fills empty values with a default text, so this field is never blank in the export.",
      example: [
        ["en", "en"],
        ["(empty)", "unknown"],
      ],
    },
    hint: "Fill empty values with a default.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "defaultValue", kind: "text", label: "Default value", placeholder: "unknown" },
    ],
    build: (v) => `coalesce(${v.column}, ${sqlString(v.defaultValue)})`,
  },
  {
    id: "date_format",
    label: "Change date format",
    group: "Dates",
    info: {
      what: "Changes how a date is written. Say how the date is stored (skip that when the column is already a real date) and how the partner wants it.",
      example: [["2026-09-11", "11/09/2026"]],
    },
    hint: "Reformat a date. Leave 'stored as' empty when the column is already a date.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      {
        key: "fromFormat",
        kind: "text",
        label: "Stored as (empty = already a date)",
        placeholder: "yyyy-MM-dd",
        optional: true,
      },
      { key: "toFormat", kind: "text", label: "Export as", placeholder: "dd/MM/yyyy" },
    ],
    build: (v) =>
      need(v.fromFormat)
        ? `date_format(to_date(${v.column}, ${sqlString(v.fromFormat)}), ${sqlString(v.toFormat)})`
        : `date_format(${v.column}, ${sqlString(v.toFormat)})`,
  },
  {
    id: "combine",
    label: "Combine columns",
    group: "Text",
    info: {
      what: "Joins two or more columns into one field, with a separator you choose — or none.",
      example: [["Science · Physics", "Science / Physics"]],
    },
    hint: "Join several columns into one text with a separator.",
    inputs: [
      { key: "columns", kind: "columns", label: "Columns (in order)" },
      {
        key: "separator",
        kind: "text",
        label: "Separator (empty = none; a space works)",
        placeholder: " / ",
        optional: true,
        keepSpaces: true,
      },
    ],
    validate: (v) =>
      Array.isArray(v.columns) && v.columns.length >= 2 ? null : "pick at least two columns",
    build: (v) => `concat_ws(${sqlString(v.separator ?? "")}, ${v.columns.join(", ")})`,
  },
  {
    id: "split_part",
    label: "Split / take part",
    group: "Text",
    info: {
      what: "Splits the value on a separator and keeps one part — part 1 is the first.",
      example: [["978-013-468", "013"]],
    },
    hint: "Split on a separator and keep one part (1 = first).",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "separator", kind: "text", label: "Separator (a space works)", placeholder: "-", keepSpaces: true },
      { key: "part", kind: "number", label: "Part number", placeholder: "1", min: 1 },
    ],
    build: (v) => `split_part(${v.column}, ${sqlString(v.separator)}, ${Number(v.part)})`,
  },
  {
    id: "change_case",
    label: "Change case / trim",
    group: "Text",
    info: {
      what: "Makes the text UPPERCASE, lowercase, or Title Case — and can remove spaces around it first.",
      example: [["  physics ", "PHYSICS"]],
    },
    hint: "UPPERCASE, lowercase, or Title Case — optionally trim spaces first.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      {
        key: "mode",
        kind: "select",
        label: "Case",
        options: [
          { value: "upper", label: "UPPERCASE" },
          { value: "lower", label: "lowercase" },
          { value: "initcap", label: "Title Case" },
          { value: "none", label: "keep case" },
        ],
      },
      {
        key: "trim",
        kind: "select",
        label: "Trim spaces",
        options: [
          { value: "yes", label: "yes" },
          { value: "no", label: "no" },
        ],
      },
    ],
    validate: (v) =>
      v.mode === "none" && v.trim !== "yes" ? "pick a case change or trim — this does nothing" : null,
    build: (v) => {
      let expr = v.column;
      if (v.trim === "yes") expr = `trim(${expr})`;
      if (v.mode !== "none") expr = `${v.mode}(${expr})`;
      return expr;
    },
  },
  {
    id: "find_replace",
    label: "Find and replace",
    group: "Text",
    info: {
      what: "Finds text and replaces it — plain text, or a pattern for advanced cases. Replace with nothing to delete it.",
      example: [["Maths & Science", "Maths and Science"]],
    },
    hint: "Replace text — plain, or a regular expression pattern.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "find", kind: "text", label: "Find", placeholder: "&" },
      { key: "replaceWith", kind: "text", label: "Replace with", placeholder: "and", optional: true, allowEmpty: true },
      {
        key: "regex",
        kind: "select",
        label: "Pattern type",
        options: [
          { value: "plain", label: "plain text" },
          { value: "regex", label: "regular expression" },
        ],
      },
    ],
    build: (v) => {
      const fn = v.regex === "regex" ? "regexp_replace" : "replace";
      return `${fn}(${v.column}, ${sqlString(v.find)}, ${sqlString(v.replaceWith ?? "")})`;
    },
  },
  {
    id: "substring",
    label: "Substring",
    group: "Text",
    info: {
      what: "Keeps only part of the text — pick where to start (1 = first character) and how many characters to keep.",
      example: [["9780134685991", "978"]],
    },
    hint: "Keep part of the text (start counts from 1).",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "start", kind: "number", label: "Start at", placeholder: "1", min: 1 },
      { key: "length", kind: "number", label: "Length", placeholder: "10", min: 1 },
    ],
    build: (v) => `substring(${v.column}, ${Number(v.start)}, ${Number(v.length)})`,
  },
  {
    id: "pad_to_width",
    label: "Pad to fixed width",
    group: "Text",
    info: {
      what: "Makes every value the same width by adding a fill character — useful for fixed-width partner files.",
      example: [["42", "0000000042"]],
    },
    hint: "Pad with a character until the value is exactly this wide.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "width", kind: "number", label: "Width", placeholder: "10", min: 1 },
      { key: "padChar", kind: "text", label: "Pad with", placeholder: "0", keepSpaces: true },
      {
        key: "side",
        kind: "select",
        label: "Side",
        options: [
          { value: "left", label: "left (lpad)" },
          { value: "right", label: "right (rpad)" },
        ],
      },
    ],
    build: (v) =>
      `${v.side === "right" ? "rpad" : "lpad"}(${v.column}, ${Number(v.width)}, ${sqlString(v.padChar)})`,
  },
  {
    id: "round_number",
    label: "Round / format number",
    group: "Numbers",
    info: {
      what: "Rounds a number to a set number of decimals — or formats it as text with thousand separators.",
      example: [["1234.567", "1,234.57"]],
    },
    hint: "Round to N decimals, or format with thousand separators.",
    inputs: [
      { key: "column", kind: "column", label: "Column" },
      { key: "decimals", kind: "number", label: "Decimals", placeholder: "2", min: 0 },
      {
        key: "mode",
        kind: "select",
        label: "Result",
        options: [
          { value: "round", label: "number (round)" },
          { value: "format", label: "text with separators (format_number)" },
        ],
      },
    ],
    build: (v) =>
      v.mode === "format"
        ? `format_number(${v.column}, ${Number(v.decimals)})`
        : `round(${v.column}, ${Number(v.decimals)})`,
  },
  {
    id: "mask_hash",
    label: "Mask (hash)",
    group: "Privacy",
    info: {
      what: "Replaces a sensitive value with an irreversible code. The partner can still match rows on it, but can never read the real value.",
      example: [["A101703313003", "9f8a3c1b…"]],
    },
    hint: "Ship an irreversible hash — still joinable, never readable.",
    inputs: [{ key: "column", kind: "column", label: "Column" }],
    build: (v) => `sha2(cast(${v.column} as string), 256)`,
  },
  {
    id: "fixed_value",
    label: "Fixed value",
    group: "Other",
    info: {
      what: "Puts the same fixed text on every row — for example a source-system tag the partner asked for.",
      example: [["(every row)", "gold-product"]],
    },
    hint: "The same constant on every row (e.g. a source-system tag).",
    inputs: [{ key: "value", kind: "text", label: "Value", placeholder: "gold-product" }],
    // literal rule, not an expression — the one entry that skips `derived`
    build: (v) => ({ literal: v.value }),
  },
];

export const transformById = (id) => TRANSFORMS.find((t) => t.id === id);

// Display order of the popup's grouped picker (UIFPE-425 v2).
export const TRANSFORM_GROUPS = ["Logic", "Dates", "Text", "Numbers", "Privacy", "Other"];

// Compile one menu entry + its form values into the mapping row to add.
// Returns { ok: true, row } or { ok: false, error } — never throws.
export function buildTransform(id, values, target) {
  const t = transformById(id);
  if (!t) return { ok: false, error: `unknown transformation ${id}` };
  if (!need(target)) return { ok: false, error: "name the target field" };

  for (const input of t.inputs) {
    if (input.optional || input.allowEmpty) continue;
    const v = values[input.key];
    const missing =
      input.kind === "columns" || input.kind === "pairs"
        ? !Array.isArray(v) || v.length === 0
        : input.keepSpaces
          ? v === undefined || v === null || String(v) === ""
          : !need(v);
    if (missing) return { ok: false, error: `fill in "${input.label}"` };
    if (input.kind === "number" && (!/^\d+$/.test(String(v).trim()) || Number(v) < (input.min ?? 0)))
      return { ok: false, error: `"${input.label}" must be a whole number${input.min ? ` of at least ${input.min}` : ""}` };
  }
  if (t.validate) {
    const problem = t.validate(values);
    if (problem) return { ok: false, error: problem };
  }

  const built = t.build(values);
  const row =
    typeof built === "object" && built !== null && "literal" in built
      ? { type: "literal", target: target.trim(), value: built.literal }
      : { type: "derived", target: target.trim(), expression: built };
  return { ok: true, row };
}

// Preview text for the live <code> block — the expression (or literal) the
// current form values would produce, or null while the form is incomplete.
export function previewTransform(id, values) {
  const r = buildTransform(id, values, "preview");
  if (!r.ok) return null;
  return r.row.type === "literal" ? `(fixed value) ${sqlString(r.row.value)}` : r.row.expression;
}

// A sensible default target name: the picked column's own name (last path
// segment). The author can always overwrite it — including back to the
// source attribute's name, which "replaces" that attribute in the export.
export function suggestTarget(values) {
  const col = values.column || (Array.isArray(values.columns) ? values.columns[0] : "");
  if (!col) return "";
  const parts = String(col).split(".");
  return parts[parts.length - 1] || "";
}
