import { taskStreamUrl, validTask } from "./api";
import type { RepairTask, TaskTracePage } from "./types";

export type StreamState = "connecting" | "live" | "reconnecting" | "closed" | "fallback";
interface Callbacks {
  task: (task: RepairTask) => void;
  trace: (page: TaskTracePage) => void;
  state: (state: StreamState) => void;
  expired: () => void;
}

export function subscribeTask(id: string, callbacks: Callbacks, cursor = 0): () => void {
  if (typeof EventSource === "undefined") { callbacks.state("fallback"); return () => {}; }
  let source: EventSource;
  let stopped = false;
  const close = () => { stopped = true; source?.close(); };
  const fail = () => { close(); callbacks.state("fallback"); };
  try { source = new EventSource(taskStreamUrl(id, cursor), { withCredentials: true }); }
  catch { callbacks.state("fallback"); return close; }
  callbacks.state("connecting");
  source.onopen = () => { if (!stopped) callbacks.state("live"); };
  source.onerror = () => {
    if (stopped) return;
    if (source.readyState === 2) fail();
    else callbacks.state("reconnecting"); // EventSource resumes with Last-Event-ID.
  };
  source.addEventListener("task", event => {
    if (stopped) return;
    try {
      const task: unknown = JSON.parse((event as MessageEvent).data);
      if (!validTask(task) || task.state.task_id !== id) throw new Error("invalid task event");
      callbacks.task(task);
    } catch { fail(); }
  });
  source.addEventListener("trace", event => {
    if (stopped) return;
    try {
      const page = JSON.parse((event as MessageEvent).data) as TaskTracePage;
      if (!Number.isSafeInteger(page.next_cursor) || page.next_cursor < cursor || !Array.isArray(page.events)
        || !page.events.every(item => item !== null && typeof item === "object" && !Array.isArray(item))
        || typeof page.has_more !== "boolean" || !Number.isSafeInteger(page.skipped_invalid_records)
        || page.skipped_invalid_records < 0) throw new Error("invalid trace page");
      if (page.next_cursor > cursor) {
        callbacks.trace(page);
        cursor = page.next_cursor;
      }
    } catch { fail(); }
  });
  source.addEventListener("complete", event => {
    if (stopped) return;
    try {
      const payload = JSON.parse((event as MessageEvent).data);
      if (!validTask(payload.task) || payload.task.state.task_id !== id || payload.task.execution !== "idle") throw new Error("invalid completion");
      callbacks.task(payload.task);
      close(); callbacks.state("closed");
    } catch { fail(); }
  });
  source.addEventListener("stream-error", event => {
    if (stopped) return;
    let status = 0;
    try { status = JSON.parse((event as MessageEvent).data).status; } catch { /* fall back to GET */ }
    if (status === 401 || status === 403 || status === 404) { close(); callbacks.expired(); }
    else fail();
  });
  return close;
}
