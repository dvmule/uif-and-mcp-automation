import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import { defineConfig, globalIgnores } from 'eslint/config';

/**
 * Mirrors `stacks/echo/ui/eslint.config.js` — the same four extends, in the
 * same order — rather than inventing a rule set for this stack. `npm run lint`
 * has been in `package.json` since the app was scaffolded with no eslint
 * installed and no config on disk, so it failed on invocation and nothing
 * lint-checked 20k lines of UI.
 *
 * Two deliberate departures from echo, both recorded here so a future reader
 * does not "fix" them back:
 *
 * 1. Nothing from `prettier`. The root CLAUDE.md names eslint AND prettier as
 *    the TypeScript checks CI enforces, but no UI in this repo has a prettier
 *    config, and running prettier here without one reformatted 357 lines to a
 *    different quote style. Formatting is not enforced until someone commits
 *    a shared config.
 * 2. Test and config files get their own blocks. Echo has no Playwright suite
 *    and folds its tests into one project; this app's specs use Node globals
 *    and Playwright's own `test`, and `vite.config.ts` runs in Node.
 */
export default defineConfig([
  globalIgnores([
    'dist',
    'coverage',
    // Playwright's own output, plus the MCP browser's scratch directory.
    'playwright-report',
    'test-results',
    '.playwright-mcp',
  ]),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // `globals: true` in the Vitest config, so describe/it/expect/vi are not
    // imported in every file.
    files: ['src/**/*.{test,spec}.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      // A test may legitimately hand a component a malformed payload to prove
      // it survives one, which is exactly what a type would forbid.
      '@typescript-eslint/no-explicit-any': 'off',
      // Exporting a helper from a spec file is not a React Fast Refresh
      // hazard; these never mount in the dev server.
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['e2e/**/*.ts', '*.config.ts', 'tailwind.config.ts', 'postcss.config.js'],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
]);
