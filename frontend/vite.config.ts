import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Vue 插件负责把 .vue 单文件组件编译成浏览器可运行的模块。
export default defineConfig({
  plugins: [vue()],
  server: { port: 5173, strictPort: true },
});
