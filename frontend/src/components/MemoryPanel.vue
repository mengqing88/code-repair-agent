<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { getMemories, memoryStats, saveMemory, removeMemory, getKnowledgeCandidates, reviewKnowledgeCandidate,
  type PersonalMemory, type KnowledgeCandidate } from "../api";

const memories = ref<PersonalMemory[]>([]);
const total = ref<number | null>(null);
const kind = ref<PersonalMemory["kind"]>("preference");
const content = ref("");
const editing = ref<string>();
const busy = ref(false);
const error = ref("");
const candidates = ref<KnowledgeCandidate[]>([]);
const reviewReasons = ref<Record<string, string>>({});
let active = true;
onUnmounted(() => { active = false; window.removeEventListener("repair:memories-changed", load); });
function reset() { editing.value = undefined; content.value = ""; kind.value = "preference"; }
async function load() {
  busy.value = true; error.value = "";
  try {
    const [notes, stats, uncertain, pending, reviewing, approved] = await Promise.all([
      getMemories(), memoryStats(), getKnowledgeCandidates(), getKnowledgeCandidates("pending"),
      getKnowledgeCandidates("reviewing"), getKnowledgeCandidates("approved"),
    ]);
    if (active) { memories.value = notes.memories; total.value = stats.total;
      candidates.value = [...uncertain.candidates, ...pending.candidates, ...reviewing.candidates, ...approved.candidates]; }
  } catch (cause) { if (active) error.value = cause instanceof Error ? cause.message : "读取记忆失败"; }
  finally { if (active) busy.value = false; }
}
async function save() {
  if (busy.value || !content.value.trim()) return;
  busy.value = true; error.value = "";
  try {
    const result = await saveMemory(kind.value, content.value, editing.value);
    if (active) { memories.value = result.memories; reset(); }
  } catch (cause) { if (active) error.value = cause instanceof Error ? cause.message : "保存失败"; }
  finally { if (active) busy.value = false; }
}
async function remove(id: string) {
  if (busy.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await removeMemory(id);
    if (active) { memories.value = result.memories; if (editing.value === id) reset(); }
  } catch (cause) { if (active) error.value = cause instanceof Error ? cause.message : "删除失败"; }
  finally { if (active) busy.value = false; }
}
function edit(note: PersonalMemory) { editing.value = note.id; kind.value = note.kind; content.value = note.content; }
async function reviewCandidate(candidate: KnowledgeCandidate, decision: "approve" | "decline") {
  const reason = reviewReasons.value[candidate.id]?.trim();
  if (busy.value || !reason) return;
  busy.value = true; error.value = "";
  try {
    await reviewKnowledgeCandidate(candidate, decision, reason);
    if (active) { delete reviewReasons.value[candidate.id]; await load(); }
  } catch (cause) { if (active) error.value = cause instanceof Error ? cause.message : "审核未完成，请刷新核对状态"; }
  finally { if (active) busy.value = false; }
}
onMounted(() => { window.addEventListener("repair:memories-changed", load); void load(); });
</script>

<template>
  <section class="panel memory-panel" aria-labelledby="memory-title">
    <div class="panel-heading"><div><span class="panel-dot" /><h2 id="memory-title">我的长期记忆</h2></div><span class="muted">修复经验 {{ total === null ? '—' : total }} 条 · 个人记忆 {{ memories.length }}/20</span></div>
    <div class="memory-body">
      <p class="preview-notice">本项目任务通过验证且你接受补丁后保存成功经验；拒绝补丁保留失败经验。外部导入先由经验审核员核验，无法确定的内容交给你审核。</p>
      <p v-if="error" class="error-message" role="alert">{{ error }} <button class="text-button" :disabled="busy" @click="load">重新读取</button></p>
      <p v-if="!memories.length && !busy" class="muted">还没有个人记忆，可以添加常用语言、代码风格或编码规范。</p>
      <ul v-if="memories.length" class="memory-list">
        <li v-for="note in memories" :key="note.id">
          <div><span class="badge neutral">{{ note.kind === 'preference' ? '偏好' : '编码规范' }}</span><p>{{ note.content }}</p><small v-if="note.source_conversation" class="muted">来源对话 {{ note.source_conversation }} · 提取于 {{ new Date((note.extracted_at || 0) * 1000).toLocaleString() }}</small></div>
          <div class="memory-actions"><button class="text-button" :disabled="busy" @click="edit(note)">编辑</button><button class="text-button" :disabled="busy" @click="remove(note.id)">删除</button></div>
        </li>
      </ul>
      <form class="account-form" @submit.prevent="save">
        <fieldset :disabled="busy" class="memory-kinds"><legend>{{ editing ? '编辑记忆' : '添加记忆' }}</legend><label><input v-model="kind" type="radio" value="preference" /> 个人偏好</label><label><input v-model="kind" type="radio" value="coding_rule" /> 编码规范</label></fieldset>
        <label class="field"><span>内容 <span class="muted">最多 2,000 字</span></span><textarea v-model="content" required maxlength="2000" rows="3" :disabled="busy" placeholder="例如：优先使用类型注解，错误提示使用中文。" /></label>
        <div class="memory-actions"><button class="button primary" type="submit" :disabled="busy || !content.trim()">{{ busy ? '处理中…' : '保存记忆' }}</button><button v-if="editing" class="button subtle" type="button" :disabled="busy" @click="reset">取消编辑</button></div>
      </form>
      <div aria-labelledby="knowledge-review-title">
        <h3 id="knowledge-review-title">修复经验待审核 · {{ candidates.length }}</h3>
        <p class="muted">核对来源、验收材料和适用条件后再批准。拒绝补丁的记录只作为失败经验发布。</p>
        <button class="text-button" :disabled="busy" @click="load">刷新审核列表</button>
        <p v-if="!candidates.length && !busy" class="muted">没有需要人工处理的修复经验。</p>
        <article v-for="candidate in candidates" :key="candidate.id" class="account-form">
          <strong>{{ candidate.status === 'approved' ? '已批准，发布待完成' : candidate.status === 'reviewing' ? '经验审核员处理中' : '等待人工核验' }}</strong>
          <span class="muted">来源：{{ candidate.source || '外部导入' }}</span>
          <p v-if="candidate.error" class="error-message">{{ candidate.error }}</p>
          <details><summary>查看完整候选资料</summary><pre class="code-block">{{ JSON.stringify(candidate.record, null, 2) }}</pre></details>
          <details><summary>查看经验审核员的判断和证据缺口</summary><pre class="code-block">{{ JSON.stringify(candidate.audit, null, 2) }}</pre></details>
          <label class="field"><span>审核理由或证据说明</span><textarea v-model="reviewReasons[candidate.id]" :disabled="busy" maxlength="2000" rows="2" placeholder="说明核对了哪些材料，或为什么拒绝收录。" /></label>
          <div class="memory-actions">
            <button class="button subtle" :disabled="busy || candidate.status === 'reviewing' || !reviewReasons[candidate.id]?.trim() || candidate.status === 'approved'" @click="reviewCandidate(candidate, 'decline')">拒绝收录</button>
            <button class="button primary" :disabled="busy || candidate.status === 'reviewing' || !reviewReasons[candidate.id]?.trim()" @click="reviewCandidate(candidate, 'approve')">{{ candidate.status === 'approved' ? '继续发布' : '批准并发布' }}</button>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>
