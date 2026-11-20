<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { BudgetKind, RepairTask, ReviewRecord, TaskDraft, TaskTracePage } from "./types";
import { statusLabels } from "./types";
import { ApiError, createTask, getRequest, getTask, reviewTask, clarifyTask, extendTaskBudget, authorizeTool, resolveToolOutcome as resolveToolOutcomeApi, taskFingerprint, getSession, logout, setApiSession, type BudgetExtensionRequest, type Session, type User } from "./api";
import { subscribeTask, type StreamState } from "./task-stream";
import { tokenBudgetExtensionBlocker, validTokenBudgetGrant } from "./task-budget";
import AppIcon from "./components/AppIcon.vue";
import TaskComposer from "./components/TaskComposer.vue";
import ReviewPanel from "./components/ReviewPanel.vue";
import AccountGate from "./components/AccountGate.vue";
import MemoryPanel from "./components/MemoryPanel.vue";
import ChatPanel from "./components/ChatPanel.vue";
import { captureClientError, setDiagnosticSession } from "./diagnostics";

const user = ref<User | null>(null);
const checkingSession = ref(true);
const sessionError = ref("");
const showMemory = ref(false);
const showChat = ref(false);
const signingOut = ref(false);
const clarificationAnswer = ref("");
const toolOutcomeEvidence = ref("");
const resolvingToolOutcome = ref(false);
const authorizationReason = ref("");
const budgetTokens = ref<number | null>(null);
const budgetReason = ref("");
const pendingBudgetGrant = ref<(BudgetExtensionRequest & { task_id: string }) | null>(null);

