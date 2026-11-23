import { defineConfig, devices } from "@playwright/test";

const FRONTEND_URL = "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.js",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: FRONTEND_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "node e2e/start-backend.mjs",
      url: "http://127.0.0.1:8000/api/health/",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run dev",
      url: FRONTEND_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
