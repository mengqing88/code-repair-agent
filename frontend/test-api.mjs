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

test("folder and ZIP payloads include selected bytes and never reuse stale single-file contents", async () => {
  const client = api(() => {});
  const files = [{ path: "src/a.py", content_base64: "eD0x" }, { path: "tests/test_a.py", content_base64: "eD0y" }];
  const folder = { ...draft, sourceType: "directory", path: "project", projectFiles: files, fileContent: "stale" };
  const payload = client.taskPayload(folder);
  assert.equal(payload.project_files, files);
  assert.equal(payload.file_content, null);
  assert.equal(payload.revision, "");
  assert.notEqual(await client.taskFingerprint(folder), await client.taskFingerprint({ ...folder, projectFiles: files.slice(1) }));
  const zip = client.taskPayload({ ...folder, sourceType: "zip_archive", fileName: "project.zip", archiveBase64: "UEs=" });
  assert.equal(zip.archive_base64, "UEs=");
  assert.equal(zip.file_name, "project.zip");
  assert.equal(zip.project_files, undefined);
  assert.equal(client.taskPayload({ ...folder, sourceType: "code_file" }).project_files, undefined);
});

const uploadSource = ts.transpileModule(await readFile(new URL("./src/project-upload.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function uploads() {
  const exports = {};
  vm.runInNewContext(uploadSource, { exports, Uint8Array, btoa });
  return exports;
}
function selectedFile(path, text, size = Buffer.byteLength(text)) {
  return { webkitRelativePath: path, size, arrayBuffer: async () => Uint8Array.from(Buffer.from(text)).buffer };
}

test("folder selection retains relative paths and binary bytes, excluding credentials before reading", async () => {
  const secret = selectedFile("demo/.env", "do not read");
  secret.arrayBuffer = async () => { throw new Error("credential read"); };
  const result = await uploads().readProjectFolder([selectedFile("demo/src/a.py", "x=1"), secret,
    selectedFile("demo/node_modules/cache.js", "ignored"), selectedFile("demo/tests/test_a.py", "assert True")]);
  assert.equal(result.name, "demo");
  assert.equal(result.skipped, 2);
  assert.equal(result.files.length, 2);
  assert.equal(result.files[0].path, "src/a.py");
  assert.equal(Buffer.from(result.files[0].content_base64, "base64").toString(), "x=1");
});

test("folder selection rejects missing relative paths, oversized files and too many files", async () => {
  const client = uploads();
  await assert.rejects(client.readProjectFolder([selectedFile("", "x")]), /ZIP/);
  await assert.rejects(client.readProjectFolder([selectedFile("demo/a.py", "x", 2_000_001)]), /2 MB/);
  await assert.rejects(client.readProjectFolder(Array.from({ length: 1001 }, (_, i) => selectedFile(`demo/${i}.py`, "x"))), /1000/);
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

const streamSource = ts.transpileModule(await readFile(new URL("./src/task-stream.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function streamClient() {
  const sources = [], tasks = [], traces = [], states = [];
  let expired = 0;
  const client = api(() => {});
  client.setApiSession({ user: { id: "b".repeat(32) }, csrf_token: "csrf" });
  class FakeEventSource {
    readyState = 0;
    closed = false;
    handlers = {};
    constructor(url, options) { this.url = url; this.options = options; sources.push(this); }
    addEventListener(name, handler) { this.handlers[name] = handler; }
    close() { this.closed = true; }
    emit(name, payload) { this.handlers[name]?.({ data: JSON.stringify(payload) }); }
  }
  const exports = {};
  vm.runInNewContext(streamSource, { exports, EventSource: FakeEventSource, require: () => client });
  const stop = exports.subscribeTask("a".repeat(32), {
    task: value => tasks.push(value), trace: value => traces.push(value),
    state: value => states.push(value), expired: () => { expired++; },
  });
  return { source: sources[0], stop, tasks, traces, states, expired: () => expired };
}
function taskEvent(execution = "running") {
  return { execution, source_label: "source", config: { test_targets: ["."] },
    state: { task_id: "a".repeat(32), status: "repairing", elapsed_seconds: 1, attempt_count: 0,
      remaining_budget: { seconds: 120, tokens: 20000, attempts: 10, cost: 0.1 }, code_evidence: [], case_matches: [] } };
}

test("SSE uses the signed-in account, handles replay once and closes on completion", () => {
  const stream = streamClient();
  assert.equal(stream.source.options.withCredentials, true);
  assert.match(stream.source.url, /account_id=bbbb/);
  stream.source.onopen();
  stream.source.emit("task", taskEvent());
  const page = { events: [{ operation: "read_code", event: "succeeded" }], next_cursor: 100, has_more: false, skipped_invalid_records: 0 };
  stream.source.emit("trace", page);
  stream.source.emit("trace", page);
  assert.equal(stream.traces.length, 1);
  stream.source.emit("complete", { task: taskEvent("idle"), next_cursor: 100 });
  assert.equal(stream.tasks.length, 2);
  assert.equal(stream.tasks.at(-1).execution, "idle");
  assert.equal(stream.source.closed, true);
  assert.equal(stream.states.at(-1), "closed");
  stream.source.onerror();
  assert.equal(stream.states.at(-1), "closed");
});

test("SSE reconnecting allows polling and malformed payload falls back safely", () => {
  const stream = streamClient();
  stream.source.onerror();
  assert.equal(stream.states.at(-1), "reconnecting");
  assert.equal(stream.source.closed, false);
  stream.source.onopen();
  assert.equal(stream.states.at(-1), "live");
  stream.source.emit("task", { unexpected: "invalid" });
  assert.equal(stream.tasks.length, 0);
  assert.equal(stream.states.at(-1), "fallback");
  assert.equal(stream.source.closed, true);
});

test("SSE logout and explicit cancellation discard late events", () => {
  const expired = streamClient();
  expired.source.emit("stream-error", { status: 401, detail: "expired" });
  assert.equal(expired.expired(), 1);
  assert.equal(expired.source.closed, true);
  expired.source.emit("task", taskEvent());
  assert.equal(expired.tasks.length, 0);
  const switched = streamClient();
  switched.stop();
  switched.source.emit("task", taskEvent());
  assert.equal(switched.tasks.length, 0);
  assert.equal(switched.source.closed, true);
});

test("knowledge review sends the frozen candidate hash, reason and account authorization", async () => {
  const calls = [];
  const candidate = { id: "c".repeat(32), content_hash: "d".repeat(64), status: "published", record: {}, audit: [] };
  const client = api(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => url.includes("?status=") ? { candidates: [candidate] } : candidate };
  });
  client.setApiSession({ user: { id: "b".repeat(32) }, csrf_token: "review-csrf" });
  assert.equal((await client.getKnowledgeCandidates()).candidates.length, 1);
  await client.reviewKnowledgeCandidate(candidate, "approve", "checked independent evidence");
  assert.equal(calls[0].url, "/api/knowledge/candidates?status=needs_human");
  assert.equal(calls[1].options.headers["X-CSRF-Token"], "review-csrf");
  assert.equal(calls[1].options.headers["X-Account-ID"], "b".repeat(32));
  assert.deepEqual(JSON.parse(calls[1].options.body), { decision: "approve", reason: "checked independent evidence", content_hash: candidate.content_hash });
});
