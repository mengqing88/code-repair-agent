<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { RepairTask, ReviewRecord, TaskDraft } from "./types";
import { statusLabels } from "./types";
import { ApiError, createTask, getRequest, getTask, reviewTask, taskFingerprint } from "./api";
import AppIcon from "./components/AppIcon.vue";
import TaskComposer from "./components/TaskComposer.vue";
import ReviewPanel from "./components/ReviewPanel.vue";
import { captureClientError } from "./diagnostics";

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
const reviewUnknown = ref(false);
const pendingKey = "repair-pending-request";
const pending = ref<{ key: string; fingerprint: string } | null>(null);
function savePending(value: typeof pending.value) {
  pending.value = value;
  try {
    if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value));
    else sessionStorage.removeItem(pendingKey);
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
  task.value = result; page.value = "review"; remember(result.state.task_id);
  if (result.review || result.execution === "idle") reviewUnknown.value = false;
}
function emptyDraft(): TaskDraft {
  return { sourceType: "python_file", path: "", revision: "", codeText: "", fileName: "",
    fileContent: null, errorReport: "", testTarget: "" };
}
const draft = ref<TaskDraft>(emptyDraft());
const running = computed(() => task.value !== null && task.value.execution !== "idle");
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
  if (busy.value) return;
  if (pending.value || reviewUnknown.value) { error.value = "上次提交结果尚未确认，请先查询状态。"; return; }
  generation++;
  pollFailures = 0;
  clearTimeout(poll);
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
  if (running.value) poll = setTimeout(() => void refresh(), 1000);
}
async function refresh(id = task.value?.state.task_id ?? new URL(window.location.href).searchParams.get("task")) {
  if (!id || refreshing.value) return;
  const version = generation;
  refreshing.value = true;
  try {
    const result = await getTask(id);
    if (version !== generation) return;
    showTask(result); error.value = ""; pollFailures = 0;
    schedule();
  } catch (cause) {
    if (version === generation) {
      error.value = cause instanceof Error ? cause.message : "读取任务失败";
      if (cause instanceof ApiError && cause.retryable && pollFailures++ < 3) {
        const delay = Math.max(Math.min(8000, 1000 * 2 ** pollFailures) + Math.random() * 500,
          (cause.detail?.retry_after || 0) * 1000);
        poll = setTimeout(() => void refresh(id), delay);
      } else error.value += " 自动重连已停止，可手动查询状态。";
    }
  } finally { refreshing.value = false; }
}
async function startTask() {
  if (!preparedTask.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    const fingerprint = await taskFingerprint(preparedTask.value);
    if (pending.value && pending.value.fingerprint !== fingerprint) {
      throw new Error("上次提交结果尚未确认，请查询状态或恢复相同的任务输入后重试。");
    }
    if (!pending.value) savePending({ key: crypto.randomUUID(), fingerprint });
    const key = pending.value!.key;
    let result: RepairTask | null = null;
    try { result = await getRequest(key); }
    catch (cause) { if (!(cause instanceof ApiError) || cause.status !== 404) throw cause; }
    if (!result) result = await createTask(preparedTask.value, key);
    showTask(result); savePending(null);
    announcement.value = "任务已提交，正在执行 Python 修复工作流。";
    schedule();
  } catch (cause) {
    if (cause instanceof ApiError && !cause.uncertain && cause.status !== 409) savePending(null);
    error.value = cause instanceof Error ? cause.message : "提交失败";
    if (pending.value) error.value += " 本次请求标识已保存，请查询状态后继续。";
  }
  finally { busy.value = false; }
}
async function recoverSubmission() {
  if (!pending.value || busy.value) return;
  busy.value = true;
  try {
    const result = await getRequest(pending.value.key);
    showTask(result); savePending(null); error.value = ""; schedule();
  } catch (cause) {
    error.value = cause instanceof ApiError && cause.status === 404
      ? "尚未查到任务。请保留相同输入再次提交；页面刷新后需重新填写原输入。"
      : cause instanceof Error ? cause.message : "查询失败";
  } finally { busy.value = false; }
}
async function recordReview(decision: ReviewRecord["decision"], reason: string) {
  if (!task.value?.state.patch_hash || busy.value) return;
  busy.value = true; error.value = "";
  clearTimeout(poll);
  const version = ++generation;
  try {
    const result = await reviewTask(task.value.state.task_id, decision, reason, task.value.state.patch_hash);
    if (version !== generation) return;
    showTask(result); announcement.value = "审核决定已写入数据库。";
    schedule();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "审核提交失败";
    if (cause instanceof ApiError && cause.uncertain) {
      reviewUnknown.value = true;
      error.value += " 请先读取任务，核对审核决定。";
    }
    poll = setTimeout(() => void refresh(), 1000);
  } finally { busy.value = false; }
}
onMounted(() => {
  window.addEventListener("repair:client-error", clientError);
  try {
    const saved = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    if (saved !== null && (typeof saved !== "object" || typeof saved.key !== "string"
      || !/^[a-zA-Z0-9_-]{8,128}$/.test(saved.key) || typeof saved.fingerprint !== "string"
      || !/^[a-f0-9]{64}$/.test(saved.fingerprint))) throw new Error("invalid pending submission");
    pending.value = saved;
  }
  catch { error.value = `无法读取本地提交标识，请检查浏览器存储设置（错误编号 ${captureClientError("storage_error")}）。`; }
  const id = new URL(window.location.href).searchParams.get("task");
  if (id) void refresh(id);
  else if (pending.value) void recoverSubmission();
});
onUnmounted(() => { generation++; clearTimeout(poll); window.removeEventListener("repair:client-error", clientError); });
</script>

