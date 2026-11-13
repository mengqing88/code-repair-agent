<script setup lang="ts">
import { ref } from "vue";
import { authenticate, type Session } from "../api";

const emit = defineEmits<{ signedIn: [session: Session] }>();
const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
async function submit() {
  if (busy.value) return;
  busy.value = true; error.value = "";
  try {
    const session = await authenticate(mode.value, username.value, password.value);
    password.value = "";
    emit("signedIn", session);
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "登录失败，请重试"; }
  finally { busy.value = false; }
}
</script>

<template>
  <section class="panel account-gate">
    <span class="eyebrow">CODE REPAIR AGENT</span>
    <h1>{{ mode === 'login' ? '登录修复工作台' : '创建你的账号' }}</h1>
    <p class="account-intro">任务、修复经验和个人长期记忆，按账号独立保存。</p>
    <form class="account-form" @submit.prevent="submit">
      <label class="field"><span>用户名</span><input v-model="username" name="username" autocomplete="username" required pattern="[a-zA-Z0-9_]{3,32}" minlength="3" maxlength="32" :disabled="busy" placeholder="3–32 位字母、数字或下划线" /></label>
      <label class="field"><span>密码</span><input v-model="password" name="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" required :minlength="mode === 'register' ? 10 : 1" maxlength="128" :disabled="busy" placeholder="注册密码至少 10 位" /></label>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <button class="button primary" :disabled="busy" type="submit">{{ busy ? '正在验证…' : mode === 'login' ? '登录' : '注册并登录' }}</button>
      <button class="text-button" :disabled="busy" type="button" @click="mode = mode === 'login' ? 'register' : 'login'; error = ''; password = ''">{{ mode === 'login' ? '还没有账号？创建账号' : '已有账号？返回登录' }}</button>
    </form>
  </section>
</template>
