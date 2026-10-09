import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'list',
  use: {
    // Overridable so this suite can point at a deployed environment instead of
    // a dev server. It is NOT wired into the deploy workflow's smoke job, and
    // that is a limitation rather than an oversight: the deployed UI sits
    // behind an API Gateway authorizer whose CI credential is a request
    // header, and a browser cannot attach one to its own XHRs. A
    // runner-driven browser is therefore denied before it renders any data.
    // Unblocked by either an allowlisted egress address or the real
    // user-facing authentication this stack still lacks.
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
