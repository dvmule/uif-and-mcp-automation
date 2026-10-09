/**
 * ADR-0023 subject grammar: `{domain}.{entity}.{type}`.
 *
 * ADVISORY ONLY. The server is the authority — `metadata_validator.py` is the
 * one thing that can reject a publish. This exists for while-you-type
 * feedback, which a per-keystroke round trip would not give.
 *
 * Kept honest by `subject-shape.test.ts`, which loads
 * `stacks/apicurio/standards/subject-naming.cases.json` — the same corpus the
 * BFF's pytest suite loads. A regex that drifts in one language fails that
 * language's tests.
 *
 * This replaced the Kafka subject shape,
 * `/^[a-z0-9]+(\.[a-z0-9-]+)+-(value|key)$/`, which matched only the demo seed
 * data and so warned on every real name in the estate: all the on-disk
 * coordinates, the normative `<entity>-<action>-data`, and ADR-0013's form.
 */
export const SUBJECT_CHANNELS = ['api', 'event', 'message'] as const;
export const SUBJECT_OPERATIONS = ['created', 'updated'] as const;

/** mapping and gold, plus every channel crossed with every operation. */
export const SUBJECT_TYPES = [
  'mapping',
  'gold',
  ...SUBJECT_CHANNELS.flatMap((channel) =>
    SUBJECT_OPERATIONS.map((operation) => `${channel}-${operation}`),
  ),
] as const;

/**
 * The bare channel tokens ADR-0023 shipped with, kept so the old coordinates
 * still warn. `claimsSubjectGrammar` tests the third segment against the
 * vocabulary, so dropping these would make `product.product.api` unclaimed and
 * therefore silent — the one outcome a migration cannot afford.
 */
export const SUPERSEDED_TYPES = SUBJECT_CHANNELS;

export const SUBJECT_SHAPE = new RegExp(
  `^[a-z][a-z0-9]*\\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*\\.(?:${SUBJECT_TYPES.join('|')})$`,
);

/**
 * Whether this rule has authority over a subject.
 *
 * The registry holds artifacts under other standards — the normative
 * `<entity>-<action>-data`, `uif/pearson-event-envelope`, OpenAPI contracts,
 * capability artifacts like `legal:vocabulary`. Warning on those would train
 * the reader to ignore the warning, so the third segment being one of the five
 * type tokens is the test. It mirrors the server rule's scope exactly.
 */
export function claimsSubjectGrammar(artifactId: string): boolean {
  const parts = artifactId.split('.');
  if (parts.length < 3) return false;
  return (
    (SUBJECT_TYPES as readonly string[]).includes(parts[2]) ||
    (SUPERSEDED_TYPES as readonly string[]).includes(parts[2])
  );
}

/** The advisory message, or null when there is nothing to say. */
export function subjectWarningFor(artifactId: string): string | null {
  if (!artifactId) return null;
  if (SUBJECT_SHAPE.test(artifactId)) return null;
  if (!claimsSubjectGrammar(artifactId)) return null;
  const type = artifactId.split('.')[2];
  if ((SUPERSEDED_TYPES as readonly string[]).includes(type)) {
    return (
      `The type '${type}' is superseded: a channel is now registered per ` +
      `operation, so use '${type}-created' or '${type}-updated'. The server ` +
      `will reject it.`
    );
  }
  return (
    `This looks like an ADR-0023 subject (type '${type}') but does not match ` +
    `domain.entity.type — exactly three lowercase dot-separated segments, entity in ` +
    `kebab-case, and no version segment. The server will reject it.`
  );
}
