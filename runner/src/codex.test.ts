import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CodexService, classifyAuthProbeError } from "./codex.js";


test("auth errors are classified as expired", () => {
  const result = classifyAuthProbeError("401 Unauthorized: refresh token expired", "");

  assert.equal(result.status, "expired");
  assert.match(result.error || "", /refresh token expired/);
});


test("transport errors are not reported as authenticated", () => {
  const result = classifyAuthProbeError("connection timed out", "");

  assert.equal(result.status, "unavailable");
});


test("refresh transport failures do not delete otherwise valid credentials", () => {
  const result = classifyAuthProbeError("network timed out while requesting a refresh token", "");

  assert.equal(result.status, "unavailable");
});


test("missing credentials are unauthenticated", async () => {
  const codexHome = await mkdtemp(join(tmpdir(), "kabubot-no-auth-"));
  try {
    const service = new CodexService(codexHome, process.cwd(), process.cwd());
    const result = await service.authStatus();

    assert.equal(result.ok, false);
    assert.equal(result.status, "unauthenticated");
    assert.equal(result.validation, "local");
  } finally {
    await rm(codexHome, { recursive: true, force: true });
  }
});
