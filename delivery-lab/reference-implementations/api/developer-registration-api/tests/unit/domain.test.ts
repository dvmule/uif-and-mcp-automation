// Unit tests: pure domain logic, no network. Fast, run on every commit.
import { describe, expect, it } from 'vitest';
import { normalizeEmail, validateRegisterDeveloper } from '../../src/domain.js';

describe('validateRegisterDeveloper', () => {
  it('accepts a valid payload', () => {
    expect(validateRegisterDeveloper({ email: 'ada@example.com', displayName: 'Ada' })).toEqual([]);
  });

  it('rejects a missing/invalid email', () => {
    const errors = validateRegisterDeveloper({ email: 'nope', displayName: 'Ada' });
    expect(errors.map((e) => e.field)).toContain('email');
  });

  it('rejects an empty displayName', () => {
    const errors = validateRegisterDeveloper({ email: 'ada@example.com', displayName: '' });
    expect(errors.map((e) => e.field)).toContain('displayName');
  });

  it('rejects a non-object body', () => {
    expect(validateRegisterDeveloper('x')).toHaveLength(1);
  });

  it('rejects an over-long displayName', () => {
    const errors = validateRegisterDeveloper({ email: 'ada@example.com', displayName: 'a'.repeat(121) });
    expect(errors.map((e) => e.field)).toContain('displayName');
  });
});

describe('normalizeEmail', () => {
  it('trims and lowercases', () => {
    expect(normalizeEmail('  Ada@Example.COM ')).toBe('ada@example.com');
  });
});
