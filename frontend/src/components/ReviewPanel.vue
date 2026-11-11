<script setup lang="ts">
import { computed, ref } from "vue";
import type { RepairTask, ReviewRecord } from "../types";
import AppIcon from "./AppIcon.vue";
const props = defineProps<{ task: RepairTask; busy: boolean }>();
const emit = defineEmits<{ reviewed: [decision: ReviewRecord["decision"], reason: string] }>();
const activeTab = ref<"patch" | "evidence" | "tests">("patch");
const rejecting = ref(false);
const reason = ref("");
const error = ref("");
const canReview = computed(() => !props.busy && !props.task.review && props.task.state.status === "reviewable"
  && props.task.state.validation?.passed === true && props.task.state.validation.patch_hash === props.task.state.patch_hash);
const patchLines = computed(() => (props.task.state.patch_diff ?? "").split("\n").map((text, index) => ({
  number: index + 1, text, kind: text.startsWith("+") ? "added" : text.startsWith("-") ? "removed" : "context",
})));
function reject() {
  if (!reason.value.trim()) { error.value = "请说明拒绝原因。"; return; }
  error.value = "";
  emit("reviewed", "rejected", reason.value.trim());
}
function downloadPatch() {
  const url = URL.createObjectURL(new Blob([props.task.state.patch_diff ?? ""], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = `${props.task.state.task_id}.patch`; link.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <section class="review-panel">
    <div class="review-intro">
      <div class="review-symbol"><AppIcon name="branch" /></div>
      <div><span class="eyebrow">人工审核</span><h2>每一处修改，由你确认。</h2><p>核对修复依据与验收结果，再决定是否接受补丁。</p></div>
      <span class="badge" :class="task.state.validation?.passed ? 'neutral' : 'amber'">{{ task.state.validation?.passed ? '验证已通过' : '尚未通过验证' }}</span>
    </div>
    <div class="review-tabs" role="group" aria-label="审核内容">
      <button :class="{ active: activeTab === 'patch' }" :aria-pressed="activeTab === 'patch'" @click="activeTab = 'patch'">补丁差异</button>
      <button :class="{ active: activeTab === 'evidence' }" :aria-pressed="activeTab === 'evidence'" @click="activeTab = 'evidence'">根因与复现</button>
      <button :class="{ active: activeTab === 'tests' }" :aria-pressed="activeTab === 'tests'" @click="activeTab = 'tests'">验证结果</button>
    </div>
    <div v-if="activeTab === 'patch'" class="diff-view">
      <div class="diff-heading"><span><AppIcon name="file" /><span class="mono">候选 unified diff</span></span><button v-if="task.state.patch_diff" class="text-button" @click="downloadPatch">下载补丁</button></div>
      <div v-if="task.state.patch_diff" class="diff-lines" aria-label="实际补丁差异">
        <div v-for="line in patchLines" :key="line.number" class="diff-line" :class="line.kind"><span>{{ line.number }}</span><code>{{ line.text }}</code></div>
      </div>
      <p v-else class="diff-footnote">尚未生成补丁。执行状态和停止原因显示在上方。</p>
      <div class="diff-footnote mono">SHA-256：{{ task.state.patch_hash || '未生成' }}<br />工作区：{{ task.config.workspace }}</div>
      <div class="diff-footnote">接受补丁会记录审核并保存案例；原始代码需自行检查并应用下载的补丁。</div>
    </div>
    <div v-else-if="activeTab === 'evidence'" class="evidence-view">
      <span class="eyebrow">根因诊断</span><p class="preserve-text">{{ task.state.diagnosis || '尚未生成诊断。' }}</p>
      <section v-if="task.state.case_matches?.length" class="case-match-list" aria-label="历史修复案例适用性判断">
        <span class="eyebrow">历史修复经验</span>
        <article v-for="item in task.state.case_matches" :key="`${item.case_id}@${item.revision}`" class="case-match">
          <div class="case-match-heading">
            <strong class="mono">{{ item.case_id }}@{{ item.revision }}</strong>
            <span class="badge" :class="item.decision === 'applicable' ? 'green' : item.decision === 'rejected' ? 'red' : 'amber'">
              {{ item.decision === 'applicable' ? '适用' : item.decision === 'rejected' ? '不适用' : item.decision === 'inconclusive' ? '无法判断' : '待判断' }}
            </span>
            <span class="mono case-score">{{ Math.round(item.score * 100) }}%</span>
          </div>
          <p class="preserve-text">{{ item.document.original_report }}</p>
          <p class="preserve-text">{{ item.document.diagnosis }}</p>
          <dl v-if="item.decision !== 'pending'" class="case-assessment">
            <template v-if="item.matched_conditions.length"><dt>匹配条件</dt><dd>{{ item.matched_conditions.join('；') }}</dd></template>
            <template v-if="item.conflicts.length"><dt>冲突</dt><dd>{{ item.conflicts.join('；') }}</dd></template>
            <template v-if="item.unknowns.length"><dt>未知项</dt><dd>{{ item.unknowns.join('；') }}</dd></template>
          </dl>
        </article>
      </section>
      <div v-for="(item, index) in task.state.code_evidence" :key="index" class="code-evidence">
        <h3 class="mono">{{ item.path }}:{{ item.start_line }}–{{ item.end_line }}</h3><p>{{ item.reason }}</p><pre class="log-box mono">{{ item.quote }}</pre>
      </div>
      <h3>{{ task.state.reproduction?.status || '尚未复现' }}</h3>
      <p class="preserve-text">{{ task.state.reproduction?.conclusion }}</p>
      <p class="preserve-text">{{ task.state.reproduction?.steps }}</p>
      <pre v-if="task.state.reproduction?.execution" class="log-box mono">{{ task.state.reproduction.execution.stdout }}{{ task.state.reproduction.execution.stderr }}</pre>
    </div>
    <div v-else class="evidence-view">
      <span class="eyebrow">真实 pytest 结果</span>
      <template v-if="task.state.validation">
        <h3>{{ task.state.validation.summary }}</h3>
        <p>退出码：{{ task.state.validation.return_code ?? '无' }} · 超时：{{ task.state.validation.timed_out ? '是' : '否' }}</p>
        <p class="mono preserve-text">受测补丁：{{ task.state.validation.patch_hash }}</p>
        <p class="mono preserve-text">报告：{{ task.state.validation.report_path }}</p>
        <pre class="log-box mono">{{ task.state.validation.stdout || '（无标准输出）' }}</pre>
        <pre v-if="task.state.validation.stderr" class="log-box mono">{{ task.state.validation.stderr }}</pre>
        <template v-if="task.state.validation.regression">
          <h3>缺陷回归用例</h3>
          <p class="mono preserve-text">用例哈希：{{ task.state.validation.regression.test_hash }}</p>
          <pre class="log-box mono">{{ task.state.validation.regression.test_code }}</pre>
          <h3>同一用例在基线上的结果</h3>
          <pre class="log-box mono">{{ task.state.validation.regression.baseline.stdout }}{{ task.state.validation.regression.baseline.stderr }}</pre>
          <template v-if="task.state.validation.regression.patched">
            <h3>同一用例在补丁上的结果</h3>
            <pre class="log-box mono">{{ task.state.validation.regression.patched.stdout }}{{ task.state.validation.regression.patched.stderr }}</pre>
          </template>
        </template>
        <template v-if="task.state.validation.reproduction">
          <h3>补丁后的原复现脚本</h3>
          <pre class="log-box mono">{{ task.state.validation.reproduction.stdout }}{{ task.state.validation.reproduction.stderr }}</pre>
        </template>
      </template>
      <p v-else class="test-placeholder"><span class="status-dot" />尚无实际测试结果</p>
    </div>
    <div v-if="task.review" class="review-outcome" :class="task.review.decision" role="status">
      <AppIcon :name="task.review.decision === 'accepted' ? 'check' : 'close'" />
      <div><strong>{{ task.review.decision === 'accepted' ? '已接受补丁' : '已拒绝补丁' }}</strong>
        <p>{{ task.review.reason || '人工审核决定已保存。' }}</p>
        <small>已写入数据库 · {{ new Date(task.review.created_at).toLocaleString('zh-CN') }}</small>
        <p v-if="task.review.decision === 'accepted'">{{ task.state.memory_saved ? '修复案例已保存到知识库。' : '案例保存状态请查看上方流程结果。' }}</p>
      </div>
    </div>
    <form v-else-if="rejecting" class="rejection-form" @submit.prevent="reject">
      <label for="review-reason">拒绝理由（必填）</label>
      <textarea id="review-reason" v-model="reason" rows="3" maxlength="2000" placeholder="说明遗漏场景、行为变化或修改范围问题。" />
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <div class="review-actions"><button type="button" class="button subtle" :disabled="busy" @click="rejecting = false">取消</button><button class="button danger" type="submit" :disabled="!canReview">{{ busy ? '提交中…' : '确认拒绝' }}</button></div>
    </form>
    <div v-else class="review-actions review-action-bar">
      <span class="muted">{{ canReview ? '通过验证后，由你决定。' : '通过独立测试并结束执行后才可审核。' }}</span>
      <button class="button subtle" :disabled="!canReview" @click="rejecting = true"><AppIcon name="close" />拒绝补丁</button>
      <button class="button primary" :disabled="!canReview" @click="emit('reviewed', 'accepted', '')"><AppIcon name="check" />接受补丁</button>
    </div>
  </section>
</template>
