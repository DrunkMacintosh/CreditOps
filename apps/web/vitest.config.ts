import { configDefaults, defineConfig } from "vitest/config";

// Unit/component tests run under jsdom. The Playwright end-to-end specs under
// tests/e2e/ use @playwright/test's runner and must NOT be collected by vitest.
export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "tests/e2e/**"],
  },
});
