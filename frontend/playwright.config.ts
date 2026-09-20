import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1280, height: 720 },
    video: process.env.DEMO_CAPTURE ? "on" : "off",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    port: 5173,
    reuseExistingServer: true,
  },
});
