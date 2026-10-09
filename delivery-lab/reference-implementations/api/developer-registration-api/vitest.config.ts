import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      exclude: ['src/server.ts'],
      // A realistic floor to ratchet up — not a fake 100%.
      thresholds: { lines: 80, functions: 80, branches: 75, statements: 80 },
    },
  },
});
