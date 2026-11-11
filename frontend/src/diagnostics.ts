/** Only fixed error codes and identifiers leave the browser; never source, reports or exception text. */
export type ClientErrorCode = "component_error" | "browser_error" | "unhandled_rejection" | "resource_error"
  | "storage_error" | "request_timeout" | "connection_failed" | "response_invalid";
type ClientFault = { code: ClientErrorCode; error_id: string; task_id?: string };
const codes = new Set<ClientErrorCode>(["component_error", "browser_error", "unhandled_rejection", "resource_error",
  "storage_error", "request_timeout", "connection_failed", "response_invalid"]);
const queueKey = "repair-client-errors";
let memory: ClientFault[] = [];
let flushing = false;
const recent = new Map<ClientErrorCode, { id: string; time: number }>();

function queue(): ClientFault[] {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(queueKey) || "[]");
    if (!Array.isArray(saved)) return memory;
    const valid = saved.filter((item): item is ClientFault => !!item && typeof item === "object"
      && codes.has(item.code) && typeof item.error_id === "string" && /^[a-z0-9-]{8,64}$/.test(item.error_id)
      && (item.task_id === undefined || /^[a-f0-9]{32}$/.test(item.task_id)));
    return [...new Map([...valid, ...memory].map(item => [item.error_id, item])).values()].slice(-20);
  } catch { return memory; }
}
function save(items: ClientFault[]) {
  memory = items.slice(-20);
  try { sessionStorage.setItem(queueKey, JSON.stringify(memory)); }
  catch { console.warn("浏览器诊断暂存不可用；当前页面仍保留有限诊断记录。"); }
}

export async function flushClientErrors() {
  if (flushing) return;
  flushing = true;
  try {
    for (const item of queue()) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch("/api/client-errors", { method: "POST", signal: controller.signal,
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(item) });
        const receipt = await response.json().catch(() => null);
        if (!response.ok || receipt?.recorded !== true || receipt?.error_id !== item.error_id) break;
        save(queue().filter(candidate => candidate.error_id !== item.error_id));
      } catch { break; }
      finally { clearTimeout(timer); }
    }
  } finally { flushing = false; }
}

export function captureClientError(code: ClientErrorCode): string {
  const previous = recent.get(code);
  if (previous && Date.now() - previous.time < 30_000) return previous.id;
  const error_id = crypto.randomUUID();
  recent.set(code, { id: error_id, time: Date.now() });
  const task = new URL(window.location.href).searchParams.get("task");
  const item = { code, error_id, ...(task && /^[a-f0-9]{32}$/.test(task) ? { task_id: task } : {}) };
  save([...queue(), item]);
  window.dispatchEvent(new CustomEvent("repair:client-error", { detail: item }));
  void flushClientErrors();
  return error_id;
}

export function installClientDiagnostics() {
  window.addEventListener("error", event => captureClientError(event.target === window ? "browser_error" : "resource_error"), true);
  window.addEventListener("unhandledrejection", () => captureClientError("unhandled_rejection"));
  window.addEventListener("online", () => void flushClientErrors());
  void flushClientErrors();
}
