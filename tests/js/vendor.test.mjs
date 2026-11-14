import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("vendored ghostty-web has no bare imports and carries its WASM", () => {
  const src = readFileSync(new URL("../../web/vendor/ghostty-web/ghostty-web.mjs", import.meta.url), "utf8");
  assert.equal(/^import\s.+from\s+["'][^.\/]/m.test(src), false);
  assert.ok(src.includes("data:application/wasm"));
});
