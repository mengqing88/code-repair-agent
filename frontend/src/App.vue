<script setup lang="ts">
import { computed, ref } from "vue";
import { budgets } from "./types";
import type { PreviewReview, TaskDraft } from "./types";
import AppIcon from "./components/AppIcon.vue";
import TaskComposer from "./components/TaskComposer.vue";
import ReviewPanel from "./components/ReviewPanel.vue";

// 页面状态：new 为任务输入，prepared 为输入摘要，review 为审核示例，history 为本次预览记录。
const page = ref<"new" | "prepared" | "review" | "history">("new");
const review = ref<PreviewReview | null>(null);
const preparedTask = ref<TaskDraft | null>(null);
const announcement = ref("");

/** 创建独立表单对象，避免新任务与上一份草稿共享数据。 */
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
const draft = ref<TaskDraft>(emptyDraft());
const title = computed(
  () =>
    ({
      new: "新建修复任务",
      prepared: "任务输入已就绪",
      review: "审阅候选补丁",
      history: "审核记录",
    })[page.value],
);
const subtitle = computed(
  () =>
    ({
      new: "从一份错误报告开始，让修复过程有据可查。",
      prepared: "核对代码来源和错误报告，准备进入修复工作流。",
      review: "先看证据，再确认修改。",
      history: "查看本次页面预览中的人工审核结果。",
    })[page.value],
);
const workflowSteps = [
  "任务输入",
  "错误复现",
  "上下文检索",
  "生成补丁",
  "自动验证",
  "人工审核",
];
const currentStep = computed(() => (page.value === "review" ? 5 : 0));
const sourceLabel = computed(() => {
  const task = preparedTask.value;
  if (!task) return "";
  return task.sourceType === "pasted_code" ? "粘贴的 Python 代码" : task.path;
});

/** 开始新的输入草稿，已有预览记录仍留在当前页面内存中。 */
function newTask() {
  draft.value = emptyDraft();
  announcement.value = "";
  page.value = "new";
}

/** 复制经过校验的表单输入，不启动模型、测试或数据库写入。 */
function prepareTask() {
  preparedTask.value = { ...draft.value };
  page.value = "prepared";
  announcement.value = "任务输入已校验，当前为界面预览。";
}

/** 将示例审核决定保存在页面内存，提供接受和拒绝的交互反馈。 */
function recordReview(decision: PreviewReview["decision"], reason: string) {
  review.value = {
    decision,
    reason,
    createdAt: new Date().toLocaleString("zh-CN", { hour12: false }),
  };
  announcement.value = "审核示例已记录在本页，尚未写入数据库。";
}
</script>

