import { defineConfig } from '@playwright/test';

// BASE_URL defaults to the local dev stack; point it at the deployed app for smoke checks.
export default defineConfig({
  testDir: './specs',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:8300',
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
