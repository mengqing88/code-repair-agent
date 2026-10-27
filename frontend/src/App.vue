<script setup lang="ts">
import { computed, ref } from "vue";
import type { PreviewReview, TaskDraft } from "./types";
import AppIcon from "./components/AppIcon.vue";
import TaskComposer from "./components/TaskComposer.vue";
import ReviewPanel from "./components/ReviewPanel.vue";

// page：当前显示的三个页面阶段；数据仍只保存在本地页面内存中。
const page = ref<"new" | "prepared" | "review">("new");
// review：保存用户对示例补丁的接受或拒绝结果。
const review = ref<PreviewReview | null>(null);
// preparedTask：保存通过表单校验后用于预览的任务输入。
const preparedTask = ref<TaskDraft | null>(null);
// announcement：给屏幕阅读器播报页面状态变化。
const announcement = ref("");

/** 创建一份空任务，避免新任务复用上一份表单数据。 */
function emptyDraft(): TaskDraft {
  return {
    sourceType: "python_file",
    path: "",
    revision: "",
    codeText: "",
    fileName: "",
    fileContent: null,
    errorReport: "",
    testTarget: "",
  };
}

// draft：当前正在编辑的任务表单。
const draft = ref<TaskDraft>(emptyDraft());

// 页面标题和说明只随当前阶段变化，帮助用户确认下一步动作。
const title = computed(() => {
  if (page.value === "prepared") return "确认任务输入";
  if (page.value === "review") return "审核候选补丁";
  return "新建修复任务";
});
const subtitle = computed(() => {
  if (page.value === "prepared") return "确认无误后查看补丁和验证结果。";
  if (page.value === "review") return "查看修改依据，再接受或拒绝补丁。";
  return "提供代码和错误报告，开始一次修复任务。";
});
const sourceLabel = computed(() => {
  const task = preparedTask.value;
  if (!task) return "";
  return task.sourceType === "pasted_code" ? "粘贴的 Python 代码" : task.path;
});

/** 清空当前任务并回到第一步。 */
function newTask() {
  draft.value = emptyDraft();
  preparedTask.value = null;
  review.value = null;
  announcement.value = "已开始新任务。";
  page.value = "new";
}

/** 保存通过校验的表单输入，进入任务摘要页面。 */
function prepareTask() {
  preparedTask.value = { ...draft.value };
  announcement.value = "任务输入已校验。";
  page.value = "prepared";
}

/** 保存当前页面的示例审核结果。 */
function recordReview(decision: PreviewReview["decision"], reason: string) {
  review.value = {
    decision,
    reason,
    createdAt: new Date().toLocaleString("zh-CN", { hour12: false }),
  };
  announcement.value = "审核结果已记录在本页。";
}
</script>

<template>
  <div class="workbench">
    <header class="app-header">
      <a class="brand" href="#" @click.prevent="newTask">
        <span class="brand-mark" aria-hidden="true"><span /><span /><span /></span>
        <span>修复工作台</span>
      </a>
      <div class="header-actions">
        <span class="preview-indicator"><span class="status-dot mint" />界面预览</span>
        <button class="button subtle" type="button" @click="newTask">
          <AppIcon name="plus" />新建任务
        </button>
      </div>
    </header>

    <main class="main-content">
      <div class="page-heading">
        <div>
          <span class="eyebrow">CODE REPAIR AGENT</span>
          <h1>{{ title }}</h1>
          <p>{{ subtitle }}</p>
        </div>
        <div class="step-indicator" aria-label="当前步骤">
          <span :class="{ active: page === 'new' }">1 输入</span>
          <span aria-hidden="true">→</span>
          <span :class="{ active: page === 'prepared' }">2 确认</span>
          <span aria-hidden="true">→</span>
          <span :class="{ active: page === 'review' }">3 审核</span>
        </div>
      </div>

      <div class="budget-line">
        <span>默认预算</span>
        <span class="mono">10 轮 · 120 秒 · 20,000 tokens · ¥0.10</span>
      </div>

      <section v-if="page === 'new'" class="panel">
        <div class="panel-heading">
          <div><span class="panel-dot" /><h2>任务输入</h2></div>
          <span class="muted">带 * 的字段必填</span>
        </div>
        <TaskComposer v-model="draft" @submit="prepareTask" />
      </section>

      <section v-else-if="page === 'prepared' && preparedTask" class="panel prepared-panel">
        <div class="section-heading compact-heading">
          <div><span class="section-number">已确认</span><h2>任务摘要</h2></div>
          <span class="badge neutral">尚未执行</span>
        </div>
        <dl class="task-summary">
          <div><dt>代码来源</dt><dd class="mono">{{ sourceLabel }}</dd></div>
          <div v-if="preparedTask.revision"><dt>固定版本</dt><dd class="mono">{{ preparedTask.revision }}</dd></div>
          <div><dt>测试范围</dt><dd class="mono">{{ preparedTask.testTarget || "pytest ." }}</dd></div>
        </dl>
        <div class="report-preview">
          <span class="eyebrow">错误报告</span>
          <p>{{ preparedTask.errorReport }}</p>
        </div>
        <p class="preview-notice"><span class="status-dot" />当前是界面预览，后端接入后才会调用模型和运行测试。</p>
        <div class="composer-footer">
          <button class="button subtle" type="button" @click="page = 'new'">继续编辑</button>
          <button class="button primary" type="button" @click="page = 'review'">查看补丁<AppIcon name="arrow" /></button>
        </div>
      </section>

      <section v-else class="panel">
        <ReviewPanel
          :review="review"
          @reviewed="recordReview"
          @reset="review = null"
        />
      </section>

      <footer class="page-footer">
        <span>复现有记录 · 修改有依据 · 结果由你确认</span>
        <span class="mono">本地预览 · 数据仅保存在当前页面</span>
      </footer>
    </main>

    <div class="visually-hidden" role="status" aria-live="polite">{{ announcement }}</div>
  </div>
</template>
