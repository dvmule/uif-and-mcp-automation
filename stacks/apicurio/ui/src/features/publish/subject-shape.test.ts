import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { SUBJECT_TYPES, SUBJECT_SHAPE, claimsSubjectGrammar, subjectWarningFor } from './subject-shape';

/**
 * The same corpus the BFF's pytest suite loads. Read from disk rather than
 * imported so the assertion is against the standard's own file, not a copy
 * Vite has transformed.
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const cases = JSON.parse(
  readFileSync(path.resolve(here, '../../../../standards/subject-naming.cases.json'), 'utf-8'),
) as {
  pattern: string;
  types: string[];
  valid: { subject: string }[];
  invalid: { subject: string; why: string; claimed: boolean }[];
};

describe('the shared corpus and this module agree', () => {
  it('has the same type vocabulary', () => {
    expect([...SUBJECT_TYPES]).toEqual(cases.types);
  });

  it('has the same pattern', () => {
    // The literal lives here because the standards folder is not bundled into
    // the app. This is the check that makes that safe.
    expect(SUBJECT_SHAPE.source).toBe(cases.pattern);
  });
});

describe('SUBJECT_SHAPE', () => {
  it.each(cases.valid.map((c) => c.subject))('accepts %s', (subject) => {
    expect(SUBJECT_SHAPE.test(subject)).toBe(true);
  });

  it.each(cases.invalid.map((c) => [c.subject || '<empty>', c.subject, c.why]))(
    'rejects %s',
    (_id, subject, why) => {
      expect(SUBJECT_SHAPE.test(subject), why as string).toBe(false);
    },
  );
});

describe('subjectWarningFor', () => {
  it('says nothing about a valid subject', () => {
    for (const { subject } of cases.valid) {
      expect(subjectWarningFor(subject), subject).toBeNull();
    }
  });

  it('says nothing about a subject outside this rule’s authority', () => {
    // The load-bearing half. The registry holds artifacts under other
    // standards; warning on those would train the reader to ignore the
    // warning. These are all rejected by the pattern, and still not ours.
    const unclaimed = cases.invalid.filter((c) => !c.claimed);
    expect(unclaimed.length).toBeGreaterThan(0);
    for (const c of unclaimed) {
      expect(subjectWarningFor(c.subject), `${c.subject}: ${c.why}`).toBeNull();
    }
  });

  it('warns about a malformed subject that claims a valid type', () => {
    const claimed = cases.invalid.filter((c) => c.claimed);
    expect(claimed.length).toBeGreaterThan(0);
    for (const c of claimed) {
      expect(subjectWarningFor(c.subject), `${c.subject}: ${c.why}`).not.toBeNull();
    }
  });

  it('names the offending type in the message', () => {
    expect(subjectWarningFor('product.marketing_copy.gold')).toContain("type 'gold'");
  });

  it('is silent on an empty subject, so an untouched field is not scolded', () => {
    expect(subjectWarningFor('')).toBeNull();
  });
});

describe('claimsSubjectGrammar mirrors the server rule scope', () => {
  it.each(cases.invalid.map((c) => [c.subject || '<empty>', c.subject, c.claimed]))(
    'agrees with the corpus for %s',
    (_id, subject, claimed) => {
      expect(claimsSubjectGrammar(subject as string)).toBe(claimed);
    },
  );
});
