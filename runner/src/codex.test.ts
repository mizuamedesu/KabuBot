import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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


test("valid stored credentials use normal refresh and verify upstream access", async () => {
  const fixture = await authFixture();
  try {
    const results = await Promise.all([fixture.service.authStatus(), fixture.service.authStatus()]);

    for (const result of results) {
      assert.equal(result.ok, true);
      assert.equal(result.status, "authenticated");
      assert.equal(result.validation, "remote");
      assert.equal(result.planType, "pro");
    }
    const requests = (await readFile(fixture.requestsPath, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    assert.equal(requests.filter(request => request.method === "initialize").length, 1);
    assert.deepEqual(requests.find(request => request.method === "account/read").params, { refreshToken: false });
    assert.equal(requests.filter(request => request.method === "account/rateLimits/read").length, 1);
  } finally {
    await fixture.cleanup();
  }
});


test("upstream rejection remains expired and a later successful check clears stale failure", async () => {
  const fixture = await authFixture();
  try {
    await fixture.service.authStatus();
    await writeFile(fixture.errorPath, "401 Unauthorized: credentials expired");
    const rejected = await fixture.service.authStatus();
    assert.equal(rejected.ok, false);
    assert.equal(rejected.status, "expired");
    assert.equal((rejected.authProcess as { status: string }).status, "failed");

    await rm(fixture.errorPath);
    const recovered = await fixture.service.authStatus();
    assert.equal(recovered.status, "authenticated");
    assert.equal((recovered.authProcess as { status: string }).status, "authenticated");
    assert.equal((recovered.authProcess as { error?: string }).error, undefined);
  } finally {
    await fixture.cleanup();
  }
});


test("valid local credentials do not hide an upstream outage", async () => {
  const fixture = await authFixture();
  try {
    await writeFile(fixture.errorPath, "connection timed out");
    const result = await fixture.service.authStatus();
    assert.equal(result.ok, false);
    assert.equal(result.status, "unavailable");
  } finally {
    await fixture.cleanup();
  }
});


async function authFixture() {
  const root = await mkdtemp(join(tmpdir(), "kabubot-auth-probe-"));
  const executable = join(root, "codex-mock.cjs");
  const requestsPath = join(root, "requests.jsonl");
  const errorPath = join(root, "upstream-error");
  await writeFile(executable, `#!/usr/bin/env node
const fs = require("node:fs");
const readline = require("node:readline");
if (process.argv[2] === "login") {
  console.log("Logged in using ChatGPT");
  process.exit(0);
}
readline.createInterface({ input: process.stdin }).on("line", line => {
  const message = JSON.parse(line);
  fs.appendFileSync(${JSON.stringify(requestsPath)}, line + "\\n");
  if (message.id === undefined) return;
  let response = { id: message.id, result: {} };
  if (message.method === "account/read") {
    response.result = {
      account: message.params.refreshToken ? null : { type: "chatgpt", planType: "pro" },
      requiresOpenaiAuth: true
    };
  }
  if (message.method === "account/rateLimits/read") {
    if (fs.existsSync(${JSON.stringify(errorPath)})) {
      response = { id: message.id, error: { code: -32603, message: fs.readFileSync(${JSON.stringify(errorPath)}, "utf8") } };
    } else {
      response.result = { rateLimits: { planType: "pro" } };
    }
  }
  console.log(JSON.stringify(response));
});
`);
  await chmod(executable, 0o700);
  return {
    service: new CodexService(root, root, root, executable),
    requestsPath,
    errorPath,
    cleanup: () => rm(root, { recursive: true, force: true })
  };
}
