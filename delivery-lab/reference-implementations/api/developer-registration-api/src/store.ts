// In-memory repository. A real domain swaps this for a database behind the same
// interface; the handlers and tests do not change.
import { ulid } from 'ulid';
import type { Developer } from './domain.js';
import { normalizeEmail } from './domain.js';

export interface CreateDeveloperInput {
  email: string;
  displayName: string;
}

export class EmailTakenError extends Error {}

export class DeveloperStore {
  private byId = new Map<string, Developer>();
  private byEmail = new Map<string, string>(); // normalized email -> id

  create(input: CreateDeveloperInput): Developer {
    const email = normalizeEmail(input.email);
    if (this.byEmail.has(email)) {
      throw new EmailTakenError(email);
    }
    const developer: Developer = {
      id: `dev_${ulid()}`,
      email,
      displayName: input.displayName,
      status: 'active',
      createdAt: new Date().toISOString(),
    };
    this.byId.set(developer.id, developer);
    this.byEmail.set(email, developer.id);
    return developer;
  }

  get(id: string): Developer | undefined {
    return this.byId.get(id);
  }

  // Newest first, cursor is the id to start after.
  list(pageSize: number, cursor?: string): { items: Developer[]; nextCursor: string | null } {
    const all = [...this.byId.values()].sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
    let start = 0;
    if (cursor) {
      const idx = all.findIndex((d) => d.id === cursor);
      start = idx >= 0 ? idx + 1 : 0;
    }
    const items = all.slice(start, start + pageSize);
    const next = all[start + pageSize];
    return { items, nextCursor: next ? items[items.length - 1]!.id : null };
  }
}
