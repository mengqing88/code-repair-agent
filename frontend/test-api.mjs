import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import ts from "typescript";
import { webcrypto } from "node:crypto";

const source = ts.transpileModule(await readFile(new URL("./src/api.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function api(fetch, timers = {}) {
  const exports = {};
  vm.runInNewContext(source, { exports, fetch, crypto: webcrypto, TextEncoder, AbortController,
    require: () => ({ captureClientError: () => "client-test-error" }),
    setTimeout, clearTimeout, ...timers });
  return exports;
}
const draft = { sourceType: "pasted_code", path: "", revision: "", codeText: "print(1)", fileName: "",
  fileContent: null, errorReport: "report", testTarget: "." };

test("creation sends original key and stable parameter fingerprint", async () => {
  const requests = [];
  const client = api(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ execution: "queued", source_label: "source", config: { test_targets: ["."] },
      state: { task_id: "a".repeat(32), status: "queued", elapsed_seconds: 0, attempt_count: 0,
        remaining_budget: { seconds: 120, tokens: 20000, attempts: 10, cost: 0.1 }, code_evidence: [], case_matches: [] } }) };
  });
  await client.createTask(draft, "original-key");
  await client.getRequest("original-key");
  assert.equal(requests[0].options.headers["Idempotency-Key"], "original-key");
  assert.equal(requests[1].url, "/api/requests/original-key");
  assert.equal(await client.taskFingerprint(draft), await client.taskFingerprint({ ...draft }));
  assert.notEqual(await client.taskFingerprint(draft), await client.taskFingerprint({ ...draft, errorReport: "changed" }));
});

test("request timeout aborts fetch and reports uncertain outcome", async () => {
  let abort, cleared = false;
  const client = api(async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")));
    abort();
  }), { setTimeout: (callback, ms) => { assert.equal(ms, 15000); abort = callback; return 1; }, clearTimeout: () => { cleared = true; } });
  await assert.rejects(client.createTask(draft, "key"), error => error.uncertain && error.message.includes("超时"));
  assert.equal(cleared, true);
});

test("validation errors preserve server action and cannot be retried unchanged", async () => {
  const client = api(async () => ({ ok: false, status: 422, json: async () => ({ error: {
    code: "invalid_arguments", message: "修正参数", next_action: "补充字段", retryable: false, outcome_unknown: false,
  } }) }));
  await assert.rejects(client.createTask(draft, "key"), error => !error.retryable && !error.uncertain && error.detail.next_action === "补充字段");
});

test("connection loss and invalid success body remain uncertain", async () => {
  for (const fetch of [async () => { throw new Error("offline"); }, async () => ({ ok: true, json: async () => { throw new Error("invalid JSON"); } }),
    async () => ({ ok: true, json: async () => ({ unexpected: "object" }) })]) {
    await assert.rejects(api(fetch).createTask(draft, "key"), error => error.uncertain);
  }
});

test("unknown outcomes preserve uncertainty and error references even for HTTP 409", async () => {
  const client = api(async () => ({ ok: false, status: 409, json: async () => ({ error: {
    code: "outcome_unknown", message: "核对结果", error_id: "error-reference", outcome_unknown: true,
  } }) }));
  await assert.rejects(client.createTask(draft, "key"), error => error.uncertain && error.message.includes("error-reference"));
});
