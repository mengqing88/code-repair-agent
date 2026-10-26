<script setup lang="ts">
// name：图标标识；SVG 图标仅用于按钮和导航，不承载业务数据。
defineProps<{
  name:
    | "workspace"
    | "plus"
    | "arrow"
    | "file"
    | "check"
    | "close"
    | "history"
    | "branch"
    | "code"
    | "upload"
    | "chevron";
}>();

const paths = {
  workspace: ["M3 3h18v18H3z", "M3 9h18", "M9 9v12"],
  plus: ["M12 5v14", "M5 12h14"],
  arrow: ["M5 12h14", "m13 6 6 6-6 6"],
  file: ["M14 2H5v20h14V7z", "M14 2v6h5", "M8 13h8", "M8 17h6"],
  check: ["m5 12 4 4L19 6"],
  close: ["m6 6 12 12", "M18 6 6 18"],
  history: ["M3 11a9 9 0 1 1 2 7", "M3 4v7h7", "M12 7v5l3 2"],
  branch: [
    "M6 3v12a4 4 0 0 0 4 4h8",
    "M18 8v11",
    "M3 3h6",
    "M15 8h6",
    "M15 19h6",
  ],
  code: ["m8 6-6 6 6 6", "m16 6 6 6-6 6", "m14 3-4 18"],
  upload: ["M12 16V3", "m7 8 5-5 5 5", "M4 16v5h16v-5"],
  chevron: ["m9 5 7 7-7 7"],
};
</script>

<template>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(path, index) in paths[name]" :key="index" :d="path" />
  </svg>
</template>
