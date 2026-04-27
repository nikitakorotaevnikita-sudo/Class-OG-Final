// playwright.config.js — Playwright E2E configuration for Class OG Final
// Run: npx playwright test
// Requires: npm install -D @playwright/test playwright

const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30 * 1000,
  use: {
    baseURL: 'http://localhost:8000',
    headless: true,
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: '',
    port: 8000,
    reuseExistingServer: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
});