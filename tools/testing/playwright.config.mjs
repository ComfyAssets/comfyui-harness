import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const port = Number(process.env.HARNESS_TEST_PORT ?? 8770);

// Created once by the runner; workers inherit it through the environment.
process.env.HARNESS_TEST_STATE ??= mkdtempSync(join(tmpdir(), "harness-browser-state-"));
process.env.HARNESS_TEST_REPO = repo;
process.env.HARNESS_TEST_PORT = String(port);

export default defineConfig({
  testDir: join(repo, "tests/browser"),
  testMatch: "*.spec.mjs",
  outputDir: join(repo, "test-results/browser"),
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  globalTeardown: join(repo, "tests/browser/teardown.mjs"),
  webServer: {
    command: `${join(repo, ".venv/bin/python")} ${join(repo, "tests/browser/server.py")}`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: "pipe",
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    env: {
      HARNESS_TEST_PORT: String(port),
      HARNESS_TEST_STATE: process.env.HARNESS_TEST_STATE,
      FAKE_CLI_LOG: join(process.env.HARNESS_TEST_STATE, "fake_cli.log"),
    },
  },
});