const page = ref<"new" | "prepared" | "review">("new");
const task = ref<RepairTask | null>(null);
const preparedTask = ref<TaskDraft | null>(null);
const announcement = ref("");
const error = ref("");
const busy = ref(false);
const refreshing = ref(false);
let generation = 0;
let poll: ReturnType<typeof setTimeout> | undefined;
let pollFailures = 0;
let stopStream: (() => void) | null = null;
let streamTaskId = "";
let snapshotVersion = 0;
const streamState = ref<StreamState>("closed");
const reviewUnknown = ref(false);
const liveEvents = ref<string[]>([]);
const pendingKey = computed(() => `repair-pending-request:${user.value?.id}`);
const pending = ref<{ key: string; fingerprint: string } | null>(null);
function savePending(value: typeof pending.value) {
  pending.value = value;
  try {
    if (value) sessionStorage.setItem(pendingKey.value, JSON.stringify(value));
    else sessionStorage.removeItem(pendingKey.value);
  } catch {
    const id = captureClientError("storage_error");
    if (value) throw new Error(`无法保存提交标识，请检查浏览器存储设置后再提交（错误编号 ${id}）。`);
  }
}
function clientError(event: Event) {
  const detail = (event as CustomEvent<{ error_id: string; code: string }>).detail;
  if (["component_error", "browser_error", "unhandled_rejection", "resource_error"].includes(detail.code)) {
    error.value = `页面执行或资源加载发生异常，请刷新并按错误编号查询诊断：${detail.error_id}`;
  }
}
function showTask(result: RepairTask) {
  if (task.value?.state.task_id !== result.state.task_id) {
    closeTaskStream(); liveEvents.value = []; traceCursor = 0;
    pendingBudgetGrant.value = loadBudgetGrant(result.state.task_id);
    budgetTokens.value = pendingBudgetGrant.value?.tokens ?? null;
    budgetReason.value = pendingBudgetGrant.value?.reason ?? "";
  }
  task.value = result; page.value = "review"; remember(result.state.task_id);
  const grant = pendingBudgetGrant.value;
  if (grant?.task_id === result.state.task_id
    && (result.execution !== "idle" || result.state.status !== "budget_exceeded"
      || (result.state.budget_exhausted !== "tokens" && result.state.error_info?.budget_kind !== "tokens"))) {
    clearBudgetGrant(result.state.task_id);
    budgetTokens.value = null; budgetReason.value = "";
  }
  if (result.review || result.execution === "idle") reviewUnknown.value = false;
}
function budgetGrantStorageKey(taskId: string) {
  return `repair-budget-extension:${user.value?.id}:${taskId}`;
}
function loadBudgetGrant(taskId: string) {
  if (!user.value) return null;
  const key = budgetGrantStorageKey(taskId);
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (saved === null) return null;
    if (typeof saved !== "object" || saved.task_id !== taskId
      || typeof saved.request_id !== "string" || !/^[A-Za-z0-9_-]{8,128}$/.test(saved.request_id)
      || !Number.isInteger(saved.tokens) || saved.tokens < 1 || saved.tokens > 200_000
      || typeof saved.reason !== "string" || !saved.reason.trim() || saved.reason.length > 2000) {
      throw new Error("invalid budget extension request");
    }
    return saved as BudgetExtensionRequest & { task_id: string };
  } catch {
    try { sessionStorage.removeItem(key); } catch {}
    error.value = `无法读取本地预算请求记录，请检查浏览器存储设置（错误编号 ${captureClientError("storage_error")}）。`;
    return null;
  }
}
function saveBudgetGrant(taskId: string, grant: BudgetExtensionRequest & { task_id: string }) {
  try {
    sessionStorage.setItem(budgetGrantStorageKey(taskId), JSON.stringify(grant));
    pendingBudgetGrant.value = grant;
  } catch {
    const id = captureClientError("storage_error");
    throw new Error(`无法安全保存追加请求标识，尚未发送请求（错误编号 ${id}）。`);
  }
}
function clearBudgetGrant(taskId: string) {
  try { sessionStorage.removeItem(budgetGrantStorageKey(taskId)); }
  catch { error.value = `追加请求已返回，但无法清除本地请求记录（错误编号 ${captureClientError("storage_error")}）。`; }
  if (pendingBudgetGrant.value?.task_id === taskId) pendingBudgetGrant.value = null;
}
function closeTaskStream() {
  stopStream?.(); stopStream = null; streamTaskId = "";
  streamState.value = "closed";
}
let traceCursor = 0;
function addLiveEvents(page: TaskTracePage) {
  traceCursor = page.next_cursor;
  const labels = page.events.map(record => {
    const agent = typeof record.agent_label === "string" ? record.agent_label : "";
    const operation = typeof record.operation === "string" ? record.operation : "trace";
    const status = typeof record.event === "string" ? record.event : "更新";
    return `${agent ? `${agent} · ` : ""}${operation} · ${status}`;
  });
  if (labels.length) liveEvents.value = [...liveEvents.value, ...labels].slice(-8);
}
function startTaskStream(id: string) {
  if (streamTaskId === id) return;
  closeTaskStream(); streamTaskId = id;
  const version = generation;
  stopStream = subscribeTask(id, {
    task: result => { if (version === generation) { snapshotVersion++; showTask(result); error.value = ""; pollFailures = 0; } },
    trace: page => { if (version === generation) addLiveEvents(page); },
    state: state => {
      if (version !== generation) return;
      streamState.value = state;
      if (state === "live") clearTimeout(poll);
      else if (state === "reconnecting" || state === "fallback") schedule();
      else if (state === "closed") { clearTimeout(poll); stopStream = null; streamTaskId = ""; }
    },
    expired: () => { if (version === generation) sessionExpired(); },
  }, traceCursor);
}
function emptyDraft(): TaskDraft {
  return { sourceType: "code_file", path: "", revision: "", codeText: "", fileName: "",
    fileContent: null, errorReport: "", testTarget: "", language: "auto", testCommand: "", reproduceCommand: "", regressionCommand: "" };
}
const draft = ref<TaskDraft>(emptyDraft());
const running = computed(() => task.value !== null && task.value.execution !== "idle");
const budgetLabels: Record<BudgetKind, string> = {
  runtime: "时间预算耗尽",
  tokens: "模型 Token 预算耗尽",
  cost: "模型费用预算耗尽",
  tool_calls: "工具调用预算耗尽",
  request_retries: "请求重试次数耗尽",
  repair_attempts: "修复尝试次数耗尽",
};
const budgetMarker = computed(() => {
  const state = task.value?.state;
  if (!state || !["budget_exceeded", "failed", "needs_input"].includes(state.status)) return "";
  return state.error_info?.budget_label || (state.budget_exhausted ? budgetLabels[state.budget_exhausted] : "");
});
const tokenBudgetExhausted = computed(() => task.value?.state.status === "budget_exceeded"
  && (task.value.state.budget_exhausted === "tokens" || task.value.state.error_info?.budget_kind === "tokens"));
