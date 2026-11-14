// Removes the run's shared state directory (the fake CLI log and the data dir name).
import { rmSync } from "node:fs";

export default async function teardown() {
  const state = process.env.HARNESS_TEST_STATE;
  if (state) rmSync(state, { recursive: true, force: true });
}
