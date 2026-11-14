<script setup lang="ts">
import { computed, ref } from "vue";
import type { SourceType, TaskDraft } from "../types";
import AppIcon from "./AppIcon.vue";
import { fileBase64, readProjectFolder, validateArchive } from "../project-upload";

// draft：父组件提供的表单草稿；emit：通知父组件确认已校验的输入。
const draft = defineModel<TaskDraft>({ required: true });
const emit = defineEmits<{ submit: [] }>();
const error = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const archiveInput = ref<HTMLInputElement | null>(null);
const importNotice = ref("");
const reading = ref(false);
const sourceOptions: {
  value: SourceType;
  label: string;
  icon: "file" | "branch" | "code" | "upload";
}[] = [
  { value: "code_file", label: "代码文件", icon: "file" },
  { value: "git_repository", label: "Git 仓库", icon: "branch" },
  { value: "directory", label: "文件夹", icon: "file" },
  { value: "zip_archive", label: "ZIP 压缩包", icon: "upload" },
  { value: "pasted_code", label: "粘贴代码", icon: "code" },
];
const isRepository = computed(
  () => draft.value.sourceType === "git_repository",
);
const languageOptions = ["auto", "python", "javascript", "typescript", "java", "go", "rust", "c", "cpp", "csharp", "ruby", "php", "kotlin", "swift", "scala", "shell", "sql", "perl", "elixir", "erlang", "lua", "dart", "r", "objective-c", "objective-c++", "haskell", "clojure", "groovy", "xml", "html", "css", "yaml", "toml"];
const effectiveLanguage = computed(() => {
  if (draft.value.language !== "auto") return draft.value.language;
  if (isRepository.value) return "auto";
  if (draft.value.sourceType === "directory" || draft.value.sourceType === "zip_archive") return "auto";
  const name = draft.value.fileName || draft.value.path.split(/[\\/]/).pop() || "";
  const suffix = name.slice(name.lastIndexOf(".")).toLowerCase();
  return suffix === ".py" || suffix === ".pyw" ? "python" : "auto";
});

/** 根据已选语言为粘贴代码设置默认文件扩展名。 */
function updateLanguage(value: string) {
  draft.value.language = value;
  if (draft.value.sourceType === "pasted_code" && (!draft.value.fileName || draft.value.fileName.startsWith("main."))) {
    const suffix: Record<string, string> = { python: ".py", javascript: ".js", typescript: ".ts", java: ".java", go: ".go", rust: ".rs", c: ".c", cpp: ".cpp", csharp: ".cs", ruby: ".rb", php: ".php", kotlin: ".kt", swift: ".swift", scala: ".scala", shell: ".sh" };
    draft.value.fileName = `main${suffix[value] || ".txt"}`;
  }
}

/** 切换代码来源，保留已填写内容并清除上一种来源的校验提示。 */
function selectSource(source: SourceType) {
  if (draft.value.sourceType !== source) {
    draft.value.path = ""; draft.value.fileName = ""; draft.value.fileContent = null;
    draft.value.projectFiles = null; draft.value.archiveBase64 = null; importNotice.value = "";
  }
  draft.value.sourceType = source;
  error.value = "";
}

async function readFolder(event: Event) {
  const input = event.target as HTMLInputElement;
  if (!input.files?.length) return;
  clearSelectedProject(); draft.value.path = "";
  reading.value = true; error.value = "";
  try {
    const project = await readProjectFolder(Array.from(input.files));
    draft.value.projectFiles = project.files;
    draft.value.path = project.name; draft.value.fileName = project.name;
    importNotice.value = `已读取 ${project.files.length} 个文件${project.skipped ? `，排除 ${project.skipped} 项凭据、依赖或缓存` : ""}。`;
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "文件夹读取失败。"; }
  finally { reading.value = false; input.value = ""; }
}