const tokenBudgetBlocker = computed(() => tokenBudgetExtensionBlocker(task.value));
const showTokenBudgetExtension = computed(() => tokenBudgetExhausted.value && !tokenBudgetBlocker.value);
const validTokenGrant = computed(() => validTokenBudgetGrant(budgetTokens.value, budgetReason.value));
const canSubmitTokenGrant = computed(() => !busy.value && !tokenBudgetBlocker.value
  && (pendingBudgetGrant.value?.task_id === task.value?.state.task_id || validTokenGrant.value));
const taskStatusLabel = computed(() => {
  if (budgetMarker.value && task.value?.state.status !== "needs_input" && !task.value?.state.error_info?.outcome_unknown) return budgetMarker.value;
  return task.value ? statusLabels[task.value.state.status] || task.value.state.status : "";
});
const recoveryText = computed(() => {
  const progress = task.value?.progress;
  const status = progress?.recovery?.status;
  if (!status) return "";
  const labels: Record<string, string> = {
    working: "正在核对中断任务，已保留恢复前的检查点。",
    rolled_back: "已还原恢复前的任务内容。",
    resumed: "已核对中断位置，并恢复可安全继续的流程。",
    paused: "自动恢复已暂停，请按下方错误记录核对。",
    rollback_pending: "恢复前的内容尚未完全还原，请保留任务并核对错误记录。",
  };
  return `${labels[status] || status} 自动恢复 ${progress?.recovery_count || 0} 次。`;
});
const title = computed(() => page.value === "prepared" ? "确认任务输入" : page.value === "review" ? "修复任务与补丁审核" : "新建修复任务");
const subtitle = computed(() => page.value === "prepared" ? "确认后将调用模型、运行代码并执行测试。" : page.value === "review"
  ? "查看真实执行结果，再接受或拒绝通过验证的补丁。" : "提供代码和错误报告，开始一次修复任务。");
