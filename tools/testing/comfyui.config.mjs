// Smoke run against a real ComfyUI (make comfyui-test). Not in CI.
//
// Starts a second, throwaway ComfyUI on 127.0.0.1:8774 from COMFYUI_DIR
// (default ~/comfy/ComfyUI) with every custom node disabled except this one (as comfyui-harness-under-test),
// linked in from a scratch directory that also holds its user, temp, input and
// output directories. A fake `claude` is first on its PATH, so the real CLI is
// never started.
import { defineConfig, devices } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const comfy = process.env.COMFYUI_DIR ?? join(homedir(), "comfy/ComfyUI");
const port = 8774;
// Not "comfyui-harness": the author's ComfyUI has its own custom_nodes/comfyui-harness
// (the main checkout), and a whitelist of that name would load it as well.
const NODE_NAME = "comfyui-harness-under-test";
const quote = (text) => `'${text.replaceAll("'", "'\\''")}'`;

function makeScratch() {
  const scratch = mkdtempSync(join(tmpdir(), "harness-comfyui-"));
  for (const dir of ["nodes", "user/default", "temp", "input", "output", "bin"]) {
    mkdirSync(join(scratch, dir), { recursive: true });
  }
  symlinkSync(repo, join(scratch, "nodes", NODE_NAME));
  writeFileSync(join(scratch, "extra.yaml"), `harness_check:\n  base_path: ${scratch}\n  custom_nodes: nodes\n`);
  // Skip the first-run template browser so the canvas takes the first key.
  writeFileSync(join(scratch, "user/default/comfy.settings.json"), JSON.stringify({ "Comfy.TutorialCompleted": true }));
  const shim = join(scratch, "bin/claude");
  const python = join(repo, ".venv/bin/python");
  writeFileSync(shim, `#!/bin/sh\nexec ${quote(python)} ${quote(join(repo, "tests/browser/fake_cli.py"))} "$@"\n`);
  chmodSync(shim, 0o700);
  return scratch;
}

if (!process.env.HARNESS_COMFY_SCRATCH) {
  const created = makeScratch();
  process.env.HARNESS_COMFY_SCRATCH = created;
  // The runner exits after the server has stopped; workers inherit the variable
  // and never get here.
  process.on("exit", () => rmSync(created, { recursive: true, force: true }));
  const bin = join(process.env.HARNESS_COMFY_SCRATCH, "bin");
  process.env.HARNESS_COMFY_PATH = `${bin}:${process.env.PATH}`;
  // Never let the real claude start: the shim must win the lookup.
  const found = execFileSync("sh", ["-c", "command -v claude"], {
    env: { ...process.env, PATH: process.env.HARNESS_COMFY_PATH },
  }).toString().trim();
  if (realpathSync(found) !== realpathSync(join(bin, "claude"))) {
    throw new Error(`claude resolves to ${found}, not the fake CLI`);
  }
}
const scratch = process.env.HARNESS_COMFY_SCRATCH;
process.env.HARNESS_TEST_REPO = repo;
process.env.HARNESS_COMFY_PORT = String(port);
process.env.HARNESS_NODE_NAME = NODE_NAME;
process.env.FAKE_CLI_LOG = join(scratch, "fake_cli.log");

const args = [
  "main.py", "--listen", "127.0.0.1", "--port", String(port), "--cpu",
  "--disable-all-custom-nodes", "--whitelist-custom-nodes", NODE_NAME,
  "--extra-model-paths-config", join(scratch, "extra.yaml"),
  "--user-directory", join(scratch, "user"), "--temp-directory", join(scratch, "temp"),
  "--input-directory", join(scratch, "input"), "--output-directory", join(scratch, "output"),
  "--disable-auto-launch",
];

export default defineConfig({
  testDir: join(repo, "tests/comfyui"),
  testMatch: "*.spec.mjs",
  outputDir: join(repo, "test-results/comfyui"),
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 900 } } }],
  webServer: {
    command: `cd ${quote(comfy)} && exec venv/bin/python ${args.map(quote).join(" ")}`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
    gracefulShutdown: { signal: "SIGINT", timeout: 15_000 },
    env: { PATH: process.env.HARNESS_COMFY_PATH, FAKE_CLI_LOG: process.env.FAKE_CLI_LOG },
  },
});
