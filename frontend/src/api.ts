import type { RepairTask, ReviewRecord, TaskDraft, StructuredError } from "./types";
import { captureClientError } from "./diagnostics";
export interface User { id: string; username: string }
export interface Session { user: User; csrf_token: string }
export interface PersonalMemory { id: string; kind: "preference" | "coding_rule"; content: string; updated_at: number; source_conversation?: string | null; extracted_at?: number | null }
export interface ChatMessage { id: string; role: "user" | "assistant"; content: string; request_id: string; status: string }
export interface MemoryCandidate { id: string; kind: PersonalMemory["kind"]; content: string; extracted_at: number }
export interface Conversation { id: string; title: string; summary: string; messages: ChatMessage[]; candidates: MemoryCandidate[] }
export interface KnowledgeCandidate {
  id: string; content_hash: string; status: string; source: string; record: Record<string, unknown>; error: string | null;
  audit: Array<{ event: string; actor: string; details: Record<string, unknown> }>;
}
let csrfToken = "";
let accountId = "";
export function setApiSession(session: Session | null) {
  csrfToken = session?.csrf_token || "";
  accountId = session?.user.id || "";
}

export class ApiError extends Error {
  constructor(message: string, public status = 0, public detail?: StructuredError) {
    super(message + (detail?.error_id ? `（错误编号 ${detail.error_id}）` : ""));
  }
  get uncertain() { return this.detail?.outcome_unknown === true || this.status === 0 || this.status >= 500; }
  get retryable() { return this.uncertain || this.status === 429; }
}

async function request<T>(url: string, body?: unknown, key?: string, valid: (value: any) => boolean = validTask, method?: string, timeout = 15_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { signal: controller.signal, credentials: "same-origin",
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(csrfToken ? { "X-CSRF-Token": csrfToken, "X-Account-ID": accountId } : {}),
        ...(key ? { "Idempotency-Key": key } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      if ((response.status === 401 || payload?.error?.code === "account_changed") && typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("repair:session-expired"));
      }
      const detail = payload?.detail;
      throw new ApiError(payload?.error?.message || (typeof detail === "string" ? detail : Array.isArray(detail)
        ? detail.map((item: { msg: string }) => item.msg).join("；") : `请求失败（${response.status}）`), response.status, payload?.error);
    }
    if (!valid(payload)) {
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

export function validTask(value: unknown): value is RepairTask {
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
    code_text: draft.codeText, file_name: ["code_file", "python_file", "pasted_code", "directory", "zip_archive"].includes(draft.sourceType) ? draft.fileName : "",
    file_content: ["code_file", "python_file"].includes(draft.sourceType) ? draft.fileContent : null,
    ...(draft.sourceType === "directory" ? { project_files: draft.projectFiles ?? null } : {}),
    ...(draft.sourceType === "zip_archive" ? { archive_base64: draft.archiveBase64 ?? null } : {}),
    language: draft.language, test_command: draft.testCommand, reproduce_command: draft.reproduceCommand,
    regression_command: draft.regressionCommand || "",
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
export const taskStreamUrl = (id: string, cursor = 0) =>
  `/api/tasks/${encodeURIComponent(id)}/stream?cursor=${encodeURIComponent(cursor)}&account_id=${encodeURIComponent(accountId)}`;
export const reviewTask = (id: string, decision: ReviewRecord["decision"], reason: string, patchHash: string) =>
  request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}/review`, { decision, reason, patch_hash: patchHash });
export const clarifyTask = (id: string, answer: string) =>
  request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}/clarification`, { answer }, undefined, validTask);
export const resolveToolOutcome = (id: string, operation_id: string, outcome: "completed" | "not_applied", evidence: string) =>
  request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}/tool-outcome`, { operation_id, outcome, evidence }, undefined, validTask);
export const authorizeTool = (id: string, authorization_id: string, decision: "approved" | "rejected" | "renew", reason: string) =>
  request<RepairTask>(`/api/tasks/${encodeURIComponent(id)}/tool-authorization`, { authorization_id, decision, reason }, undefined, validTask);

const validSession = (value: any) => /^[a-f0-9]{32}$/.test(value?.user?.id || "")
  && typeof value?.user?.username === "string" && /^[a-f0-9]{64}$/.test(value?.csrf_token || "");
const validMemories = (value: any) => Array.isArray(value?.memories) && value.memories.every((item: any) =>
  /^[a-f0-9]{32}$/.test(item.id) && ["preference", "coding_rule"].includes(item.kind)
  && typeof item.content === "string" && Number.isFinite(item.updated_at));
export const getSession = () => request<Session>("/api/auth/me", undefined, undefined, validSession);
export const authenticate = (mode: "login" | "register", username: string, password: string) =>
  request<Session>(`/api/auth/${mode}`, { username, password }, undefined, validSession);
export const logout = () => request<{ ok: boolean }>("/api/auth/logout", {}, undefined, value => value?.ok === true);
export const getMemories = () => request<{ memories: PersonalMemory[] }>("/api/memories", undefined, undefined, validMemories);
export const saveMemory = (kind: PersonalMemory["kind"], content: string, id?: string) =>
  request<{ memories: PersonalMemory[] }>(`/api/memories${id ? `/${encodeURIComponent(id)}` : ""}`,
    { kind, content }, undefined, validMemories, id ? "PUT" : "POST");
export const removeMemory = (id: string) => request<{ memories: PersonalMemory[] }>(`/api/memories/${encodeURIComponent(id)}`,
  undefined, undefined, validMemories, "DELETE");
export const memoryStats = () => request<{ total: number }>("/api/knowledge/stats", undefined, undefined, value => Number.isInteger(value?.total));
const validKnowledgeCandidate = (value: any) => /^[a-f0-9]{32}$/.test(value?.id || "")
  && /^[a-f0-9]{64}$/.test(value?.content_hash || "") && typeof value.status === "string"
  && !!value.record && typeof value.record === "object" && Array.isArray(value.audit);
export const getKnowledgeCandidates = (status = "needs_human") => request<{ candidates: KnowledgeCandidate[] }>(
  `/api/knowledge/candidates?status=${encodeURIComponent(status)}`, undefined, undefined,
  value => Array.isArray(value?.candidates) && value.candidates.every(validKnowledgeCandidate));
export const reviewKnowledgeCandidate = (candidate: KnowledgeCandidate, decision: "approve" | "decline", reason: string) =>
  request<KnowledgeCandidate>(`/api/knowledge/candidates/${encodeURIComponent(candidate.id)}/review`,
    { decision, reason, content_hash: candidate.content_hash }, undefined, validKnowledgeCandidate, undefined, 130_000);
const validConversation = (value: any) => /^[a-f0-9]{32}$/.test(value?.id || "") && typeof value.summary === "string" && Array.isArray(value.messages) && Array.isArray(value.candidates);
export const listConversations = () => request<{ conversations: Array<Pick<Conversation, "id" | "title" | "summary">> }>("/api/conversations", undefined, undefined, value => Array.isArray(value?.conversations));
export const createConversation = () => request<Conversation>("/api/conversations", {}, undefined, validConversation);
export const getConversation = (id: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, undefined, undefined, validConversation);
export const sendChat = (id: string, content: string, request_id: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}/messages`, { content, request_id }, undefined, validConversation, undefined, 180_000);
export const decideCandidate = (id: string, candidate: MemoryCandidate, accept: boolean) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidate.id)}/${accept ? "accept" : "reject"}`, accept ? { kind: candidate.kind, content: candidate.content } : {}, undefined, validConversation);