<template>
  <div class="workbench">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="page = 'new'"
        ><span class="brand-mark" aria-hidden="true"
          ><span /> <span /> <span /></span
        ><span>修复工作台<small>CODE REPAIR AGENT</small></span></a
      >
      <button class="button new-task-button" @click="newTask">
        <AppIcon name="plus" />新建任务<span class="mono">＋</span>
      </button>
      <nav class="main-navigation" aria-label="主导航">
        <button
          :class="{ active: page !== 'history' }"
          @click="page = preparedTask ? 'prepared' : 'new'"
        >
          <AppIcon name="workspace" />修复工作区<span class="nav-active-dot" />
        </button>
        <button
          :class="{ active: page === 'history' }"
          @click="page = 'history'"
        >
          <AppIcon name="history" />审核记录<span
            v-if="review"
            class="nav-count"
            >1</span
          >
        </button>
      </nav>
      <div class="sidebar-section-title">
        本次任务 <span>{{ preparedTask ? "01" : "00" }}</span>
      </div>
      <button
        v-if="preparedTask"
        class="sidebar-task"
        :class="{ selected: page === 'prepared' }"
        @click="page = 'prepared'"
      >
        <span class="status-dot" /><span
          >{{ sourceLabel }}<small>输入已就绪 · 待接入运行</small></span
        >
      </button>
      <div v-else class="sidebar-empty">
        <span class="empty-line" /><span class="empty-line" />
        <p>还没有修复任务<br />提交代码和报告即可开始。</p>
      </div>
      <div class="sidebar-bottom">
        <div>
          <span class="status-dot mint" /><span>本地工作空间</span
          ><span class="mono">v0.1</span>
        </div>
        <small>Vue 3 · TypeScript · Vite</small>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb">
          工作空间<AppIcon name="chevron" /><span>{{
            page === "history" ? "审核记录" : "修复工作区"
          }}</span>
        </div>
        <span class="preview-indicator"
          ><span class="status-dot" />界面预览 v0</span
        >
      </header>
      <main class="main-content">
        <div class="page-heading">
          <div>
            <span class="eyebrow">{{
              page === "review"
                ? "REVIEW & DECIDE"
                : page === "history"
                  ? "REVIEW HISTORY"
                  : "REPAIR WORKSPACE"
            }}</span>
            <h1>{{ title }}</h1>
            <p>{{ subtitle }}</p>
          </div>
          <button
            v-if="page !== 'review'"
            class="button subtle sample-button"
            @click="page = 'review'"
          >
            <AppIcon name="branch" />查看审核示例<AppIcon
              name="arrow"
            /></button
          ><button
            v-else
            class="button subtle"
            @click="page = preparedTask ? 'prepared' : 'new'"
          >
            返回任务输入
          </button>
        </div>
        <div class="workspace-grid">
          <div class="workspace-main">
            <section
              v-if="page !== 'history'"
              class="workflow-strip"
              aria-label="修复流程"
            >
              <div class="workflow-top">
                <span>修复流程</span
                ><span>{{
                  page === "review" ? "审核布局示例" : "从输入到人工确认"
                }}</span>
              </div>
              <ol>
                <li
                  v-for="(step, index) in workflowSteps"
                  :key="step"
                  :class="{ current: currentStep === index }"
                  :aria-current="currentStep === index ? 'step' : undefined"
                >
                  <span class="step-marker">{{
                    String(index + 1).padStart(2, "0")
                  }}</span
                  ><span>{{ step }}</span>
                </li>
              </ol>
            </section>
            <section v-if="page === 'new'" class="panel input-panel">
              <div class="panel-heading">
                <div>
                  <span class="panel-dot" />
                  <h2>任务输入</h2>
                </div>
                <span class="muted"
                  >必需字段标记为 <span class="required">*</span></span
                >
              </div>
              <TaskComposer v-model="draft" @submit="prepareTask" />
            </section>
            <section
              v-else-if="page === 'prepared' && preparedTask"
              class="panel prepared-panel"
            >
              <div class="ready-heading">
                <div class="ready-symbol"><AppIcon name="check" /></div>
                <div>
                  <span class="eyebrow">INPUT READY</span>
                  <h2>输入已校验</h2>
                  <p>
                    任务保留在当前页面中。接入后端后，将从错误复现开始执行。
                  </p>
                </div>
              </div>
              <dl class="task-summary">
                <div>
                  <dt>代码来源</dt>
                  <dd class="mono">{{ sourceLabel }}</dd>
                </div>
                <div v-if="preparedTask.revision">
                  <dt>固定版本</dt>
                  <dd class="mono">{{ preparedTask.revision }}</dd>
                </div>
                <div>
                  <dt>测试范围</dt>
                  <dd class="mono">
                    {{ preparedTask.testTarget || "pytest ." }}
                  </dd>
                </div>
              </dl>
              <div class="report-preview">
                <span class="eyebrow">原始错误报告</span>
                <p>{{ preparedTask.errorReport }}</p>
              </div>
              <div class="preview-notice">
                <span class="status-dot" />
                <p>当前为界面预览，尚未调用模型或运行测试。</p>
              </div>
              <div class="composer-footer">
                <button class="button subtle" @click="page = 'new'">
                  继续编辑</button
                ><button class="button primary" @click="page = 'review'">
                  查看审核示例<AppIcon name="arrow" />
                </button>
              </div>
            </section>
            <section v-else-if="page === 'review'" class="panel">
              <ReviewPanel
                :review="review"
                @reviewed="recordReview"
                @reset="review = null"
              />
            </section>
            <section v-else-if="page === 'history'" class="panel history-panel">
              <div class="panel-heading">
                <div>
                  <AppIcon name="history" />
                  <h2>人工审核</h2>
                </div>
                <span class="badge neutral">本页预览</span>
              </div>
              <div v-if="!review" class="empty-state">
                <div class="empty-state-glyph"><AppIcon name="history" /></div>
                <h2>还没有审核记录</h2>
                <p>审阅补丁后，接受决定或拒绝理由将显示在这里。</p>
                <button class="button subtle" @click="page = 'review'">
                  体验补丁审核<AppIcon name="arrow" />
                </button>
              </div>
              <div v-else class="history-record">
                <div class="history-record-title">
                  <span class="mono">example.py</span
                  ><span
                    class="badge"
                    :class="review.decision === 'accepted' ? 'green' : 'red'"
                    >{{
                      review.decision === "accepted" ? "接受补丁" : "拒绝补丁"
                    }}</span
                  >
                </div>
                <p>{{ review.reason || "已接受示例补丁。" }}</p>
                <div class="history-record-meta">
                  <span>{{ review.createdAt }}</span
                  ><span>示例记录 · 未写入数据库</span>
                </div>
                <button class="text-button" @click="page = 'review'">
                  查看补丁<AppIcon name="arrow" />
                </button>
              </div>
            </section>
            <div class="workspace-caption">
              <span class="caption-line" /><span
                >复现有记录 · 修改有依据 · 结果由你确认</span
              ><span class="caption-line" />
            </div>
          </div>

          <aside class="context-column" aria-label="任务配置与记录">
            <section class="budget-panel">
              <div class="side-panel-heading">
                <h2>执行预算</h2>
                <span class="badge neutral">默认</span>
              </div>
              <p class="side-panel-description">达到任一上限，停止自动修复。</p>
              <div class="budget-list">
                <div
                  v-for="budget in budgets"
                  :key="budget.label"
                  class="budget-item"
                >
                  <div>
                    <span>{{ budget.label }}</span
                    ><small>{{ budget.caption }}</small>
                  </div>
                  <strong class="mono"
                    >{{ budget.value }}<small>{{ budget.unit }}</small></strong
                  >
                </div>
              </div>
              <div class="budget-footer">
                <span class="status-dot mint" />人工审核等待时间不计入
              </div>
            </section>
            <section class="reproduction-card">
              <div class="document-label">
                <AppIcon name="file" /><span>复现工作文档</span>
              </div>
              <h2>先确认问题，<br />再开始修复。</h2>
              <p>原始报告、补全步骤、实际输出和结论，记录在同一份文档中。</p>
              <div class="document-path">
                <span class="mono">work/reproduction.md</span
                ><AppIcon name="file" />
              </div>
              <div class="document-note">
                <span class="status-dot" />待真实任务生成
              </div>
            </section>
            <div class="review-reminder">
              <span class="small-rule" />
              <h3>最后一步，留给你。</h3>
              <p>测试通过后仍需人工确认。拒绝时填写理由，保留完整审核记录。</p>
            </div>
          </aside>
        </div>
        <footer class="workspace-footer">
          <span>CODE REPAIR AGENT</span
          ><span>本地界面预览 · 输入仅保存在当前页面</span>
        </footer>
      </main>
    </div>
    <div class="visually-hidden" role="status" aria-live="polite">
      {{ announcement }}
    </div>
  </div>
</template>
