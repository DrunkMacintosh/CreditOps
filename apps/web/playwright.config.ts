import { defineConfig, devices } from "@playwright/test";

// Critical-path E2E for the synthetic-fixture prototype. The app needs no live
// upstream here: the tests activate a Scenario (SYNTHETIC TEST FIXTURE), which
// installs the client-side interceptor, so the whole flow runs offline.
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: process.env.E2E_BASE_URL ? "true" : "pnpm dev",
    url: `${process.env.E2E_BASE_URL ?? "http://localhost:3000"}/ho-so`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