async function readArchive(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  clearSelectedProject(); draft.value.path = "";
  error.value = ""; reading.value = true;
  try {
    validateArchive(file);
    draft.value.archiveBase64 = await fileBase64(file);
    draft.value.fileName = file.name; draft.value.path = file.name;
    importNotice.value = `已读取 ${file.name}（${(file.size / 1_000_000).toFixed(2)} MB）。`;
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "ZIP 读取失败。"; }
  finally { reading.value = false; input.value = ""; }
}

function clearSelectedProject() {
  draft.value.fileContent = null; draft.value.fileName = "";
  draft.value.projectFiles = null; draft.value.archiveBase64 = null; importNotice.value = "";
}

/** 读取用户主动选择的代码文本文件；不访问其他本地文件。 */
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
    draft.value.sourceType = "code_file";
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
  if (reading.value) return;
  const inferProject = ["directory", "zip_archive"].includes(draft.value.sourceType) && draft.value.language === "auto";
  if (
    draft.value.sourceType === "pasted_code" &&
    !draft.value.codeText.trim()
  ) {
    error.value = "请先粘贴需要修复的代码。";
  } else if (
    draft.value.sourceType !== "pasted_code" &&
    !draft.value.path.trim()
  ) {
    error.value = isRepository.value
      ? "请填写仓库路径。"
      : draft.value.sourceType === "directory" ? "请填写文件夹路径或选择文件夹。" : "请填写代码文件路径或选择文件。";
  } else if (draft.value.sourceType === "zip_archive" && !draft.value.archiveBase64) {
    error.value = "请先选择 ZIP 压缩包。";
  } else if (
    isRepository.value &&
    !/^(?:[a-f\d]{40}|[a-f\d]{64})$/i.test(draft.value.revision.trim())
  ) {
    error.value = "请提供完整的 40 或 64 位十六进制 commit ID。";
  } else if (!draft.value.errorReport.trim()) {
    error.value = "请填写错误报告，以便核对实际问题。";
  } else if (
    !inferProject && effectiveLanguage.value !== "python" &&
    (!draft.value.testCommand.trim() || !draft.value.reproduceCommand.trim())
  ) {
    error.value = "请为非 Python 或未确定语言的任务填写固定复现命令和测试命令。";
  } else if (!inferProject && !["python", "javascript", "typescript"].includes(effectiveLanguage.value) && !draft.value.regressionCommand?.trim()) {
    error.value = "请填写独立回归命令，使用 {test_file} 和 {report_file} 输出 JUnit 报告。";
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
      <span class="field-note">支持常见编程语言</span>
    </div>
    <div class="source-options" role="group" aria-label="代码来源">
      <button
        v-for="option in sourceOptions"
        :key="option.value"
        type="button"
        :class="{ selected: draft.sourceType === option.value }"
        :aria-pressed="draft.sourceType === option.value"
        :disabled="reading"
        @click="selectSource(option.value)"
      >
        <AppIcon :name="option.icon" />{{ option.label }}
      </button>
    </div>
    <div class="field">
      <label for="language">代码语言</label>
      <select id="language" :value="draft.language" @change="updateLanguage(($event.target as HTMLSelectElement).value)">
        <option v-for="language in languageOptions" :key="language" :value="language">
          {{ language === "auto" ? "自动识别 / 混合仓库" : language }}
        </option>
      </select>
      <p class="input-hint">自动识别失败或仓库包含多种语言时，请选择主要语言，并配置执行命令。</p>
    </div>

    <div v-if="draft.sourceType === 'pasted_code'" class="field">
      <label for="code-text">代码 <span class="required">*</span></label>
      <textarea
        id="code-text"
        v-model="draft.codeText"
        class="code-input"
        rows="7"
        placeholder="粘贴需要修复的代码"
        spellcheck="false"
      />
      <label for="code-name">文件名</label>
      <input id="code-name" v-model="draft.fileName" class="mono" placeholder="main.py" />
    </div>
    <div v-else-if="draft.sourceType === 'zip_archive'" class="field">
      <label>项目 ZIP 压缩包 <span class="required">*</span></label>
      <button type="button" class="button subtle" :disabled="reading" @click="archiveInput?.click()">
        <AppIcon name="upload" />{{ reading ? "读取中…" : "选择 ZIP 压缩包" }}
      </button>
      <input ref="archiveInput" type="file" accept=".zip,application/zip" class="visually-hidden" tabindex="-1" aria-label="选择 ZIP 压缩包" @change="readArchive" />
      <p class="input-hint">{{ importNotice || "ZIP 最大 100 MB；导入后最多 1000 个文件、总共 500 MB，单文件最多 20 MB。" }}</p>
      <p class="input-hint">仅有一个外层文件夹时自动以该文件夹为项目根目录。凭据、依赖和缓存路径会排除；不支持加密 ZIP。</p>
    </div>
    <template v-else>
      <div class="field">
        <label for="source-path"
          >{{ isRepository ? "本地仓库路径" : draft.sourceType === 'directory' ? "本机文件夹路径或上传文件夹" : "代码文件路径" }}
          <span class="required">*</span></label
        >
        <div class="file-path-row">
          <input
            id="source-path"
            v-model="draft.path"
            class="mono"
            :placeholder="
              isRepository || draft.sourceType === 'directory'
                ? 'F:\\projects\\my-repository'
                : 'F:\\projects\\example.js'
            "
            autocomplete="off"
            :disabled="reading"
            @input="clearSelectedProject"
          />
          <button
            v-if="!isRepository"
            type="button"
            class="button subtle"
            :disabled="reading"
            @click="draft.sourceType === 'directory' ? folderInput?.click() : fileInput?.click()"
          >
            <AppIcon name="upload" />{{ reading ? "读取中…" : draft.sourceType === 'directory' ? "选择文件夹" : "选择文件" }}
          </button>
        </div>
        <input
          ref="fileInput"
          type="file"
          class="visually-hidden"
          tabindex="-1"
          aria-label="选择代码文本文件"
          @change="readSelectedFile"
        />
        <input ref="folderInput" type="file" webkitdirectory multiple class="visually-hidden" tabindex="-1" aria-label="选择项目文件夹" @change="readFolder" />
        <p class="input-hint">
          {{
            draft.sourceType === 'directory' ? importNotice || "本机路径由修复服务读取；也可上传整个文件夹。最多 1000 个文件、总共 500 MB，单文件最多 20 MB。" : draft.fileContent !== null && !isRepository
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
        <AppIcon name="chevron" /><span>复现与验收执行</span
        ><span class="mono">{{ draft.testCommand || (effectiveLanguage === "python" ? "pytest 默认" : "需要配置") }}</span>
      </summary>
      <div class="field">
        <label for="reproduce-command">固定复现命令{{ effectiveLanguage === "python" ? "（可选）" : "" }}</label>
        <input id="reproduce-command" v-model="draft.reproduceCommand" class="mono"
          placeholder="例如 npm test -- --runInBand；支持 {source_root}" />
        <p class="input-hint">Python 可由模型生成复现脚本；其他语言需要固定命令。命令不会经过 shell 执行。</p>
      </div>
      <div class="field">
        <label for="test-command">固定测试命令{{ effectiveLanguage === "python" ? "（留空使用 pytest）" : "（必填）" }}</label>
        <input id="test-command" v-model="draft.testCommand" class="mono"
          placeholder="例如 npm test -- {targets} 或 go test ./..." />
        <p class="input-hint">Docker 运行环境由服务端配置；语言运行时和项目依赖必须已安装在对应镜像中。</p>
      </div>
      <div v-if="![ 'python', 'javascript', 'typescript' ].includes(effectiveLanguage)" class="field">
        <label for="regression-command">独立回归命令</label>
        <input id="regression-command" v-model="draft.regressionCommand" class="mono"
          placeholder="包含 {test_file} 与 {report_file}，输出单测试 JUnit XML" />
        <p class="input-hint">用于验证专用用例在旧实现失败、在补丁通过；JavaScript/TypeScript 默认使用 node:test。</p>
      </div>
      <div class="field">
        <label for="test-target">测试目标</label
        ><input
          id="test-target"
          v-model="draft.testTarget"
          class="mono"
          placeholder="例如 tests/；留空使用 ."
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
