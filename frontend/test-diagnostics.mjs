import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import ts from "typescript";
import { webcrypto } from "node:crypto";

const source = ts.transpileModule(await readFile(new URL("./src/diagnostics.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
function setup({ storageFails = false, networkFails = false, recorded = true } = {}) {
  const saved = new Map(), listeners = new Map(), sent = [], exports = {};
  const window = { location: { href: `http://127.0.0.1:8001/?task=${"a".repeat(32)}` },
    addEventListener: (name, callback) => listeners.set(name, callback), dispatchEvent: () => {} };
  const state = { networkFails };
  vm.runInNewContext(source, { exports, window, crypto: webcrypto, URL, AbortController, setTimeout, clearTimeout,
    CustomEvent: class { constructor(name, options) { this.type = name; this.detail = options.detail; } },
    console: { warn() {} }, sessionStorage: {
      getItem: key => { if (storageFails) throw new Error("blocked"); return saved.get(key) || null; },
      setItem: (key, value) => { if (storageFails) throw new Error("blocked"); saved.set(key, value); },
    }, fetch: async (_url, options) => {
      const body = JSON.parse(options.body); sent.push(body);
      if (state.networkFails) throw new Error("offline");
      return { ok: true, json: async () => ({ recorded, error_id: body.error_id }) };
    } });
  return { exports, saved, sent, listeners, state, window };
}

test("browser faults queue offline, deduplicate, then flush without source text", async () => {
  const instance = setup({ networkFails: true });
  const id = instance.exports.captureClientError("component_error");
  assert.equal(instance.exports.captureClientError("component_error"), id);
  await tick();
  assert.equal(JSON.parse(instance.saved.get("repair-client-errors")).length, 1);
  instance.state.networkFails = false;
  await instance.exports.flushClientErrors();
  assert.equal(JSON.parse(instance.saved.get("repair-client-errors")).length, 0);
  assert.deepEqual(Object.keys(instance.sent[0]).sort(), ["code", "error_id", "task_id"]);
});

test("storage failure keeps diagnostic in memory and cannot cause recursive rejection", async () => {
  const instance = setup({ storageFails: true, networkFails: true });
  instance.exports.captureClientError("storage_error");
  await tick();
  instance.state.networkFails = false;
  await instance.exports.flushClientErrors();
  assert.equal(instance.sent.length, 2);
});

test("diagnostic is retained when the backend could not persist it", async () => {
  const instance = setup({ recorded: false });
  instance.exports.captureClientError("browser_error");
  await tick();
  assert.equal(JSON.parse(instance.saved.get("repair-client-errors")).length, 1);
});

test("global error and rejection hooks emit only fixed codes", async () => {
  const instance = setup();
  instance.exports.installClientDiagnostics();
  instance.listeners.get("error")({ target: instance.window, message: "SECRET source" });
  instance.listeners.get("unhandledrejection")({ reason: "SECRET response" });
  await tick();
  await instance.exports.flushClientErrors();
  assert(instance.sent.some(item => item.code === "browser_error"));
  assert(instance.sent.some(item => item.code === "unhandled_rejection"));
  assert(!JSON.stringify(instance.sent).includes("SECRET"));
});
