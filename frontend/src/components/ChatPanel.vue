<script setup lang="ts">
import { onMounted, ref } from "vue";
import { createConversation, decideCandidate, getConversation, getMemories, listConversations, sendChat, type Conversation, type MemoryCandidate, type PersonalMemory } from "../api";

const conversations = ref<Array<Pick<Conversation, "id" | "title" | "summary">>>([]);
const current = ref<Conversation | null>(null);
const memories = ref<PersonalMemory[]>([]);
const content = ref("");
const busy = ref(false);
const error = ref("");
const uncertain = ref<{ id: string; content: string; requestId: string } | null>(null);
async function refreshList() { conversations.value = (await listConversations()).conversations; }
async function load(id: string) { if (busy.value) return; busy.value = true; error.value = ""; try { current.value = await getConversation(id); memories.value = (await getMemories()).memories; } catch (e) { error.value = e instanceof Error ? e.message : "读取对话失败"; } finally { busy.value = false; } }
async function create() { if (busy.value) return; busy.value = true; error.value = ""; try { current.value = await createConversation(); await refreshList(); } catch (e) { error.value = e instanceof Error ? e.message : "创建对话失败"; } finally { busy.value = false; } }
async function send(retry = false) {
  if (busy.value || !current.value) return;
  const pending = current.value.messages.find(message => message.role === "user" && message.status !== "completed");
  const message = retry ? (pending?.content || uncertain.value?.content) : content.value.trim();
  if (!message || (!retry && pending)) return;
  const id = current.value.id;
  const requestId = retry ? (pending?.request_id || uncertain.value?.requestId || "") : crypto.randomUUID().replaceAll("-", "");
  busy.value = true; error.value = "";
  try { current.value = await sendChat(id, message, requestId); uncertain.value = null; content.value = ""; await refreshList(); }
  catch (e) {
    error.value = e instanceof Error ? e.message : "发送失败";
    uncertain.value = { id, content: message, requestId };
    try { current.value = await getConversation(id); if (current.value.messages.some(item => item.request_id === requestId)) { content.value = ""; uncertain.value = null; } } catch { /* Keep the request identity when status cannot be checked. */ }
  } finally { busy.value = false; }
}
async function decide(candidate: MemoryCandidate, accept: boolean) {
  if (busy.value || !current.value) return;
  busy.value = true; error.value = "";
  try { current.value = await decideCandidate(current.value.id, candidate, accept); memories.value = (await getMemories()).memories; if (accept) window.dispatchEvent(new Event("repair:memories-changed")); }
  catch (e) { error.value = e instanceof Error ? e.message : "处理候选失败"; }
  finally { busy.value = false; }
}
onMounted(async () => { try { await refreshList(); memories.value = (await getMemories()).memories; } catch (e) { error.value = e instanceof Error ? e.message : "读取对话失败"; } });
</script>

<template>
  <section class="panel memory-panel" aria-labelledby="chat-title">
    <div class="panel-heading"><div><span class="panel-dot" /><h2 id="chat-title">对话与记忆候选</h2></div><button class="text-button" :disabled="busy" @click="create">新建对话</button></div>
    <div class="memory-body">
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <label class="field">选择对话 <select :value="current?.id || ''" :disabled="busy" @change="load(($event.target as HTMLSelectElement).value)"><option value="" disabled>选择已有对话</option><option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
      <template v-if="current">
        <div v-if="current.summary"><strong>对话摘要</strong><p class="chat-text">{{ current.summary }}</p></div>
        <ol class="chat-messages"><li v-for="message in current.messages" :key="message.id"><strong>{{ message.role === 'user' ? '你' : '助手' }}</strong><p class="chat-text">{{ message.content }}</p><small v-if="message.status !== 'completed'" class="muted">回复未完成</small></li></ol>
        <button v-if="uncertain?.id === current.id || current.messages.some(m => m.role === 'user' && m.status !== 'completed')" class="button subtle" :disabled="busy" @click="send(true)">重试未完成的回复</button>
        <form class="account-form" @submit.prevent="send(false)"><label class="field"><span>消息</span><textarea v-model="content" maxlength="4000" rows="3" :disabled="busy" /></label><button class="button primary" :disabled="busy || !content.trim() || uncertain?.id === current.id || current.messages.some(m => m.role === 'user' && m.status !== 'completed')">{{ busy ? '处理中…' : '发送' }}</button></form>
        <div v-if="current.candidates.length"><h3>待确认的记忆</h3><p class="muted">核对已有记忆后再保存，候选不会自动覆盖已有内容。</p><ul class="memory-list"><li v-for="candidate in current.candidates" :key="candidate.id"><div class="candidate-edit"><select v-model="candidate.kind"><option value="preference">偏好</option><option value="coding_rule">编码规范</option></select><textarea v-model="candidate.content" maxlength="2000" rows="3" /><small class="muted">提取于 {{ new Date(candidate.extracted_at * 1000).toLocaleString() }}</small></div><div class="memory-actions"><button class="text-button" :disabled="busy || !candidate.content.trim()" @click="decide(candidate, true)">确认保存</button><button class="text-button" :disabled="busy" @click="decide(candidate, false)">拒绝</button></div></li></ul><p v-for="memory in memories" :key="memory.id" class="chat-text muted">已有{{ memory.kind === 'preference' ? '偏好' : '编码规范' }}：{{ memory.content }}</p></div>
      </template>
    </div>
  </section>
</template>
