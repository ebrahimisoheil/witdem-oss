import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/witdem/dashboard/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.indexOf("/node_modules/") === -1) return undefined;
          if (id.indexOf("/node_modules/zrender/") !== -1) return "zrender";
          if (id.indexOf("/node_modules/echarts-for-react/") !== -1) return "echarts-react";
          if (id.indexOf("/node_modules/echarts/") !== -1) return "echarts";
          if (id.indexOf("/node_modules/@tanstack/") !== -1) return "tanstack";
          if (id.indexOf("/node_modules/@lemonsqueezy/") !== -1) return "ui";
          if (
            id.indexOf("/node_modules/react/") !== -1
            || id.indexOf("/node_modules/react-dom/") !== -1
            || id.indexOf("/node_modules/scheduler/") !== -1
          ) return "react";
          return "vendor";
        },
      },
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8501", "/health": "http://127.0.0.1:8501" } },
});
