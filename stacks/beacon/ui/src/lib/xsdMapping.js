// xsdMapping.js (UIFPE-616)
// When an author uploads a partner XSD for an XML export, the XSD is the
// contract: the Mapping step's rows come from it (UIFPE-566), may not be
// removed, and no extra elements may be added. An OPTIONAL element may be
// "left out" — nothing is mapped for it, so the element is not written (the
// XML writer skips an element with no value). A REQUIRED one never can be:
// the partner would reject the file, and publish already refuses it.
//
// Also the inline-edit rules the Mapping table uses with or without an XSD:
// the Expression/Source cell is always editable, the target only without an
// XSD.

/** Is the Mapping step held to an uploaded XSD? */
export function schemaLocked(outputFormat, partnerSchema, guide) {
  return outputFormat === "xml" && Boolean(partnerSchema?.id) && (guide?.paths?.length || 0) > 0;
}

function pathOf(guide, target) {
  return (guide?.paths || []).find((p) => p.path === target) || null;
}

/** Does the XSD declare this target (a mapped element or attribute path)? */
export function isSchemaTarget(guide, target) {
  if (!target) return false;
  return Boolean(pathOf(guide, target)) || (guide?.declared || []).includes(target);
}

/** May the author remove this row? Always without an XSD; with one, only a
 *  row the XSD does not declare (what a strict partner would reject). */
export function canRemoveRow(locked, guide, target) {
  return !locked || !isSchemaTarget(guide, target);
}

/** May this row be left out? Only an optional path of the XSD. */
export function canLeaveOut(guide, target) {
  const p = pathOf(guide, target);
  return Boolean(p) && !p.required;
}

/** Left-out paths that still mean something for this XSD — optional paths
 *  it declares. A saved list from another schema version drops the rest. */
export function validLeftOut(guide, leftOut) {
  return [...new Set(leftOut || [])].filter((t) => canLeaveOut(guide, t));
}

/** The text the Expression/Source cell shows for a mapping row. */
export function mappingText(m) {
  if (!m) return "";
  if (m.expression) return m.expression;
  if (m.source) return m.source;
  return m.value == null ? "" : String(m.value);
}

// UIFPE-718: a mapping row counts as valid once it has a target and a
// source (a column, an expression or a fixed value); the count line under
// the table sums the rows up the way the prototype does
export function mappingFilled(m) {
  return Boolean(String(m?.target || "").trim() && String(mappingText(m) || "").trim());
}
export function mappingCountLine({ mappings, pending = 0, unresolved = 0, shape = "" }) {
  const valid = mappings.filter(mappingFilled).length;
  const toFill = mappings.length - valid + pending;
  const total = mappings.length + pending;
  return [
    `${total} field${total === 1 ? "" : "s"}`,
    `${valid} valid`,
    toFill ? `${toFill} to fill in` : "",
    unresolved ? `${unresolved} unresolved` : "",
    shape,
  ]
    .filter(Boolean)
    .join(" · ");
}

const COLUMN_RE = /^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$/;

/** A row from typed text — the same column-vs-expression rule the Add row
 *  and pending rows use: `alias.column` is a column, anything else Spark SQL. */
export function mappingFromText(text, target) {
  const expression = String(text || "").trim();
  if (!expression) return null;
  return COLUMN_RE.test(expression)
    ? { type: "one_to_one", source: expression, target }
    : { type: "derived", expression, target };
}

/** The row after an inline source edit. Unchanged text keeps the row as it
 *  is (a literal or fx-built row stays itself); blank text is refused. */
export function editSource(m, text) {
  const next = String(text || "").trim();
  if (!next) return { error: "A source or expression is required." };
  if (next === mappingText(m).trim()) return { mapping: m, changed: false };
  return { mapping: mappingFromText(next, m.target), changed: true };
}

/** The error for renaming row `index` to `name`, or null when it is fine. */
export function renameError(mappings, index, name) {
  const next = String(name || "").trim();
  if (!next) return "A target name is required.";
  const clash = (mappings || []).some(
    (m, j) => j !== index && (m.target === next || (m.targets || []).includes(next))
  );
  return clash ? `Another row already writes "${next}".` : null;
}