function remember(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("task", id); else url.searchParams.delete("task");
  window.history.replaceState(null, "", url);
}
function newTask() {
  if (!user.value || busy.value) return;
  if (pending.value || reviewUnknown.value) { error.value = "上次提交结果尚未确认，请先查询状态。"; return; }
  generation++;
  pollFailures = 0;
  clearTimeout(poll);
  closeTaskStream(); liveEvents.value = []; traceCursor = 0;
  draft.value = emptyDraft(); preparedTask.value = null; task.value = null;
  error.value = ""; page.value = "new"; remember(null);
  announcement.value = "已开始新任务。";
}
function prepareTask() {
  preparedTask.value = { ...draft.value };
  error.value = ""; page.value = "prepared";
}
function schedule() {
  clearTimeout(poll);
  if (running.value && streamState.value !== "live") poll = setTimeout(() => void refresh(), 1000);
}
async function refresh(id = task.value?.state.task_id ?? new URL(window.location.href).searchParams.get("task")) {
  if (!user.value || !id || refreshing.value) return;
  const version = generation;
  const snapshot = snapshotVersion;
  refreshing.value = true;
  try {
    const result = await getTask(id);
    if (version !== generation || snapshot !== snapshotVersion) return;
    showTask(result); error.value = ""; pollFailures = 0;
    startTaskStream(result.state.task_id);
    schedule();
  } catch (cause) {
    if (version === generation && snapshot === snapshotVersion) {
      error.value = cause instanceof Error ? cause.message : "读取任务失败";
      if (cause instanceof ApiError && cause.retryable && pollFailures++ < 3) {
        const delay = Math.max(Math.min(8000, 1000 * 2 ** pollFailures) + Math.random() * 500,
          (cause.detail?.retry_after || 0) * 1000);
        poll = setTimeout(() => void refresh(id), delay);
      } else error.value += " 自动重连已停止，可手动查询状态。";
    }
  } finally { if (version === generation) refreshing.value = false; }
}
async function startTask() {
  if (!preparedTask.value || busy.value) return;
  const version = generation;
  const input = { ...preparedTask.value };
  busy.value = true; error.value = "";
  try {
    const fingerprint = await taskFingerprint(input);
    if (version !== generation) return;
    if (pending.value && pending.value.fingerprint !== fingerprint) {
      throw new Error("上次提交结果尚未确认，请查询状态或恢复相同的任务输入后重试。");
    }
    if (!pending.value) savePending({ key: crypto.randomUUID(), fingerprint });
    const key = pending.value!.key;
    let result: RepairTask | null = null;
    try { result = await getRequest(key); }
    catch (cause) { if (!(cause instanceof ApiError) || cause.status !== 404) throw cause; }
    if (version !== generation) return;
    if (!result) result = await createTask(input, key);
    if (version !== generation) return;
    showTask(result); savePending(null);
    liveEvents.value = []; startTaskStream(result.state.task_id);
    announcement.value = "任务已提交，正在执行 Python 修复工作流。";
    schedule();
  } catch (cause) {
    if (version !== generation) return;
    if (cause instanceof ApiError && !cause.uncertain && cause.status !== 409) savePending(null);
    error.value = cause instanceof Error ? cause.message : "提交失败";
    if (pending.value) error.value += " 本次请求标识已保存，请查询状态后继续。";
  }
  finally { if (version === generation) busy.value = false; }
}
async function recoverSubmission() {
  if (!pending.value || busy.value) return;
  const version = generation;
  busy.value = true;
  try {
    const result = await getRequest(pending.value.key);
    if (version !== generation) return;
    showTask(result); savePending(null); error.value = ""; schedule();
    liveEvents.value = []; startTaskStream(result.state.task_id);
  } catch (cause) {
    if (version !== generation) return;
    error.value = cause instanceof ApiError && cause.status === 404
      ? "尚未查到任务。请保留相同输入再次提交；页面刷新后需重新填写原输入。"
      : cause instanceof Error ? cause.message : "查询失败";
  } finally { if (version === generation) busy.value = false; }
}
async function recordReview(decision: ReviewRecord["decision"], reason: string) {
  if (!task.value?.state.patch_hash || busy.value) return;
  busy.value = true; error.value = "";
  clearTimeout(poll);
  closeTaskStream();
  const version = ++generation;
  try {
    const result = await reviewTask(task.value.state.task_id, decision, reason, task.value.state.patch_hash);
    if (version !== generation) return;
    showTask(result); announcement.value = "审核决定已写入数据库。";
    startTaskStream(result.state.task_id);
  } catch (cause) {
    if (version !== generation) return;
    error.value = cause instanceof Error ? cause.message : "审核提交失败";
    if (cause instanceof ApiError && cause.uncertain) {
      reviewUnknown.value = true;
      error.value += " 请先读取任务，核对审核决定。";
    }
    poll = setTimeout(() => void refresh(), 1000);
  } finally { if (version === generation) busy.value = false; }
}
async function submitClarification() {
  if (!task.value || busy.value || !clarificationAnswer.value.trim()) return;
  busy.value = true; error.value = "";
  try {
    const result = await clarifyTask(task.value.state.task_id, clarificationAnswer.value);
    clarificationAnswer.value = ""; showTask(result); startTaskStream(result.state.task_id);
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "补充说明失败"; }
  finally { busy.value = false; }
}
async function submitTokenBudgetExtension() {
  const current = task.value;
  if (!current || busy.value || !showTokenBudgetExtension.value) return;
  const version = generation;
  const taskId = current.state.task_id;
  let grant = pendingBudgetGrant.value?.task_id === taskId ? pendingBudgetGrant.value : null;
  if (!grant) {
    if (!validTokenGrant.value || budgetTokens.value === null) return;
    grant = { task_id: taskId, request_id: crypto.randomUUID(), tokens: budgetTokens.value,
      reason: budgetReason.value.trim() };
    try { saveBudgetGrant(taskId, grant); }
    catch (cause) { error.value = cause instanceof Error ? cause.message : "无法保存追加请求，尚未发送。"; return; }
  } else {
    busy.value = true; error.value = "";
    let latest: RepairTask;
    try {
      latest = await getTask(taskId);
    } catch (cause) {
      if (version !== generation) return;
      error.value = cause instanceof Error ? cause.message : "无法确认任务状态，请稍后重试。";
      return;
    } finally { if (version === generation) busy.value = false; }
    if (version !== generation) return;
    if (latest.execution !== "idle" || latest.state.status !== "budget_exceeded") {
      clearBudgetGrant(taskId);
      budgetTokens.value = null; budgetReason.value = "";
      showTask(latest); startTaskStream(taskId); schedule();
      announcement.value = "已读取原追加请求对应的任务状态，未重复提交额度。";
      return;
    }
  }
  busy.value = true; error.value = "";
  try {
    const result = await extendTaskBudget(taskId, { request_id: grant.request_id, tokens: grant.tokens, reason: grant.reason });
    if (version !== generation) return;
    clearBudgetGrant(taskId);
    budgetTokens.value = null; budgetReason.value = "";
    showTask(result); startTaskStream(result.state.task_id); schedule();
    announcement.value = "Token 预算已追加，原任务已继续排队。";
  } catch (cause) {
    if (version !== generation) return;
    error.value = cause instanceof Error ? cause.message : "追加 Token 失败。";
    if (cause instanceof ApiError && cause.uncertain) {
      error.value += " 请求结果尚未确认；再次提交会复用同一请求号，不会重复追加。";
      void refresh(taskId);
    } else {
      clearBudgetGrant(taskId);
    }
  } finally { if (version === generation) busy.value = false; }
}
async function resolveToolOutcome(operationId: string, outcome: "completed" | "not_applied") {
  if (!task.value || busy.value || !toolOutcomeEvidence.value.trim()) return;
  busy.value = true; resolvingToolOutcome.value = true; error.value = "";
  try {
    const result = await resolveToolOutcomeApi(task.value.state.task_id, operationId, outcome, toolOutcomeEvidence.value);
    toolOutcomeEvidence.value = ""; showTask(result); startTaskStream(result.state.task_id);
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "核对结果提交失败"; }
  finally { busy.value = false; resolvingToolOutcome.value = false; }
}
async function decideToolAuthorization(id: string, decision: "approved" | "rejected" | "renew") {
  if (!task.value || busy.value || (decision === "rejected" && !authorizationReason.value.trim())) return;
  busy.value = true; error.value = "";
  try {
    const result = await authorizeTool(task.value.state.task_id, id, decision, authorizationReason.value);
    authorizationReason.value = ""; showTask(result); startTaskStream(result.state.task_id);
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "操作授权提交失败，请刷新任务查看状态"; }
  finally { busy.value = false; }
}
function clearPrivateState() {
  generation++; clearTimeout(poll); closeTaskStream(); pollFailures = 0; liveEvents.value = []; traceCursor = 0;
  task.value = null; preparedTask.value = null; draft.value = emptyDraft(); pending.value = null;
  budgetTokens.value = null; budgetReason.value = ""; pendingBudgetGrant.value = null;
  busy.value = false; refreshing.value = false; reviewUnknown.value = false;
  showMemory.value = false; showChat.value = false; clarificationAnswer.value = ""; toolOutcomeEvidence.value = "";
  authorizationReason.value = "";
  page.value = "new"; error.value = ""; announcement.value = "";
  user.value = null; setApiSession(null); setDiagnosticSession(null);
}
function sessionExpired() {
  if (!user.value) return;
  clearPrivateState();
  sessionError.value = "登录已过期或账号已切换，请重新登录。";
}
function signedIn(session: Session) {
  clearPrivateState(); sessionError.value = "";
  user.value = session.user; setApiSession(session);
  setDiagnosticSession(session.user.id, session.csrf_token);
  try {
    const saved = JSON.parse(sessionStorage.getItem(pendingKey.value) || "null");
    if (saved !== null && (typeof saved !== "object" || typeof saved.key !== "string"
      || !/^[a-zA-Z0-9_-]{8,128}$/.test(saved.key) || typeof saved.fingerprint !== "string"
      || !/^[a-f0-9]{64}$/.test(saved.fingerprint))) throw new Error("invalid pending submission");
    pending.value = saved;
  }
  catch { error.value = `无法读取本地提交标识，请检查浏览器存储设置（错误编号 ${captureClientError("storage_error")}）。`; }
  const id = new URL(window.location.href).searchParams.get("task");
  if (id) void refresh(id);
  else if (pending.value) void recoverSubmission();
}
async function checkSession() {
  checkingSession.value = true; sessionError.value = "";
  try { signedIn(await getSession()); }
  catch (cause) {
    if (!(cause instanceof ApiError) || cause.status !== 401) sessionError.value = cause instanceof Error ? cause.message : "读取登录状态失败";
  } finally { checkingSession.value = false; }
}
async function signOut() {
  signingOut.value = true;
  try { await logout(); clearPrivateState(); remember(null); }
  catch (cause) { if (user.value) error.value = cause instanceof Error ? cause.message : "退出失败，请重试"; }
  finally { signingOut.value = false; }
}
onMounted(() => {
  window.addEventListener("repair:client-error", clientError);
  window.addEventListener("repair:session-expired", sessionExpired);
  void checkSession();
});
onUnmounted(() => { generation++; clearTimeout(poll); closeTaskStream(); window.removeEventListener("repair:client-error", clientError); window.removeEventListener("repair:session-expired", sessionExpired); });
</script>

