// Pure domain logic: types, validation, and business rules.
// No network, no framework — this is what the unit tests target directly.

export type DeveloperStatus = 'active' | 'suspended';

export interface Developer {
  id: string;
  email: string;
  displayName: string;
  status: DeveloperStatus;
  createdAt: string; // RFC 3339 UTC
}

export interface RegisterDeveloperRequest {
  email: string;
  displayName: string;
}

export interface ValidationError {
  field: string;
  message: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Validates the registration payload against the contract's constraints.
// Returns the list of violations; an empty list means valid.
export function validateRegisterDeveloper(input: unknown): ValidationError[] {
  const errors: ValidationError[] = [];
  if (typeof input !== 'object' || input === null) {
    return [{ field: 'body', message: 'request body must be a JSON object' }];
  }
  const body = input as Record<string, unknown>;

  const email = body.email;
  if (typeof email !== 'string' || !EMAIL_RE.test(email)) {
    errors.push({ field: 'email', message: 'email must be a valid email address' });
  } else if (email.length > 254) {
    errors.push({ field: 'email', message: 'email must be at most 254 characters' });
  }

  const displayName = body.displayName;
  if (typeof displayName !== 'string' || displayName.length < 1) {
    errors.push({ field: 'displayName', message: 'displayName is required' });
  } else if (displayName.length > 120) {
    errors.push({ field: 'displayName', message: 'displayName must be at most 120 characters' });
  }

  return errors;
}

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}
