import type { RepairTask, ReviewRecord, TaskDraft, StructuredError } from "./types";
import { captureClientError } from "./diagnostics";

export class ApiError extends Error {
  constructor(message: string, public status = 0, public detail?: StructuredError) {
    super(message + (detail?.error_id ? `（错误编号 ${detail.error_id}）` : ""));
  }
  get uncertain() { return this.detail?.outcome_unknown === true || this.status === 0 || this.status >= 500; }
  get retryable() { return this.uncertain || this.status === 429; }
}

async function request<T>(url: string, body?: unknown, key?: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(url, { signal: controller.signal, ...(body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
      body: JSON.stringify(body),
    }) });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload?.detail;
      throw new ApiError(payload?.error?.message || (typeof detail === "string" ? detail : Array.isArray(detail)
        ? detail.map((item: { msg: string }) => item.msg).join("；") : `请求失败（${response.status}）`), response.status, payload?.error);
    }
    if (!validTask(payload)) {
      const id = captureClientError("response_invalid");
      throw new ApiError(`服务端响应无法解析，请查询原操作状态（错误编号 ${id}）。`);
    }
    return payload as T;
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    const id = captureClientError(controller.signal.aborted ? "request_timeout" : "connection_failed");
    throw new ApiError((controller.signal.aborted ? "请求等待超时，请先查询原操作状态。" : "无法连接本地修复服务，请确认 Python 服务已启动。") + `（错误编号 ${id}）`);
  } finally { clearTimeout(timer); }
}

function validTask(value: unknown): value is RepairTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<RepairTask>;
  const state = task.state;
  return !!state && typeof state.task_id === "string" && /^[a-f0-9]{32}$/.test(state.task_id)
    && ["queued", "running", "idle"].includes(task.execution || "") && typeof state.status === "string"
    && Number.isFinite(state.elapsed_seconds) && Number.isFinite(state.attempt_count)
    && !!state.remaining_budget && ["seconds", "tokens", "attempts", "cost"].every(key =>
      Number.isFinite(state.remaining_budget[key as keyof typeof state.remaining_budget]))
    && Array.isArray(state.code_evidence) && Array.isArray(state.case_matches)
    && !!task.config && Array.isArray(task.config.test_targets) && typeof task.source_label === "string";
}

export function taskPayload(draft: TaskDraft) {
  return {
    source_type: draft.sourceType, path: draft.path, revision: draft.sourceType === "git_repository" ? draft.revision : "",
    code_text: draft.codeText, file_name: draft.sourceType === "python_file" ? draft.fileName : "",
    file_content: draft.sourceType === "python_file" ? draft.fileContent : null,
    error_report: draft.errorReport, test_target: draft.testTarget,
  };
}

export async function taskFingerprint(draft: TaskDraft) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(taskPayload(draft))));
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
}

export const createTask = (draft: TaskDraft, key: string) => request<RepairTask>("/api/tasks", taskPayload(draft), key);
export const getRequest = (key: string) => request<RepairTask>(`/api/requests/${encodeURIComponent(key)}`);

export const getTask = (id: string) => request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}`);
export const reviewTask = (id: string, decision: ReviewRecord["decision"], reason: string, patchHash: string) =>
  request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}/review`, { decision, reason, patch_hash: patchHash });