<template>
  <div class="workbench">
    <header class="app-header">
      <a class="brand" href="#" @click.prevent="newTask"><span class="brand-mark" aria-hidden="true"><span /><span /><span /></span><span>修复工作台</span></a>
      <div v-if="user" class="header-actions">
        <span class="preview-indicator">{{ user.username }}</span>
        <button class="button subtle" type="button" :aria-expanded="showMemory" @click="showMemory = !showMemory">我的记忆</button>
        <button class="button subtle" type="button" :aria-expanded="showChat" @click="showChat = !showChat">对话</button>
        <button class="button subtle" type="button" :disabled="busy" @click="newTask"><AppIcon name="plus" />新建任务</button>
        <button class="text-button" type="button" :disabled="signingOut" @click="signOut">{{ signingOut ? '退出中…' : '退出' }}</button>
      </div>
    </header>
    <main v-if="checkingSession || !user" class="main-content">
      <p v-if="checkingSession" class="preview-notice" role="status">正在读取登录状态…</p>
      <template v-else>
        <p v-if="sessionError" class="error-message" role="alert">{{ sessionError }} <button class="text-button" @click="checkSession">重新连接</button></p>
        <AccountGate @signed-in="signedIn" />
      </template>
    </main>
    <main v-else class="main-content">
      <MemoryPanel v-if="showMemory" :key="user.id" />
      <ChatPanel v-if="showChat" :key="user.id" />
      <div class="page-heading">
        <div><span class="eyebrow">CODE REPAIR AGENT</span><h1>{{ title }}</h1><p>{{ subtitle }}</p></div>
        <div class="step-indicator" aria-label="当前步骤">
          <span :class="{ active: page === 'new' }">1 输入</span><span aria-hidden="true">→</span>
          <span :class="{ active: page === 'prepared' }">2 确认</span><span aria-hidden="true">→</span>
          <span :class="{ active: page === 'review' }">3 执行与审核</span>
        </div>
      </div>
      <div class="budget-line"><span>默认预算</span><span class="mono">10 次修复重试 · 1200 秒 · 200,000 tokens · ¥0.10（需配置价格）</span></div>
      <p v-if="pending" class="preview-notice">存在尚未确认的提交。<button class="text-button" :disabled="busy" @click="recoverSubmission">查询原提交状态</button></p>
      <p v-if="error" class="error-message" role="alert">{{ error }} <button v-if="page !== 'prepared'" class="text-button" :disabled="refreshing" @click="refresh()">重新读取任务</button></p>
      <section v-if="page === 'new'" class="panel">
        <div class="panel-heading"><div><span class="panel-dot" /><h2>任务输入</h2></div><span class="muted">带 * 的字段必填</span></div>
        <TaskComposer v-model="draft" @submit="prepareTask" />
      </section>
      <section v-else-if="page === 'prepared' && preparedTask" class="panel prepared-panel">
        <div class="section-heading compact-heading"><div><span class="section-number">已确认</span><h2>任务摘要</h2></div><span class="badge neutral">尚未执行</span></div>
        <dl class="task-summary">
          <div><dt>代码来源</dt><dd class="mono">{{ preparedTask.sourceType === 'pasted_code' ? `粘贴代码（${preparedTask.fileName}）` : preparedTask.path }}</dd></div>
          <div><dt>语言</dt><dd>{{ preparedTask.language }}</dd></div>
          <div v-if="preparedTask.revision && preparedTask.sourceType === 'git_repository'"><dt>固定版本</dt><dd class="mono">{{ preparedTask.revision }}</dd></div>
          <div><dt>测试命令</dt><dd class="mono">{{ preparedTask.testCommand || (preparedTask.language === 'python' ? `pytest ${preparedTask.testTarget || '.'}` : '未配置') }}</dd></div>
        </dl>
        <div class="report-preview"><span class="eyebrow">错误报告</span><p>{{ preparedTask.errorReport }}</p></div>
        <p class="preview-notice"><span class="status-dot" />代码将在任务副本中执行。请使用可信代码；未收集到测试不会判为通过。</p>
        <div class="composer-footer">
          <button class="button subtle" type="button" :disabled="busy" @click="page = 'new'">继续编辑</button>
          <button class="button primary" type="button" :disabled="busy" @click="startTask">{{ busy ? '提交中…' : '开始修复' }}<AppIcon name="arrow" /></button>
        </div>
      </section>
      <template v-else-if="task">
        <section class="panel prepared-panel task-progress" aria-live="polite">
          <div class="section-heading"><h2>{{ task.review?.decision === 'rejected' && !running ? '已拒绝补丁' : taskStatusLabel }}</h2><span class="badge neutral">{{ task.execution === 'queued' ? '队列中' : running ? '执行中' : '已暂停或结束' }}</span></div>
          <p class="mono">任务 {{ task.state.task_id }} · {{ task.source_label }}</p>
          <p>自动执行 {{ task.state.elapsed_seconds.toFixed(1) }} 秒 · 重试 {{ task.state.attempt_count }} 次 · 剩余 {{ task.state.remaining_budget.tokens.toLocaleString() }} tokens</p>
          <p v-if="recoveryText" class="preview-notice">{{ recoveryText }}</p>
          <p v-if="task.state.question" class="preview-notice">{{ task.state.question }}</p>
          <ul v-if="task.state.question && task.state.clarification_reasons?.length" class="clarification-reasons">
            <li v-for="reason in task.state.clarification_reasons" :key="reason">{{ reason }}</li>
          </ul>
          <form v-if="task.state.status === 'needs_input' && task.state.awaiting_node && task.state.awaiting_node !== 'tool_authorization' && !task.state.error_info?.outcome_unknown" class="resolution-form" @submit.prevent="submitClarification">
            <label class="field"><span>补充说明</span><textarea v-model="clarificationAnswer" maxlength="4000" rows="3" :disabled="busy" placeholder="填写触发输入、操作步骤或预期结果" /></label>
            <button class="button primary" :disabled="busy || !clarificationAnswer.trim()">{{ busy ? '提交中…' : '补充并继续任务' }}</button>
          </form>
          <div v-if="task.pending_tool_authorizations?.length && task.state.awaiting_node === 'tool_authorization'" class="resolution-form">
            <strong>请审核本次外部写操作</strong>
            <div v-for="item in task.pending_tool_authorizations" :key="item.authorization_id">
              <p class="mono">{{ item.tool }}</p><p>{{ item.impact }}</p>
              <p>目标</p><pre>{{ JSON.stringify(item.target, null, 2) }}</pre>
              <p>完整参数</p><pre>{{ JSON.stringify(item.parameters, null, 2) }}</pre>
              <p class="muted">有效期至 {{ new Date(item.expires_at * 1000).toLocaleString() }}；批准后仅相同操作可执行。</p>
              <button v-if="Date.now() >= item.expires_at * 1000" class="button subtle" :disabled="busy" @click="decideToolAuthorization(item.authorization_id, 'renew')">授权已过期，重新申请</button>
              <label class="field"><span>审核说明（拒绝时必填）</span><textarea v-model="authorizationReason" maxlength="2000" rows="2" :disabled="busy" /></label>
              <div class="memory-actions"><button class="button subtle" :disabled="busy || !authorizationReason.trim()" @click="decideToolAuthorization(item.authorization_id, 'rejected')">拒绝操作</button><button class="button primary" :disabled="busy || Date.now() >= item.expires_at * 1000" @click="decideToolAuthorization(item.authorization_id, 'approved')">批准此操作并继续</button></div>
            </div>
          </div>
          <div v-if="task.pending_external_operations?.length" class="resolution-form">
            <strong>外部工具操作需要核对</strong>
            <p class="preview-notice">系统无法判断远端是否已完成写入。请先到对应服务查询，再选择结果；选择“未执行”后任务会重新分析，选择“已完成”则停止当前修复任务。</p>
            <label class="field"><span>核对依据 <span class="muted">至少 10 字</span></span><textarea v-model="toolOutcomeEvidence" maxlength="2000" rows="3" :disabled="busy" placeholder="例如：已在工单系统按操作编号查询记录，未找到新增工单。" /></label>
            <ul class="pending-operation-list"><li v-for="operation in task.pending_external_operations" :key="operation.operation_id"><span class="mono">{{ operation.tool }} · {{ operation.operation_id }}</span><div class="memory-actions"><button class="button subtle" :disabled="busy || toolOutcomeEvidence.trim().length < 10" @click="resolveToolOutcome(operation.operation_id, 'not_applied')">核实未执行</button><button class="button subtle" :disabled="busy || toolOutcomeEvidence.trim().length < 10" @click="resolveToolOutcome(operation.operation_id, 'completed')">核实已完成</button></div></li></ul>
          </div>
          <p v-if="budgetMarker" class="preview-notice">耗尽项：{{ budgetMarker }}</p>
          <form v-if="showTokenBudgetExtension" class="resolution-form" @submit.prevent="submitTokenBudgetExtension">
            <strong>追加模型 Token 预算</strong>
            <p class="input-hint">追加额度用于当前任务后续模型输入与输出；不会增加时间或费用预算。单次最多 200,000 Token。</p>
            <label class="field"><span>追加 Token 数量</span><input v-model.number="budgetTokens" type="number" min="1" max="200000" step="1" inputmode="numeric" :disabled="busy || pendingBudgetGrant?.task_id === task.state.task_id" required /></label>
            <label class="field"><span>追加原因</span><textarea v-model="budgetReason" maxlength="2000" rows="2" :disabled="busy || pendingBudgetGrant?.task_id === task.state.task_id" placeholder="说明还需要预算完成什么" required /></label>
            <p v-if="pendingBudgetGrant?.task_id === task.state.task_id" class="preview-notice">存在未确认的追加请求；重试将沿用原额度和请求号。</p>
            <button class="button primary" type="submit" :disabled="!canSubmitTokenGrant">{{ busy ? '提交中…' : pendingBudgetGrant?.task_id === task.state.task_id ? '查询并继续' : '追加 Token 并继续' }}</button>
          </form>
          <p v-else-if="tokenBudgetExhausted && tokenBudgetBlocker" class="preview-notice">{{ tokenBudgetBlocker }}</p>
          <p v-if="task.state.last_error" class="error-message">{{ task.state.last_error }}</p>
          <p v-if="task.state.error_info" class="preview-notice">{{ task.state.error_info.outcome_unknown ? '操作结果待核对。' : '' }}{{ task.state.error_info.next_action }}</p>
          <p v-if="task.state.error_info?.error_id" class="mono muted">错误编号 {{ task.state.error_info.error_id }} · {{ task.state.error_info.operation || '执行阶段' }}</p>
          <p v-if="task.state.error_info?.diagnostics_persisted === false" class="preview-notice">诊断文件写入失败，请同时检查服务进程的标准错误输出。</p>
          <p v-for="warning in task.state.warnings || []" :key="warning" class="preview-notice">{{ warning }}</p>
          <p v-if="task.state.accounting && (!task.state.accounting.cost_complete || !task.state.accounting.usage_complete)" class="muted">用量或费用统计不完整，当前数值仅代表已观测部分。</p>
          <p v-if="running" class="muted">{{ streamState === 'live' ? '正在实时接收任务进度。' : streamState === 'reconnecting' ? '实时连接重连中，暂时轮询状态。' : streamState === 'fallback' ? '实时连接不可用，正在轮询状态。' : '正在连接进度流…' }}刷新或离开页面后，服务端任务仍会继续。</p>
          <div v-if="liveEvents.length" class="live-events" aria-live="polite">
            <span class="eyebrow">实时事件</span>
            <p v-for="(item, index) in liveEvents" :key="index" class="mono muted">{{ item }}</p>
          </div>
          <button class="text-button" :disabled="refreshing || busy" @click="refresh()">{{ refreshing ? '读取中…' : '刷新状态' }}</button>
        </section>
        <section class="panel"><ReviewPanel :task="task" :busy="busy || running || reviewUnknown" @reviewed="recordReview" /></section>
      </template>
      <footer class="page-footer"><span>复现有记录 · 修改有依据 · 结果由你确认</span><span class="mono">任务与审核持久保存 · 当前链接可恢复任务</span></footer>
    </main>
    <div class="visually-hidden" role="status" aria-live="polite">{{ announcement }}</div>
  </div>
</template>
