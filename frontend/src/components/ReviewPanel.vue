<script setup lang="ts">
import { computed, ref } from "vue";
import type { PreviewReview } from "../types";
import AppIcon from "./AppIcon.vue";

// review：父组件保存的当前预览审核结果，切换页面时仍可查看。
const props = defineProps<{ review: PreviewReview | null }>();
const emit = defineEmits<{
  reviewed: [decision: PreviewReview["decision"], reason: string];
  reset: [];
}>();
const activeTab = ref<"patch" | "evidence" | "tests">("patch");
const rejecting = ref(false);
const reason = ref("");
const error = ref("");
const lineCount = computed(() => reason.value.trim().length);

// patchLines：专门用于展示审阅布局的示例；并非真实模型或测试输出。
const patchLines = [
  { number: "1", kind: "context", text: "def average(values):" },
  {
    number: "",
    kind: "removed",
    text: "-    return sum(values) / len(values)",
  },
  { number: "2", kind: "added", text: "+    if not values:" },
  { number: "3", kind: "added", text: "+        return 0.0" },
  { number: "4", kind: "added", text: "+    return sum(values) / len(values)" },
];

/** 保存本页预览的拒绝理由；空白理由必须补充，不能当作有效审核。 */
function reject() {
  if (!lineCount.value) {
    error.value = "请说明拒绝原因，例如遗漏的场景、行为变化或修改范围问题。";
    return;
  }
  error.value = "";
  emit("reviewed", "rejected", reason.value.trim());
}

/** 重置审核示例，方便分别体验接受和拒绝两种操作。 */
function resetReview() {
  rejecting.value = false;
  reason.value = "";
  error.value = "";
  emit("reset");
}
</script>

<template>
  <section class="review-panel">
    <div class="review-intro">
      <div class="review-symbol"><AppIcon name="branch" /></div>
      <div>
        <span class="eyebrow">人工审核</span>
        <h2>每一处修改，由你确认。</h2>
        <p>核对修复依据与验收结果，再决定是否接受补丁。</p>
      </div>
      <span class="badge amber">示例数据</span>
    </div>
    <div class="review-tabs" role="group" aria-label="审核内容">
      <button
        :class="{ active: activeTab === 'patch' }"
        :aria-pressed="activeTab === 'patch'"
        @click="activeTab = 'patch'"
      >
        补丁差异 <span class="mono">1</span>
      </button>
      <button
        :class="{ active: activeTab === 'evidence' }"
        :aria-pressed="activeTab === 'evidence'"
        @click="activeTab = 'evidence'"
      >
        根因与复现
      </button>
      <button
        :class="{ active: activeTab === 'tests' }"
        :aria-pressed="activeTab === 'tests'"
        @click="activeTab = 'tests'"
      >
        验证结果
      </button>
    </div>
    <div v-if="activeTab === 'patch'" class="diff-view">
      <div class="diff-heading">
        <span><AppIcon name="file" /><span class="mono">example.py</span></span
        ><span class="diff-count"><b>+3</b> <i>−1</i></span>
      </div>
      <div class="diff-lines" aria-label="示例补丁差异">
        <div class="diff-hunk">@@ -1,2 +1,4 @@</div>
        <div
          v-for="(line, index) in patchLines"
          :key="index"
          class="diff-line"
          :class="line.kind"
        >
          <span>{{ line.number }}</span
          ><code>{{ line.text }}</code>
        </div>
      </div>
      <div class="diff-footnote">
        示例约定空输入返回 0.0；真实任务需按错误报告确认预期行为。
      </div>
    </div>
    <div v-else-if="activeTab === 'evidence'" class="evidence-view">
      <span class="eyebrow">示例复现记录</span>
      <h3>空列表触发除零异常</h3>
      <p>
        调用 <code>average([])</code> 时，分母 <code>len(values)</code> 为
        0。候选补丁在计算前增加空列表分支。
      </p>
      <div class="log-box mono">
        输入：average([])<br />实际：ZeroDivisionError<br />预期：返回
        0.0（示例约定）
      </div>
    </div>
    <div v-else class="evidence-view">
      <span class="eyebrow">验证结果布局</span>
      <h3>真实测试结果将在这里展示</h3>
      <p>
        包含退出码、测试输出、耗时，以及对应补丁的哈希。当前示例未执行 pytest。
      </p>
      <div class="test-placeholder">
        <span class="status-dot" />尚无实际测试结果
      </div>
    </div>

    <div
      v-if="props.review"
      class="review-outcome"
      :class="props.review.decision"
      role="status"
    >
      <AppIcon
        :name="props.review.decision === 'accepted' ? 'check' : 'close'"
      />
      <div>
        <strong>{{
          props.review.decision === "accepted"
            ? "已预览接受补丁"
            : "已预览拒绝补丁"
        }}</strong>
        <p>{{ props.review.reason || "接受操作已在本页记录。" }}</p>
        <small>仅本次预览保留，尚未写入数据库。</small
        ><button class="text-button reset-review" @click="resetReview">
          重新体验审核<AppIcon name="arrow" />
        </button>
      </div>
    </div>
    <form v-else-if="rejecting" class="rejection-form" @submit.prevent="reject">
      <div class="section-heading">
        <div><h3>为什么拒绝这个补丁？</h3></div>
        <span class="field-note">必填</span>
      </div>
      <label for="review-reason" class="visually-hidden">拒绝理由</label>
      <textarea
        id="review-reason"
        v-model="reason"
        rows="3"
        maxlength="2000"
        placeholder="例如：应该抛出明确异常，而不是返回默认值。"
        autofocus
      />
      <div class="reason-note">
        <span>理由将与补丁、诊断和验证结果一起记录。</span
        ><span class="mono">{{ lineCount }} / 2000</span>
      </div>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <div class="review-actions">
        <button
          type="button"
          class="button subtle"
          @click="
            rejecting = false;
            error = '';
          "
        >
          取消</button
        ><button class="button danger" type="submit">
          预览拒绝记录<AppIcon name="arrow" />
        </button>
      </div>
    </form>
    <div v-else class="review-actions review-action-bar">
      <span class="muted">当前为审阅交互示例</span
      ><button class="button subtle" @click="rejecting = true">
        <AppIcon name="close" />拒绝补丁</button
      ><button class="button primary" @click="emit('reviewed', 'accepted', '')">
        <AppIcon name="check" />接受补丁
      </button>
    </div>
  </section>
</template>
