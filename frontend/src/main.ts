import { createApp } from "vue";
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist-mono/400.css";
import App from "./App.vue";
import "./style.css";

// createApp() 创建 Vue 应用；mount() 将应用挂载到 index.html 的 #app 容器。
createApp(App).mount("#app");
