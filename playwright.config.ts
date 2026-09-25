import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/ui",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 20000 },
  use: {
    baseURL: "http://127.0.0.1:8744",
    viewport: { width: 1440, height: 980 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "uv run python -m tests.ui_server",
    url: "http://127.0.0.1:8744/api/health",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
