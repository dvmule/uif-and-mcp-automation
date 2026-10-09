import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
    // Vitest owns the unit/component suite under src/ only. Without this,
    // Vitest's default glob also collects e2e/*.spec.ts — Playwright specs,
    // which use Playwright's own fixture-injecting `test` and fail to collect.
    // Playwright runs those separately via `npm run test:e2e`.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