<template>
  <div class="workbench">
    <header class="app-header">
      <a class="brand" href="#" @click.prevent="newTask"><span class="brand-mark" aria-hidden="true"><span /><span /><span /></span><span>修复工作台</span></a>
      <div class="header-actions">
        <span class="preview-indicator"><span class="status-dot mint" />本地 Python 工作流</span>
        <button class="button subtle" type="button" :disabled="busy" @click="newTask"><AppIcon name="plus" />新建任务</button>
      </div>
    </header>
    <main class="main-content">
      <div class="page-heading">
        <div><span class="eyebrow">CODE REPAIR AGENT</span><h1>{{ title }}</h1><p>{{ subtitle }}</p></div>
        <div class="step-indicator" aria-label="当前步骤">
          <span :class="{ active: page === 'new' }">1 输入</span><span aria-hidden="true">→</span>
          <span :class="{ active: page === 'prepared' }">2 确认</span><span aria-hidden="true">→</span>
          <span :class="{ active: page === 'review' }">3 执行与审核</span>
        </div>
      </div>
      <div class="budget-line"><span>默认预算</span><span class="mono">10 次修复重试 · 120 秒 · 20,000 tokens · ¥0.10（需配置价格）</span></div>
      <p v-if="pending" class="preview-notice">存在尚未确认的提交。<button class="text-button" :disabled="busy" @click="recoverSubmission">查询原提交状态</button></p>
      <p v-if="error" class="error-message" role="alert">{{ error }} <button v-if="page !== 'prepared'" class="text-button" :disabled="refreshing" @click="refresh()">重新读取任务</button></p>
      <section v-if="page === 'new'" class="panel">
        <div class="panel-heading"><div><span class="panel-dot" /><h2>任务输入</h2></div><span class="muted">带 * 的字段必填</span></div>
        <TaskComposer v-model="draft" @submit="prepareTask" />
      </section>
      <section v-else-if="page === 'prepared' && preparedTask" class="panel prepared-panel">
        <div class="section-heading compact-heading"><div><span class="section-number">已确认</span><h2>任务摘要</h2></div><span class="badge neutral">尚未执行</span></div>
        <dl class="task-summary">
          <div><dt>代码来源</dt><dd class="mono">{{ preparedTask.sourceType === 'pasted_code' ? '粘贴的 Python 代码' : preparedTask.path }}</dd></div>
          <div v-if="preparedTask.revision && preparedTask.sourceType === 'git_repository'"><dt>固定版本</dt><dd class="mono">{{ preparedTask.revision }}</dd></div>
          <div><dt>测试范围</dt><dd class="mono">{{ preparedTask.testTarget || 'pytest .' }}</dd></div>
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
          <div class="section-heading"><h2>{{ task.review?.decision === 'rejected' && !running ? '已拒绝补丁' : statusLabels[task.state.status] || task.state.status }}</h2><span class="badge neutral">{{ task.execution === 'queued' ? '队列中' : running ? '执行中' : '已暂停或结束' }}</span></div>
          <p class="mono">任务 {{ task.state.task_id }} · {{ task.source_label }}</p>
          <p>自动执行 {{ task.state.elapsed_seconds.toFixed(1) }} 秒 · 重试 {{ task.state.attempt_count }} 次 · 剩余 {{ task.state.remaining_budget.tokens.toLocaleString() }} tokens</p>
          <p v-if="recoveryText" class="preview-notice">{{ recoveryText }}</p>
          <p v-if="task.state.question" class="preview-notice">{{ task.state.question }}</p>
          <p v-if="task.state.last_error" class="error-message">{{ task.state.last_error }}</p>
          <p v-if="task.state.error_info" class="preview-notice">{{ task.state.error_info.outcome_unknown ? '操作结果待核对。' : '' }}{{ task.state.error_info.next_action }}</p>
          <p v-if="task.state.error_info?.error_id" class="mono muted">错误编号 {{ task.state.error_info.error_id }} · {{ task.state.error_info.operation || '执行阶段' }}</p>
          <p v-if="task.state.error_info?.diagnostics_persisted === false" class="preview-notice">诊断文件写入失败，请同时检查服务进程的标准错误输出。</p>
          <p v-for="warning in task.state.warnings || []" :key="warning" class="preview-notice">{{ warning }}</p>
          <p v-if="task.state.accounting && (!task.state.accounting.cost_complete || !task.state.accounting.usage_complete)" class="muted">用量或费用统计不完整，当前数值仅代表已观测部分。</p>
          <p v-if="running" class="muted">页面每秒读取一次状态。刷新或离开页面后，服务端任务仍会继续。</p>
          <button class="text-button" :disabled="refreshing || busy" @click="refresh()">{{ refreshing ? '读取中…' : '刷新状态' }}</button>
        </section>
        <section class="panel"><ReviewPanel :task="task" :busy="busy || running || reviewUnknown" @reviewed="recordReview" /></section>
      </template>
      <footer class="page-footer"><span>复现有记录 · 修改有依据 · 结果由你确认</span><span class="mono">任务与审核持久保存 · 当前链接可恢复任务</span></footer>
    </main>
    <div class="visually-hidden" role="status" aria-live="polite">{{ announcement }}</div>
  </div>
</template>
