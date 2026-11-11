<script setup lang="ts">
import { computed, ref } from "vue";
import type { SourceType, TaskDraft } from "../types";
import AppIcon from "./AppIcon.vue";

// draft：父组件提供的表单草稿；emit：通知父组件确认已校验的输入。
const draft = defineModel<TaskDraft>({ required: true });
const emit = defineEmits<{ submit: [] }>();
const error = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const reading = ref(false);
const sourceOptions: {
  value: SourceType;
  label: string;
  icon: "file" | "branch" | "code";
}[] = [
  { value: "python_file", label: "代码文件", icon: "file" },
  { value: "git_repository", label: "Git 仓库", icon: "branch" },
  { value: "pasted_code", label: "粘贴代码", icon: "code" },
];
const isRepository = computed(
  () => draft.value.sourceType === "git_repository",
);

/** 切换代码来源，保留已填写内容并清除上一种来源的校验提示。 */
function selectSource(source: SourceType) {
  draft.value.sourceType = source;
  error.value = "";
}

/** 读取用户主动选择的文本文件；不限制 .py 后缀，不访问其他本地文件。 */
async function readSelectedFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  error.value = "";
  if (file.size > 1_000_000) {
    error.value = "请选择小于 1 MB 的代码文本文件。";
    return;
  }
  reading.value = true;
  try {
    const text = await file.text();
    if (!text.trim()) throw new Error("文件内容为空，请重新选择。");
    draft.value.fileName = file.name;
    draft.value.fileContent = text;
    draft.value.path = file.name;
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "文件读取失败，请重新选择。";
  } finally {
    reading.value = false;
    (event.target as HTMLInputElement).value = "";
  }
}

/** 检查必需输入，确认后由父组件提交给本地 Python 服务。 */
function submit() {
  if (
    draft.value.sourceType === "pasted_code" &&
    !draft.value.codeText.trim()
  ) {
    error.value = "请先粘贴需要修复的 Python 代码。";
  } else if (
    draft.value.sourceType !== "pasted_code" &&
    !draft.value.path.trim()
  ) {
    error.value = isRepository.value
      ? "请填写仓库路径。"
      : "请填写代码文件路径或选择文件。";
  } else if (
    isRepository.value &&
    !/^(?:[a-f\d]{40}|[a-f\d]{64})$/i.test(draft.value.revision.trim())
  ) {
    error.value = "请提供完整的 40 或 64 位十六进制 commit ID。";
  } else if (!draft.value.errorReport.trim()) {
    error.value = "请填写错误报告，以便核对实际问题。";
  } else {
    error.value = "";
    emit("submit");
  }
}
</script>

<template>
  <form class="composer" @submit.prevent="submit">
    <div class="section-heading">
      <div>
        <span class="section-number">01</span>
        <h2>代码来源</h2>
      </div>
      <span class="field-note">Python 代码 · 扩展名不限</span>
    </div>
    <div class="source-options" role="group" aria-label="代码来源">
      <button
        v-for="option in sourceOptions"
        :key="option.value"
        type="button"
        :class="{ selected: draft.sourceType === option.value }"
        :aria-pressed="draft.sourceType === option.value"
        @click="selectSource(option.value)"
      >
        <AppIcon :name="option.icon" />{{ option.label }}
      </button>
    </div>

    <div v-if="draft.sourceType === 'pasted_code'" class="field">
      <label for="code-text">Python 代码 <span class="required">*</span></label>
      <textarea
        id="code-text"
        v-model="draft.codeText"
        class="code-input"
        rows="7"
        placeholder="# 粘贴需要修复的 Python 代码"
        spellcheck="false"
      />
    </div>
    <template v-else>
      <div class="field">
        <label for="source-path"
          >{{ isRepository ? "本地仓库路径" : "代码文件路径" }}
          <span class="required">*</span></label
        >
        <div class="file-path-row">
          <input
            id="source-path"
            v-model="draft.path"
            class="mono"
            :placeholder="
              isRepository
                ? 'F:\\projects\\my-repository'
                : 'F:\\projects\\example.py'
            "
            autocomplete="off"
            @input="
              draft.fileContent = null;
              draft.fileName = '';
            "
          />
          <button
            v-if="!isRepository"
            type="button"
            class="button subtle"
            :disabled="reading"
            @click="fileInput?.click()"
          >
            <AppIcon name="upload" />{{ reading ? "读取中…" : "选择文件" }}
          </button>
        </div>
        <input
          ref="fileInput"
          type="file"
          class="visually-hidden"
          tabindex="-1"
          aria-label="选择 Python 代码文本文件"
          @change="readSelectedFile"
        />
        <p class="input-hint">
          {{
            draft.fileContent !== null && !isRepository
              ? `已读取 ${draft.fileName}，共 ${draft.fileContent.split("\n").length} 行`
              : "文件路径由本地修复服务使用。"
          }}
        </p>
      </div>
      <div v-if="isRepository" class="field">
        <label for="revision"
          >固定版本 commit ID <span class="required">*</span></label
        >
        <input
          id="revision"
          v-model="draft.revision"
          class="mono"
          placeholder="完整的 40 或 64 位 commit ID"
          autocomplete="off"
        />
      </div>
    </template>

    <div class="section-divider" />
    <div class="section-heading">
      <div>
        <span class="section-number">02</span>
        <h2>错误报告</h2>
      </div>
      <span class="field-note">必填</span>
    </div>
    <div class="field">
      <label for="error-report" class="visually-hidden">错误报告</label>
      <textarea
        id="error-report"
        v-model="draft.errorReport"
        rows="5"
        :placeholder="'描述预期行为、实际结果和触发步骤。\n如果有报错堆栈，也可以直接粘贴在这里。'"
      />
      <p class="input-hint">
        报告不完整时先补全复现文档；发现问题不一致时向你确认。
      </p>
    </div>
    <details class="test-settings">
      <summary>
        <AppIcon name="chevron" /><span>验收测试</span
        ><span class="mono">{{ draft.testTarget || "pytest 自动发现" }}</span>
      </summary>
      <div class="field">
        <label for="test-target">pytest 测试目标</label
        ><input
          id="test-target"
          v-model="draft.testTarget"
          class="mono"
          placeholder="例如 tests/test_example.py；留空使用 ."
        />
      </div>
    </details>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
    <div class="composer-footer">
      <span class="muted">确认输入后开始执行修复流程</span>
      <button class="button primary" type="submit" :disabled="reading">
        确认修复任务<AppIcon name="arrow" />
      </button>
    </div>
  </form>
</template>
